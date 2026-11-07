"""Only complete originals in the current auditor input can close repeated reads."""

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.sp.source_prefetch import SourcePrefetchSettings, SourcePrefetchVisibilityMiddleware


class Request:
    def __init__(self, messages, tools=None, state=None):
        self.messages = messages
        self.tools = tools if tools is not None else [SimpleNamespace(name=name) for name in ("read_file", "grep", "other_tool")]
        self.state = state or {}

    def override(self, **changes):
        return Request(changes.get("messages", self.messages), changes.get("tools", self.tools), self.state)


def _input():
    files = [{"path": "/mnt/user-data/uploads/rules.txt", "total_lines": 2}, {"path": "/mnt/user-data/uploads/context.json", "total_lines": 1}]
    contract = {"delivery_mode": "inline", "output_parse_mode": "text", "output_schema": {"type": "string"}, "source_requirements": {"audit_agent": "researcher", "files": files, "document_ids": []}}
    messages = [HumanMessage(content="Complete the original source-grounded audit and its public final answer.")]
    for index, (file, body) in enumerate(zip(files, ["First original line\nSecond original line", '{"scenario":"original"}'], strict=True)):
        call_id = f"host-source-{index}"
        messages.extend(
            [
                AIMessage(
                    content="Actual host native source read",
                    tool_calls=[{"id": call_id, "name": "read_file", "args": {"path": file["path"], "start_line": 1, "end_line": file["total_lines"]}}],
                    additional_kwargs={"sp_host_source_prefetch": True},
                ),
                ToolMessage(content=body, name="read_file", tool_call_id=call_id, additional_kwargs={"sp_host_source_prefetch": True}),
            ]
        )
    captured = [m.model_dump() for m in messages[1:]]
    settings = SourcePrefetchSettings(tokenizer="PRIVATE_TOKENIZER", context_tokens=8192, response_tokens=1024)
    return contract, messages, captured, settings


@pytest.mark.parametrize("asynchronous", [False, True])
def test_current_complete_prefetch_closes_only_duplicate_read_tools_without_changing_sources(monkeypatch, asynchronous):
    contract, messages, captured, settings = _input()
    checked = []

    def count(messages, tools, settings, thinking_enabled=None):
        checked.append((messages, tools))
        return 100

    monkeypatch.setattr("deerflow.sp.source_prefetch.count_source_prompt_tokens", count)
    middleware = SourcePrefetchVisibilityMiddleware(captured, contract=contract, settings=settings, auditor_role="researcher")
    request = Request(messages)
    if asynchronous:

        async def handler(request):
            return request

        filtered = asyncio.run(middleware.awrap_model_call(request, handler))
    else:
        filtered = middleware.wrap_model_call(request, lambda request: request)
    assert [tool.name for tool in filtered.tools] == ["other_tool"]
    assert [tool.name for tool in request.tools] == ["read_file", "grep", "other_tool"]
    assert filtered.messages[:-1] == messages
    assert filtered.messages[-1].additional_kwargs["hide_from_ui"] is True
    assert "complete original audit" in filtered.messages[-1].content
    assert "partial" in filtered.messages[-1].content
    assert checked[0][0] == filtered.messages
    assert checked[0][1] == filtered.tools
    assert "PRIVATE_TOKENIZER" not in filtered.messages[-1].content


@pytest.mark.parametrize("damage", ["artifact", "rag", "wrong_auditor", "wrong_role", "no_files", "no_schema", "no_settings", "over_budget", "missing", "preview", "changed_args", "error", "parent_ledger", "stale_visible", "forged_pair"])
def test_closed_source_audit_requires_current_original_pairs_scope_and_token_budget(monkeypatch, damage):
    contract, messages, captured, settings = _input()
    role = "researcher"
    if damage == "artifact":
        contract["delivery_mode"] = "artifact"
    elif damage == "rag":
        contract["source_requirements"]["document_ids"] = ["scoped-document"]
    elif damage == "wrong_auditor":
        contract["source_requirements"]["audit_agent"] = "reporter"
    elif damage == "wrong_role":
        role = "reporter"
    elif damage == "no_files":
        contract["source_requirements"]["files"] = []
    elif damage == "no_schema":
        contract.pop("output_schema")
    elif damage == "no_settings":
        settings = None
    elif damage in {"missing", "parent_ledger", "stale_visible"}:
        messages = messages[:3]
        if damage == "stale_visible":
            for saved in captured:
                saved["additional_kwargs"]["sp_host_source_prefetch_visible"] = True
    elif damage == "preview":
        messages[-1] = messages[-1].model_copy(update={"content": "shortened summary"})
    elif damage == "changed_args":
        messages[-2] = messages[-2].model_copy(deep=True)
        messages[-2].tool_calls[0]["args"]["path"] = "/mnt/user-data/uploads/other.txt"
    elif damage == "error":
        messages[-1] = messages[-1].model_copy(update={"status": "error"})
    elif damage == "forged_pair":
        messages[-2] = messages[-2].model_copy(deep=True)
        messages[-2].tool_calls[0]["id"] = "another-pair"
        messages[-1] = messages[-1].model_copy(update={"tool_call_id": "another-pair"})
    monkeypatch.setattr("deerflow.sp.source_prefetch.count_source_prompt_tokens", lambda *args: 99999 if damage == "over_budget" else 100)
    request = Request(messages, state={"sp_source_access": {"reads": {file["path"]: [[1, file["total_lines"]]] for file in contract["source_requirements"]["files"]}}})
    middleware = SourcePrefetchVisibilityMiddleware(captured, contract=contract, settings=settings, auditor_role=role)
    filtered = middleware.wrap_model_call(request, lambda request: request)
    assert filtered is request


