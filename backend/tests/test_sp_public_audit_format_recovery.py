"""Actual public-format feedback stays in the original audit, without host repair."""

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.sp.public_format_repair import PublicArtifactFormatRepairMiddleware


def _inline_contract():
    return {
        "delivery_mode": "inline",
        "output_parse_mode": "text",
        "output_schema": {"type": "string", "pattern": r"^Audit conclusion: (allowed|prohibited)\.$"},
        "source_requirements": {"audit_agent": "researcher", "files": [{"path": "/mnt/user-data/uploads/rules.txt", "total_lines": 1}], "document_ids": []},
    }


@pytest.mark.parametrize("damage", [None, "other_role", "other_auditor", "rag", "no_files", "no_schema"])
def test_inline_file_auditor_gets_bounded_same_instance_public_format_feedback_only_in_its_scope(damage):
    contract = _inline_contract()
    role = "researcher"
    if damage == "other_role":
        role = "reporter"
    elif damage == "other_auditor":
        contract["source_requirements"]["audit_agent"] = "reporter"
    elif damage == "rag":
        contract["source_requirements"]["document_ids"] = ["document"]
    elif damage == "no_files":
        contract["source_requirements"]["files"] = []
    elif damage == "no_schema":
        contract.pop("output_schema")
    state = {"messages": [AIMessage(content="Audit conclusion: prohibited", id="actual-final")]}
    middleware = PublicArtifactFormatRepairMiddleware(contract, auditor_role=role)
    update = middleware.after_model(state, None)
    if damage:
        assert update is None
    else:
        assert update["jump_to"] == "model"
        assert update["messages"][0].content == "Audit conclusion: prohibited"
        assert "schema mismatch" in update["messages"][0].additional_kwargs["sp_public_format_feedback"]["gaps"][0]
        state["messages"][-1] = update["messages"][0]
        state["messages"].append(AIMessage(content="Audit conclusion: prohibited."))
        assert middleware.after_model(state, None) is None


@pytest.mark.parametrize("asynchronous", [False, True])
def test_json_correction_binds_read_tools_and_requests_one_public_value_instead_of_nested_write_text(asynchronous):
    contract = {"delivery_mode": "artifact", "output_schema": {"type": "array", "items": {"type": "object"}}}
    middleware = PublicArtifactFormatRepairMiddleware(contract)
    original = '[{"text":"Exact quote\\",\\"explanation":"An issue"}]'
    update = middleware.after_model({"messages": [AIMessage(content=original, id="draft")]}, None)

    class Request:
        def __init__(self, *, messages, tools):
            self.messages = messages
            self.tools = tools

        def override(self, **changes):
            return Request(messages=changes.get("messages", self.messages), tools=changes.get("tools", self.tools))

    tools = [SimpleNamespace(name=name) for name in ("read_file", "grep", "write_file", "str_replace")]
    request = Request(messages=update["messages"], tools=tools)
    if asynchronous:

        async def handler(request):
            return request

        corrected = asyncio.run(middleware.awrap_model_call(request, handler))
    else:
        corrected = middleware.wrap_model_call(request, lambda request: request)
    assert [tool.name for tool in corrected.tools] == ["read_file", "grep"]
    assert request.tools == tools
    assert corrected.messages[0].content == original
    note = corrected.messages[-1].content
    assert "write_file/str_replace" in note
    assert "exactly once" in note
    assert "<tool_call>" in note
    assert "column" in note
    assert "complete" in note and "original quotations" in note


