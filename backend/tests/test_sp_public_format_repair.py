"""Public-format feedback retries the original model without manufacturing answers."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.sp.agent_tools import SPFinishAvailabilityMiddleware, SPThinkLabelMiddleware
from deerflow.sp.memory import TaskMemoryStack


def _inline_state(text):
    stack = TaskMemoryStack()
    stack.append_delegate("Audit", run_id="run", metadata={"target_agent": "researcher"})
    stack.append_observe("Audit complete", actor="researcher", run_id="run", metadata={"completion_status": "complete", "evidence_gaps": []})
    return {
        "sp_loop_run_id": "run",
        "sp_non_interactive": True,
        "sp_task_memory": stack.to_dict(),
        "sp_task_contract": {
            "delivery_mode": "inline",
            "output_parse_mode": "text",
            "output_schema": {"type": "string", "pattern": r"Answer: (?:False\.|True\. Illegal Operation: [A-Z]\. Problematic Team: [A-Z]\.)\s*$"},
            "source_requirements": {"audit_agent": "researcher", "files": []},
        },
        "messages": [HumanMessage(content="Audit original sources."), AIMessage(content=text, id="candidate")],
    }


def test_inline_format_failure_retries_same_central_and_preserves_original_candidate():
    state = _inline_state("Supported audit. Answer: False")
    update = SPFinishAvailabilityMiddleware._after_model_update(state, None)
    assert update.get("jump_to") == "model"
    assert update["messages"][0].content == state["messages"][-1].content
    assert not update["messages"][0].tool_calls
    assert update["messages"][0].additional_kwargs["sp_public_format_feedback"]["attempt"] == 1
    state["messages"][-1] = update["messages"][0]
    from deerflow.sp.public_format_repair import augment_public_format_request

    class Request:
        messages = state["messages"]

        def override(self, **kwargs):
            return SimpleNamespace(**kwargs)

    request = augment_public_format_request(Request())
    note = request.messages[-1]
    assert note.additional_kwargs["hide_from_ui"] is True
    assert "schema mismatch" in note.content
    assert "preserve" in note.content.lower()
    state["messages"].append(AIMessage(content="Supported audit. Answer: False.", id="corrected"))
    assert SPFinishAvailabilityMiddleware._after_model_update(state, None) is None


def test_inline_format_feedback_is_bounded_and_does_not_fall_into_file_reporter():
    state = _inline_state("Answer: False")
    for attempt in (1, 2):
        update = SPFinishAvailabilityMiddleware._after_model_update(state, None)
        assert update.get("jump_to") == "model"
        state["messages"][-1] = update["messages"][0]
        state["messages"].append(AIMessage(content="Answer: False", id=f"bad-{attempt}"))
    update = SPFinishAvailabilityMiddleware._after_model_update(state, None)
    assert not update["messages"][0].tool_calls
    assert update["sp_recovery_exhausted"]["attempts"] == 2
    assert update["messages"][0].additional_kwargs["stackplanner"]["status"] == "incomplete"


@pytest.mark.parametrize(
    "text",
    [
        '{"text":"Exact passage","explanation":"A conflict"}\n{"text":"Other passage","explanation":"Another conflict"}',
        json.dumps({"summary": "File written", "artifact_content": None, "artifact_metadata": {"completion_status": "complete", "created_paths": ["/mnt/user-data/outputs/answer.json"]}}),
    ],
)
def test_artifact_schema_failure_gets_same_instance_feedback_without_host_answer_repair(text):
    from deerflow.sp.public_format_repair import PublicArtifactFormatRepairMiddleware

    middleware = PublicArtifactFormatRepairMiddleware({"delivery_mode": "artifact", "output_schema": {"type": "array", "items": {"type": "object"}}})
    state = {"messages": [AIMessage(content=text, id="draft")]}
    update = middleware.after_model(state, None)
    assert update["jump_to"] == "model"
    assert update["messages"][0].content == text
    assert not update["messages"][0].tool_calls
    state["messages"] = [update["messages"][0], AIMessage(content='[{"text":"Exact passage","explanation":"A conflict"}]')]
    assert middleware.after_model(state, None) is None


@pytest.mark.parametrize("damage", ["partial", "blocked", "top_partial", "gaps", "provider_error", "length", "tool", "invalid_tool", "guard", "token_cap", "loop_cap", "safety", "tool_finish"])
def test_artifact_format_feedback_preserves_failures_and_tool_intent(damage):
    from deerflow.sp.public_format_repair import PublicArtifactFormatRepairMiddleware

    message = AIMessage(content="Malformed draft")
    if damage in {"partial", "blocked"}:
        message.content = json.dumps({"artifact_metadata": {"completion_status": damage, "evidence_gaps": ["Missing source"]}})
    elif damage == "top_partial":
        message.content = json.dumps({"completion_status": "partial"})
    elif damage == "gaps":
        message.content = json.dumps({"artifact_metadata": {"completion_status": "complete", "evidence_gaps": ["Missing source"]}})
    elif damage == "provider_error":
        message.additional_kwargs["error_type"] = "ProviderError"
    elif damage == "length":
        message.response_metadata["finish_reason"] = "length"
    elif damage == "tool":
        message.tool_calls = [{"name": "read_file", "args": {}, "id": "read"}]
    elif damage == "invalid_tool":
        message.invalid_tool_calls = [{"name": "read_file", "args": "bad", "id": "read", "error": "parse"}]
    elif damage == "token_cap":
        message.content += " [TOKEN BUDGET EXCEEDED]"
    elif damage == "loop_cap":
        message.content += " [FORCED STOP]"
    elif damage == "safety":
        message.additional_kwargs["safety_termination"] = {"detected": True}
    elif damage == "tool_finish":
        message.response_metadata["finish_reason"] = "tool_calls"
    else:
        message.additional_kwargs["stackplanner"] = {"status": "blocked"}
    middleware = PublicArtifactFormatRepairMiddleware({"delivery_mode": "artifact", "output_schema": {"type": "array"}})
    assert middleware.after_model({"messages": [message]}, None) is None


def test_artifact_middleware_does_not_apply_to_inline_contract():
    from deerflow.sp.public_format_repair import PublicArtifactFormatRepairMiddleware

    state = _inline_state("Answer: False")
    middleware = PublicArtifactFormatRepairMiddleware(state["sp_task_contract"])
    assert middleware.after_model(state, None) is None


@pytest.mark.parametrize("value", [{"completion_status": []}, {"artifact_metadata": {"completion_status": {}}}])
def test_malformed_envelope_fields_remain_format_errors_instead_of_crashing(value):
    from deerflow.sp.public_format_repair import PublicArtifactFormatRepairMiddleware

    middleware = PublicArtifactFormatRepairMiddleware({"delivery_mode": "artifact", "output_schema": {"type": "array"}})
    update = middleware.after_model({"messages": [AIMessage(content=json.dumps(value))]}, None)
    assert update["jump_to"] == "model"


def test_inline_missing_evidence_still_delegates_real_auditor():
    state = _inline_state("Answer: False")
    state["sp_task_memory"] = TaskMemoryStack().to_dict()
    update = SPFinishAvailabilityMiddleware._after_model_update(state, None)
    assert update["messages"][0].tool_calls[0]["args"]["target_agent"] == "researcher"


def test_inline_format_retry_never_hides_a_newer_native_partial_observation():
    state = _inline_state("Answer: False")
    stack = TaskMemoryStack.from_dict(state["sp_task_memory"])
    stack.append_delegate("Inspect the draft", run_id="run", metadata={"target_agent": "reporter"})
    stack.append_observe("Source verification incomplete", actor="reporter", run_id="run", metadata={"completion_status": "partial", "evidence_gaps": ["Unresolved evidence"]})
    state["sp_task_memory"] = stack.to_dict()
    update = SPFinishAvailabilityMiddleware._after_model_update(state, None)
    assert "jump_to" not in update
    assert update["messages"][0].tool_calls[0]["args"]["target_agent"] == "reporter"


def test_real_successful_file_receipt_keeps_native_file_acceptance():
    from deerflow.sp.public_format_repair import PublicArtifactFormatRepairMiddleware

    path = "/mnt/user-data/outputs/answer.json"
    messages = [
        AIMessage(content="", tool_calls=[{"name": "write_file", "id": "write", "args": {"path": path, "json_content": ["finding"]}}]),
        ToolMessage(content="File written successfully.", name="write_file", tool_call_id="write"),
        AIMessage(content=json.dumps({"artifact_content": None, "artifact_metadata": {"completion_status": "complete", "created_paths": [path]}})),
    ]
    middleware = PublicArtifactFormatRepairMiddleware({"delivery_mode": "artifact", "output_schema": {"type": "array"}})
    assert middleware.after_model({"messages": messages}, None) is None
    messages.pop(1)
    assert middleware.after_model({"messages": messages}, None)["jump_to"] == "model"


@pytest.mark.parametrize("kind", ["artifact", "central"])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_format_feedback_uses_real_native_graph_without_persisting_control_prompt(kind, asynchronous):
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from pydantic import PrivateAttr

    from deerflow.agents.thread_state import ThreadState
    from deerflow.sp.public_format_repair import PublicArtifactFormatRepairMiddleware

    class Model(FakeMessagesListChatModel):
        _seen: list = PrivateAttr(default_factory=list)

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self._seen.append(list(messages))
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    if kind == "central":
        state = _inline_state("unused")
        state["messages"].pop()
        first, final = "Audit. Answer: False", "Audit. Answer: False."
        middleware = SPFinishAvailabilityMiddleware()
    else:
        state = {"messages": [HumanMessage(content="Original complete public source and audit task")]}
        first, final = '{"text":"Exact passage","explanation":"Conflict"}', '[{"text":"Exact passage","explanation":"Conflict"}]'
        middleware = PublicArtifactFormatRepairMiddleware({"delivery_mode": "artifact", "output_schema": {"type": "array", "items": {"type": "object"}}})
    model = Model(responses=[AIMessage(content=first), AIMessage(content=final)])
    middlewares = [middleware, SPThinkLabelMiddleware()] if kind == "central" else [middleware]
    graph = create_agent(model=model, tools=[], middleware=middlewares, state_schema=ThreadState)
    result = asyncio.run(graph.ainvoke(state)) if asynchronous else graph.invoke(state)
    assert result["messages"][-1].content == final
    assert len(model._seen) == 2
    assert model._seen[1][-1].name == "sp_public_format_feedback"
    assert first in [m.content for m in model._seen[1]]
    assert not any(isinstance(m, HumanMessage) and m.name == "sp_public_format_feedback" for m in result["messages"])


@pytest.mark.parametrize("kind", ["artifact", "central"])
def test_native_guard_runs_before_format_jump_and_cannot_be_overridden(kind):
    from langchain.agents import create_agent
    from langchain.agents.middleware import AgentMiddleware
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from deerflow.agents.thread_state import ThreadState
    from deerflow.sp.public_format_repair import PublicArtifactFormatRepairMiddleware

    class Stop(AgentMiddleware):
        def after_model(self, state, runtime):
            latest = state["messages"][-1]
            return {"messages": [latest.model_copy(update={"content": latest.content + " [TOKEN BUDGET EXCEEDED]"})]}

    state = _inline_state("unused")
    state["messages"].pop()
    middleware = SPFinishAvailabilityMiddleware() if kind == "central" else PublicArtifactFormatRepairMiddleware({"delivery_mode": "artifact", "output_schema": {"type": "array"}})
    model = FakeMessagesListChatModel(responses=[AIMessage(content="Malformed original")])
    graph = create_agent(model=model, tools=[], middleware=[middleware, Stop()], state_schema=ThreadState)
    result = graph.invoke(state)
    assert len([message for message in result["messages"] if isinstance(message, AIMessage)]) == 1
    assert result["messages"][-1].content == "Malformed original [TOKEN BUDGET EXCEEDED]"
    assert "sp_public_format_feedback" not in result["messages"][-1].additional_kwargs