def test_current_source_proof_is_recomputed_after_a_summary_removes_an_original(monkeypatch):
    contract, messages, captured, settings = _input()
    monkeypatch.setattr("deerflow.sp.source_prefetch.count_source_prompt_tokens", lambda *args: 100)
    middleware = SourcePrefetchVisibilityMiddleware(captured, contract=contract, settings=settings, auditor_role="researcher")
    assert middleware.wrap_model_call(Request(messages), lambda request: request).tools[-1].name == "other_tool"
    summarized = Request([HumanMessage(content="A summary says all sources were read.")])
    assert middleware.wrap_model_call(summarized, lambda request: request) is summarized


def test_tokenizer_failure_retains_original_read_tools(monkeypatch):
    contract, messages, captured, settings = _input()

    def unavailable(*args):
        raise RuntimeError("Tokenizer unavailable")

    monkeypatch.setattr("deerflow.sp.source_prefetch.count_source_prompt_tokens", unavailable)
    middleware = SourcePrefetchVisibilityMiddleware(captured, contract=contract, settings=settings, auditor_role="researcher")
    request = Request(messages)
    assert middleware.wrap_model_call(request, lambda request: request) is request


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("guard_stop", [False, True])
def test_real_native_graph_preserves_original_source_and_budget_guard_without_any_new_read(monkeypatch, asynchronous, guard_stop):
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.tools import tool
    from pydantic import PrivateAttr

    from deerflow.agents.middlewares.token_budget_middleware import TokenBudgetMiddleware
    from deerflow.config.token_budget_config import TokenBudgetConfig

    class Model(FakeMessagesListChatModel):
        _seen: list = PrivateAttr(default_factory=list)

        def bind_tools(self, tools, **kwargs):
            assert not tools, "Complete current source proof must remove read tools from this model binding"
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self._seen.append(list(messages))
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    @tool("read_file")
    def read_file(path: str) -> str:
        """Read an original source file."""
        raise AssertionError("No duplicate native source read may execute")

    @tool("grep")
    def grep(pattern: str) -> str:
        """Search an original source file."""
        raise AssertionError("No duplicate source search may execute")

    contract, messages, captured, settings = _input()
    monkeypatch.setattr("deerflow.sp.source_prefetch.count_source_prompt_tokens", lambda *args: 100)
    middleware = SourcePrefetchVisibilityMiddleware(captured, contract=contract, settings=settings, auditor_role="researcher")
    guard = TokenBudgetMiddleware(TokenBudgetConfig(enabled=True, max_tokens=10000, max_output_tokens=1 if guard_stop else None))
    model = Model(responses=[AIMessage(content="Supported public answer", usage_metadata={"input_tokens": 10, "output_tokens": 10, "total_tokens": 20})])
    graph = create_agent(model=model, tools=[read_file, grep], middleware=[guard, middleware])
    config = {"context": {"run_id": "current-audit", "thread_id": "thread"}}
    result = asyncio.run(graph.ainvoke({"messages": messages}, **config)) if asynchronous else graph.invoke({"messages": messages}, **config)
    assert len(model._seen) == 1
    assert model._seen[0][-1].name == "sp_complete_visible_file_audit"
    assert all(any(current.content == original.content for current in model._seen[0]) for original in messages)
    assert not any(isinstance(message, HumanMessage) and message.name == "sp_complete_visible_file_audit" for message in result["messages"])
    assert result["messages"][-1].content.startswith("Supported public answer")
    assert ("[TOKEN BUDGET EXCEEDED]" in result["messages"][-1].content) is guard_stop
    assert guard.consume_stop_reason("current-audit") == ("token_capped" if guard_stop else None)