def test_inline_missing_final_recovery_retains_actual_schema_diagnostic():
    from deerflow.sp.subagents import SPSubagentTask
    from deerflow.sp.subagents.dr2_adapter import normalize_dr2_subagent_result

    text = "Audit conclusion: prohibited"
    task = SPSubagentTask(action_id="audit", subagent_type="researcher", task="Audit original sources.", description="Audit", context_refs={"task_contract": _inline_contract()})
    result = normalize_dr2_subagent_result(SimpleNamespace(status="completed", result=text, ai_messages=[{"type": "ai", "content": text}]), task=task)
    assert result.completion_status == "partial"
    assert any("schema mismatch" in gap for gap in result.artifact_metadata["evidence_gaps"])
    assert result.artifact_content is None


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("guard_stop", [False, True])
def test_real_inline_auditor_graph_corrects_original_final_without_new_delegation(asynchronous, guard_stop):
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from pydantic import PrivateAttr

    from deerflow.agents.middlewares.token_budget_middleware import TokenBudgetMiddleware
    from deerflow.config.token_budget_config import TokenBudgetConfig

    class Model(FakeMessagesListChatModel):
        _seen: list = PrivateAttr(default_factory=list)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self._seen.append(messages)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    first, final = "Audit conclusion: prohibited", "Audit conclusion: prohibited."
    usage = {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20}
    model = Model(responses=[AIMessage(content=first, usage_metadata=usage), AIMessage(content=final, usage_metadata=usage)])
    guard = TokenBudgetMiddleware(TokenBudgetConfig(enabled=True, max_tokens=10000, max_output_tokens=1 if guard_stop else None))
    graph = create_agent(model=model, tools=[], middleware=[PublicArtifactFormatRepairMiddleware(_inline_contract(), auditor_role="researcher"), guard])
    initial = {"messages": [HumanMessage(content="Original complete sources and original audit request")]}
    result = asyncio.run(graph.ainvoke(initial)) if asynchronous else graph.invoke(initial)
    assert len(model._seen) == (1 if guard_stop else 2)
    if guard_stop:
        assert "[TOKEN BUDGET EXCEEDED]" in result["messages"][-1].content
        return
    assert model._seen[1][-1].name == "sp_public_format_feedback"
    assert "schema mismatch" in model._seen[1][-1].content
    assert result["messages"][-1].content == final
    assert result["messages"][-2].content == first
    assert not any(isinstance(message, HumanMessage) and message.name == "sp_public_format_feedback" for message in result["messages"])


@pytest.mark.parametrize("asynchronous", [False, True])
def test_real_json_correction_preserves_quotations_and_uses_no_file_tool_or_text_envelope(asynchronous):
    import json

    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.tools import tool
    from pydantic import PrivateAttr

    class Model(FakeMessagesListChatModel):
        _bindings: list = PrivateAttr(default_factory=list)

        def bind_tools(self, tools, **kwargs):
            self._bindings.append([tool.name for tool in tools])
            return self

    @tool("read_file")
    def read_file(path: str) -> str:
        """Read original evidence."""
        raise AssertionError("No extra read is required to correct this actual draft")

    @tool("write_file")
    def write_file(path: str, content: str) -> str:
        """Write output through an actual native call."""
        raise AssertionError("Correction must return the actual public value directly")

    first = '<tool_call>{"name":"write_file","arguments":{"content":"[malformed"}</tool_call>'
    answer = [{"text": 'The original "quoted" sentence.', "explanation": "A supported issue."}]
    final = json.dumps(answer, ensure_ascii=False)
    model = Model(responses=[AIMessage(content=first), AIMessage(content=final)])
    middleware = PublicArtifactFormatRepairMiddleware({"delivery_mode": "artifact", "output_schema": {"type": "array", "items": {"type": "object"}}})
    graph = create_agent(model=model, tools=[read_file, write_file], middleware=[middleware])
    initial = {"messages": [HumanMessage(content='Original public source: The original "quoted" sentence.')]}
    result = asyncio.run(graph.ainvoke(initial)) if asynchronous else graph.invoke(initial)
    assert model._bindings == [["read_file", "write_file"], ["read_file"]]
    assert result["messages"][-2].content == first
    assert json.loads(result["messages"][-1].content) == answer
    assert not result["messages"][-1].tool_calls
