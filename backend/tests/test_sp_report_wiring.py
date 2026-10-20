"""W-layer: production make_sp_agent composition with a scripted fake model."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _agent_e2e_helpers import FakeToolCallingModel

from deerflow.config.app_config import AppConfig
from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.report import hooks
from deerflow.sp.report.hooks import report_delegate_is_serial
from deerflow.sp.report.ledger import ReportLedger

_CAPTURED_REQUESTS: list[list[BaseMessage]] = []
_CAPTURED_TOOLS: list[set[str]] = []


class CapturingModel(FakeToolCallingModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[override]
        _CAPTURED_TOOLS.append({str(getattr(tool, "name", "")) for tool in tools})
        return super().bind_tools(tools, tool_choice=tool_choice, **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[override]
        _CAPTURED_REQUESTS.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _app_config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
            "models": [{"name": "fake-model", "use": "pkg:Cls", "model": "fake-model", "supports_thinking": False}],
            "tools": [
                {"name": "read_file", "group": "file:read", "use": "deerflow.sandbox.tools:read_file_tool"},
                {"name": "ls", "group": "file:read", "use": "deerflow.sandbox.tools:ls_tool"},
                {"name": "grep", "group": "file:read", "use": "deerflow.sandbox.tools:grep_tool"},
                {"name": "glob", "group": "file:read", "use": "deerflow.sandbox.tools:glob_tool"},
                {"name": "web_search", "group": "web", "use": "deerflow.community.ddg_search.tools:web_search_tool"},
                {"name": "web_fetch", "group": "web", "use": "deerflow.community.direct_fetch.tools:web_fetch_tool"},
            ],
            "summarization": {"enabled": False},
            "title": {"enabled": False},
            "memory": {"enabled": False},
            "token_usage": {"enabled": False},
            "loop_detection": {"enabled": False},
            "tool_search": {"enabled": False},
        }
    )


def _model(responses: list[AIMessage]) -> CapturingModel:
    _CAPTURED_REQUESTS.clear()
    _CAPTURED_TOOLS.clear()
    return CapturingModel(responses=responses)


def _start_call() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "sp_replan",
                "args": {"task": "start the report", "reason": "user asks for a report", "metadata": {"report_action": "start"}},
                "id": "call-start",
                "type": "tool_call",
            }
        ],
    )


def _invoke(model: CapturingModel, *, thread_id: str):
    import deerflow.agents.lead_agent.agent as lead_agent_module

    original = lead_agent_module.create_chat_model
    lead_agent_module.create_chat_model = lambda **kwargs: model
    try:
        graph = __import__("deerflow.sp.runtime", fromlist=["make_sp_agent"]).make_sp_agent({"configurable": {"model_name": "fake-model"}}, app_config=_app_config())
        return graph.invoke(
            {
                "messages": [HumanMessage(content="请写一份关于示例公司 FY2024 偿付能力的详细研究报告。" * 200, id="m1")],
            },
            context={"thread_id": thread_id, "run_id": f"run-{thread_id}"},
        )
    finally:
        lead_agent_module.create_chat_model = original


def _tool_messages(result) -> list:
    return [message for message in result["messages"] if type(message).__name__ == "ToolMessage"]


def test_production_graph_starts_report_injects_control_and_rejects_p1_finish(monkeypatch):
    model = _model(
        [
            _start_call(),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "sp_finish",
                        "args": {"summary": "done"},
                        "id": "call-finish",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="report mode started; P1 cannot deliver"),
        ]
    )
    thread_id = f"report-w-{uuid4().hex[:8]}"
    result = _invoke(model, thread_id=thread_id)

    assert result["messages"][-1].content == "report mode started; P1 cannot deliver"
    tools = [message for message in _tool_messages(result)]
    replan_messages = [message for message in tools if message.name == "sp_replan"]
    assert replan_messages
    replan_payload = json.loads(replan_messages[-1].content)
    assert json.loads(replan_payload["result"])["started"] is True
    finish_messages = [message for message in tools if message.name == "sp_finish"]
    assert finish_messages and "P1" in finish_messages[-1].content
    assert int(result.get("sp_max_loop_iterations") or 0) == 60

    outputs = Path(result["thread_data"]["outputs_path"])
    pointer = json.loads((outputs / "report" / "current.json").read_text(encoding="utf-8"))
    assert pointer == {"report_id": "r1"}
    ledger = json.loads((outputs / "report" / "r1" / "ledger.json").read_text(encoding="utf-8"))
    assert ledger["status"] == "active"
    assert ledger["request"]["chars"] > 2400

    control_messages = [message for request in _CAPTURED_REQUESTS for message in request if getattr(message, "name", None) == "sp_report_context"]
    assert control_messages
    assert '"report_id": "r1"' in control_messages[-1].content
    assert '"next_action": "delegate_research"' in control_messages[-1].content or '"gaps": []' in control_messages[-1].content
    assert _CAPTURED_TOOLS and "sp_delegate" in _CAPTURED_TOOLS[0]


def test_production_graph_does_not_treat_broken_ledger_as_disabled():
    thread_id = f"report-w-broken-{uuid4().hex[:8]}"
    first = _invoke(_model([_start_call(), AIMessage(content="started")]), thread_id=thread_id)
    outputs = Path(first["thread_data"]["outputs_path"])
    ledger_path = outputs / "report" / "r1" / "ledger.json"
    ledger_path.write_text("{broken", encoding="utf-8")

    second = _invoke(
        _model(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "sp_finish",
                            "args": {"summary": "done"},
                            "id": "call-finish-2",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="blocked"),
            ]
        ),
        thread_id=thread_id,
    )
    finish_messages = [message for message in _tool_messages(second) if message.name == "sp_finish"]
    assert finish_messages
    assert "Report state is unavailable" in finish_messages[-1].content or "Report ledger" in finish_messages[-1].content


def test_report_delegate_serial_gate_uses_first_report_call_only():
    from deerflow.sp.report.hooks import report_context

    thread_id = f"report-w-serial-{uuid4().hex[:8]}"
    first = _invoke(_model([_start_call(), AIMessage(content="started")]), thread_id=thread_id)
    outputs = Path(first["thread_data"]["outputs_path"])
    state = {
        "thread_data": {"outputs_path": str(outputs)},
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "sp_delegate", "args": {"target_agent": "outline", "task": "plan"}, "id": "call-a", "type": "tool_call"},
                    {"name": "sp_delegate", "args": {"target_agent": "researcher", "task": "research"}, "id": "call-b", "type": "tool_call"},
                ],
            )
        ],
    }
    assert report_context(state).is_active
    assert report_delegate_is_serial(state, "call-a") is True
    assert report_delegate_is_serial(state, "call-b") is False
    assert report_delegate_is_serial({**state, "messages": [HumanMessage(content="x")]}, "call-b") is True


def test_control_middleware_injects_unapplied_answer_state_before_model(tmp_path):

    from deerflow.sp.report.context import SPReportContextMiddleware
    from deerflow.sp.report.hooks import record_human_feedback_input

    class _FakeRequest:
        def __init__(self, messages, state):
            self.messages = messages
            self.state = state

        def override(self, **kwargs):
            return _FakeRequest(kwargs.get("messages", self.messages), self.state)

    ctx = HandlerContext(
        state={"thread_data": {"outputs_path": str(tmp_path)}, "messages": [HumanMessage(content="写报告", id="m1")]},
        stack=TaskMemoryStack(),
        thread_id="t1",
        run_id="run1",
    )
    hooks.start_report(ctx, SPAction.create(ActionType.REPLAN, reason="start", task="start", metadata={"report_action": "start"}))
    recorded = record_human_feedback_input(
        {**ctx.state, "sp_pending_human_interaction": {"interaction_id": "sphitl_ctx", "metadata": {"report_gap_ids": []}}},
        "补充回答",
        pending={"interaction_id": "sphitl_ctx", "metadata": {"report_gap_ids": []}},
    )
    assert recorded is not None
    original = [HumanMessage(content="补充回答", id="answer-1")]
    request = _FakeRequest(original, ctx.state)
    augmented = SPReportContextMiddleware()._augment_request(request)
    context_messages = [message for message in augmented.messages if getattr(message, "name", None) == "sp_report_context"]
    assert len(context_messages) == 1
    assert '"report_id": "r1"' in context_messages[0].content
    assert recorded["input_id"] in context_messages[0].content
    assert len(request.messages) == 1


def test_non_report_roles_keep_their_config_and_tools():
    from types import SimpleNamespace

    from deerflow.sp.report.hooks import configure_report_subagent
    from deerflow.sp.subagents import SPSubagentTask

    config = object()
    tools = [SimpleNamespace(name="web_search")]
    task = SPSubagentTask(action_id="ordinary", subagent_type="researcher", task="ordinary research", description="ordinary")
    returned_config, returned_tools, role = configure_report_subagent(task, state={}, available_tools=tools, subagent_config=config)
    assert returned_config is config
    assert returned_tools is tools
    assert role is None
    assert [tool.name for tool in returned_tools] == ["web_search"]


def test_ask_human_and_replan_expose_optional_metadata():
    from deerflow.sp.agent_tools import sp_ask_human, sp_replan

    assert "metadata" in sp_ask_human.args
    assert "metadata" in sp_replan.args


def test_report_researcher_automatic_continuation_is_disabled():
    from types import SimpleNamespace

    from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus, SPSubagentTask
    from deerflow.sp.subagents.dr2_adapter import _research_result_needs_continuation

    task = SPSubagentTask(
        action_id="report-research",
        subagent_type="researcher",
        task="report research",
        description="report",
        metadata={"report_role": "researcher", "report_id": "r1"},
    )
    normalized = SPSubagentResult(
        status=SPSubagentStatus.COMPLETED,
        artifact_metadata={"completion_status": "partial", "evidence_gaps": ["more evidence"]},
    )
    raw = SimpleNamespace(
        ai_messages=[
            {"type": "ai", "tool_calls": [{"name": "web_fetch", "id": "c1", "args": {"url": "https://example.com"}}]},
            {"type": "tool", "tool_call_id": "c1", "name": "web_fetch", "content": "ok"},
        ]
    )
    assert _research_result_needs_continuation(raw, normalized, task=task) is False


def test_hitl_reply_and_generic_sync_share_one_input(tmp_path):
    from types import SimpleNamespace

    from langchain_core.messages import ToolMessage

    from deerflow.sp.agent_tools import SPThinkLabelMiddleware

    ctx = HandlerContext(
        state={"thread_data": {"outputs_path": str(tmp_path)}, "messages": [HumanMessage(content="写报告", id="m1")]},
        stack=TaskMemoryStack(),
        thread_id="t1",
        run_id="run1",
    )
    hooks.start_report(ctx, SPAction.create(ActionType.REPLAN, reason="start", task="start", metadata={"report_action": "start"}))
    interaction_id = "sphitl_dedupe"
    response = HumanMessage(
        content="Answer",
        id="answer-1",
        additional_kwargs={
            "human_input_response": {
                "version": 1,
                "kind": "human_input_response",
                "source": "stackplanner",
                "request_id": interaction_id,
                "response_kind": "option",
                "option_id": "option-1",
                "value": "Answer",
            }
        },
    )
    state = {
        **ctx.state,
        "messages": [
            HumanMessage(content="写报告", id="m1"),
            ToolMessage(
                content="question",
                tool_call_id="ask-tool",
                name="sp_ask_human",
                artifact={"human_input": {"request_id": interaction_id}},
            ),
            response,
        ],
        "sp_pending_human_interaction": {
            "interaction_id": interaction_id,
            "metadata": {"report_gap_ids": []},
        },
    }
    SPThinkLabelMiddleware().before_agent(state, SimpleNamespace(context={"thread_id": "t1", "run_id": "run2"}))
    hooks.sync_user_inputs(state)

    ledger = ReportLedger.load_for_context(hooks.report_context(state))
    hitl_inputs = [item for item in ledger.inputs() if item["source"] == "hitl_feedback"]
    assert len(hitl_inputs) == 1
    assert hitl_inputs[0]["interaction_id"] == interaction_id
    assert hitl_inputs[0]["source_message_id"] == "answer-1"


def test_hitl_persistence_failure_is_not_silently_swallowed(tmp_path, monkeypatch):
    from deerflow.sp.report.ledger import ReportLedger

    ctx = HandlerContext(
        state={"thread_data": {"outputs_path": str(tmp_path)}, "messages": [HumanMessage(content="写报告", id="m1")]},
        stack=TaskMemoryStack(),
        thread_id="t1",
        run_id="run1",
    )
    hooks.start_report(ctx, SPAction.create(ActionType.REPLAN, reason="start", task="start", metadata={"report_action": "start"}))

    def failing_save(self):
        raise OSError("ledger write failed")

    monkeypatch.setattr(ReportLedger, "save", failing_save)
    with pytest.raises(OSError, match="ledger write failed"):
        hooks.record_human_feedback_input(
            ctx.state,
            "Answer",
            pending={"interaction_id": "sphitl_fail", "metadata": {"report_gap_ids": []}},
        )


def test_report_bound_delegate_skips_legacy_keyword_retargeting(tmp_path):
    from deerflow.sp.agent_tools import _action_payload

    ctx = HandlerContext(
        state={"thread_data": {"outputs_path": str(tmp_path)}, "messages": [HumanMessage(content="写报告", id="m1")]},
        stack=TaskMemoryStack(),
        thread_id="t1",
        run_id="run1",
    )
    hooks.start_report(ctx, SPAction.create(ActionType.REPLAN, reason="start", task="start", metadata={"report_action": "start"}))
    args = {
        "target_agent": "researcher",
        "task": "Find company C's total assets in FY2024.",
        "metadata": {"report_gap_ids": ["g1"]},
    }
    bound = _action_payload("sp_delegate", args, tool_call_id="tc-bound", state=ctx.state)
    assert bound["target_agent"] == "researcher"
    assert bound["stage"] == "research"
    assert "routed_from_agent" not in bound["metadata"]

    ordinary = _action_payload(
        "sp_delegate",
        {"target_agent": "coder", "task": "write a report about the repository"},
        tool_call_id="tc-ordinary",
        state=None,
    )
    assert ordinary["target_agent"] == "reporter"


def test_runtime_tool_execution_classifier_distinguishes_saved_material():
    from deerflow.sp.runtime import _tool_execution_ok

    assert _tool_execution_ok("report_fetch_material", '{"saved": true, "material_id": "m1"}') is True
    assert _tool_execution_ok("report_fetch_material", '{"saved": false, "error": "fetch_failed"}') is False
    assert _tool_execution_ok("web_search", "two results") is True
    assert _tool_execution_ok("web_search", "Error: quota") is False
