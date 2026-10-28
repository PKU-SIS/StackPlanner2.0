"""W-layer: production make_sp_agent composition with a scripted fake model."""

from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _agent_e2e_helpers import FakeToolCallingModel

from deerflow.config.app_config import AppConfig
from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.handlers.delegate import DelegateHandler
from deerflow.sp.actions.router import ActionRouter
from deerflow.sp.actions.schema import ActionType, ActionValidationError, SPAction
from deerflow.sp.agent_tools import SPControlActionMiddleware, build_control_payload
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.report import hooks
from deerflow.sp.report.hooks import report_delegate_dispatch_error, report_delegate_is_serial
from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus

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


def _payload_for(state, args, call_id):
    return build_control_payload("sp_delegate", args, tool_call_id=call_id, state=state)


def _serial_gate(state, call_id, *, run_id=None):
    latest = next((message for message in reversed(state.get("messages") or []) if isinstance(message, AIMessage)), None)
    args = {}
    if latest is not None:
        for call in latest.tool_calls or []:
            if call.get("id") == call_id:
                args = dict(call.get("args") or {})
                break
    return report_delegate_is_serial(
        state,
        call_id,
        payload=_payload_for(state, args, call_id),
        payload_builder=lambda raw, cid: _payload_for(state, raw, cid),
        run_id=run_id,
    )


def _active_report_state() -> dict:
    thread_id = f"report-w-state-{uuid4().hex[:8]}"
    first = _invoke(_model([_start_call(), AIMessage(content="started")]), thread_id=thread_id)
    outputs = Path(first["thread_data"]["outputs_path"])
    return {"thread_data": {"outputs_path": str(outputs)}, "messages": []}


def _active_report_with_gaps(count: int = 2) -> tuple[dict, list[str]]:
    from deerflow.sp.report.ledger import utc_now_iso

    state = _active_report_state()
    ledger = ReportLedger.load_for_context(hooks.report_context(state))
    gap_ids: list[str] = []
    for index in range(count):
        gap = ledger.register_gap(
            mode="new",
            question=f"research question {index + 1}",
            constraints={},
            why_insufficient="not researched yet",
            stop_condition="a saved locatable source answers the question",
            requirement_refs=[{"input_id": "u1", "quote": "test report"}],
            node_ids=[],
            materials_checked=[],
        )
        gap_ids.append(str(gap["gap_id"]))
    ledger.payload["tree"] = {"version": 1, "accepted_at": utc_now_iso(), "path": "", "virtual_path": ""}
    ledger.save()
    return state, gap_ids


def _gate(state, call_id, args, run_id=None):
    payload = _payload_for(state, args, call_id)
    return report_delegate_dispatch_error(
        state,
        call_id,
        payload=payload,
        payload_builder=lambda raw, cid: _payload_for(state, raw, cid),
        run_id=run_id,
    )


class _RecordingExecutor:
    def __init__(self):
        self.tasks = []

    def execute(self, task):
        self.tasks.append(task)
        return SPSubagentResult(
            status=SPSubagentStatus.COMPLETED,
            result="observation",
            artifact_content="observation",
            artifact_type="research_observation",
            artifact_metadata={"completion_status": "complete", "evidence_gaps": []},
        )


class _BarrierExecutor:
    """Execute two independent researchers concurrently, proving real overlap."""

    def __init__(self):
        self.started = []
        self.barrier = threading.Barrier(2, timeout=10)

    def execute(self, task):
        gap_ids = [str(gap_id) for gap_id in task.metadata.get("report_gap_ids") or []]
        self.started.append((task.action_id, gap_ids))
        # This call only returns when the other independent research has entered
        # execute() too. A serial host times out here.
        self.barrier.wait()
        return SPSubagentResult(
            status=SPSubagentStatus.COMPLETED,
            result="observation",
            artifact_content="observation",
            artifact_type="research_observation",
            artifact_metadata={
                "completion_status": "complete",
                "gap_attempts": [
                    {"gap_id": gap_id, "execution": "searched", "material_ids": [], "note": "test observation"}
                    for gap_id in gap_ids
                ],
            },
        )


def _invoke_with_executor(model, *, thread_id, executor, monkeypatch):
    import deerflow.sp.runtime as sp_runtime

    monkeypatch.setattr(sp_runtime.DR2SPExecutorProvider, "__call__", lambda self, state, runtime: executor)
    return _invoke(model, thread_id=thread_id)


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
    assert _serial_gate(state, "call-a") is True
    assert _serial_gate(state, "call-b") is False
    assert _serial_gate({**state, "messages": [HumanMessage(content="x")]}, "call-b") is True


def test_report_delegate_serial_gate_uses_inferred_role_for_omitted_target():
    from deerflow.sp.report.hooks import report_context

    thread_id = f"report-w-inferred-serial-{uuid4().hex[:8]}"
    first = _invoke(_model([_start_call(), AIMessage(content="started")]), thread_id=thread_id)
    outputs = Path(first["thread_data"]["outputs_path"])
    state = {
        "thread_data": {"outputs_path": str(outputs)},
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "sp_delegate", "args": {"task": "research g1", "stage": "research"}, "id": "call-a", "type": "tool_call"},
                    {"name": "sp_delegate", "args": {"task": "research g2", "stage": "research"}, "id": "call-b", "type": "tool_call"},
                ],
            )
        ],
    }
    assert report_context(state).is_active
    assert _serial_gate(state, "call-a") is True
    assert _serial_gate(state, "call-b") is False


def test_report_delegate_serial_gate_mixes_explicit_and_inferred_roles():
    from deerflow.sp.report.hooks import report_context

    thread_id = f"report-w-mixed-serial-{uuid4().hex[:8]}"
    first = _invoke(_model([_start_call(), AIMessage(content="started")]), thread_id=thread_id)
    outputs = Path(first["thread_data"]["outputs_path"])
    state = {
        "thread_data": {"outputs_path": str(outputs)},
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "sp_delegate", "args": {"target_agent": "outline", "task": "plan"}, "id": "call-a", "type": "tool_call"},
                    {"name": "sp_delegate", "args": {"task": "research g1", "stage": "research"}, "id": "call-b", "type": "tool_call"},
                ],
            )
        ],
    }
    assert report_context(state).is_active
    assert _serial_gate(state, "call-a") is True
    assert _serial_gate(state, "call-b") is False


def test_serial_gate_uses_final_role_after_router_normalization():
    state = _active_report_state()
    args = {"task": "读取 https://api.example.com/data 的官方接口并整理结果", "stage": "implementation"}
    state = {
        **state,
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "sp_delegate", "args": dict(args), "id": "call-a", "type": "tool_call"},
                    {"name": "sp_delegate", "args": dict(args), "id": "call-b", "type": "tool_call"},
                ],
            )
        ],
    }

    assert _serial_gate(state, "call-a") is True
    assert _serial_gate(state, "call-b") is False
    for call_id in ("call-a", "call-b"):
        payload = _payload_for(state, args, call_id)
        assert payload["action_id"] == f"spact_{call_id}"
        assert payload["reason"]
        assert ActionRouter.effective_delegate_target(payload, state, run_id=None) == "researcher"

    filled = _payload_for(state, {"target_agent": "researcher", "stage": "research"}, "call-filled")
    assert filled["task"]


def test_parallel_independent_researchers_allowed_and_same_gap_blocked():
    state, gaps = _active_report_with_gaps(2)

    def researcher_args(gap_ids):
        return {
            "target_agent": "researcher",
            "task": "research the assigned gap",
            "stage": "research",
            "metadata": {"report_gap_ids": list(gap_ids)},
        }

    state["messages"] = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "sp_delegate", "args": researcher_args([gaps[0]]), "id": "call-a", "type": "tool_call"},
                {"name": "sp_delegate", "args": researcher_args([gaps[1]]), "id": "call-b", "type": "tool_call"},
            ],
        )
    ]
    assert _gate(state, "call-a", researcher_args([gaps[0]])) is None
    assert _gate(state, "call-b", researcher_args([gaps[1]])) is None

    state["messages"] = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "sp_delegate", "args": researcher_args([gaps[0]]), "id": "call-a", "type": "tool_call"},
                {"name": "sp_delegate", "args": researcher_args([gaps[0]]), "id": "call-b", "type": "tool_call"},
            ],
        )
    ]
    assert _gate(state, "call-a", researcher_args([gaps[0]])) is None
    duplicate_error = _gate(state, "call-b", researcher_args([gaps[0]]))
    assert duplicate_error is not None and "already in this batch" in duplicate_error


def test_outline_waits_for_all_selected_gaps_and_blocks_in_flight_research():
    from deerflow.sp.report.ledger import utc_now_iso

    state, gaps = _active_report_with_gaps(2)
    outline_args = {"target_agent": "outline", "task": "judge the returned results", "stage": "revision", "metadata": {"report_task": "revise"}}
    state["messages"] = [AIMessage(content="", tool_calls=[{"name": "sp_delegate", "args": dict(outline_args), "id": "outline-a", "type": "tool_call"}])]

    blocked = _gate(state, "outline-a", outline_args)
    assert blocked is not None and "have not been attempted" in blocked

    ledger = ReportLedger.load_for_context(hooks.report_context(state))
    ledger.begin_research_action("a-research", [gaps[0]])
    ledger.save()
    in_flight = _gate(state, "outline-a", outline_args)
    assert in_flight is not None and "still in flight" in in_flight

    # Return both results (one success-shaped, one technical failure shaped)
    # without requiring all gaps to be resolved. Outline may then judge them.
    ledger = ReportLedger.load_for_context(hooks.report_context(state))
    ledger.begin_research_action("a-research-2", [gaps[1]])
    now = utc_now_iso()
    for gap_id, action_id in ((gaps[0], "a-research"), (gaps[1], "a-research-2")):
        gap = ledger.require_gap(gap_id)
        gap["status"] = "awaiting_outline"
        gap["updated_at"] = now
        gap.setdefault("history", []).append(
            {"at": now, "event": "research_result", "action_id": action_id, "execution": "searched", "rounds_after": 1, "material_ids": [], "note": "test result"}
        )
        ledger.payload["actions"][action_id]["status"] = "completed"
    ledger.save()

    assert _gate(state, "outline-a", outline_args) is None


def test_same_second_old_result_does_not_count_for_new_tree():
    from deerflow.sp.report.ledger import utc_now_iso

    state, gaps = _active_report_with_gaps(1)
    ledger = ReportLedger.load_for_context(hooks.report_context(state))
    ledger.begin_research_action("a-old", [gaps[0]])
    timestamp = utc_now_iso()
    gap = ledger.require_gap(gaps[0])
    gap["status"] = "open"
    gap["updated_at"] = timestamp
    gap.setdefault("history", []).append(
        {"at": timestamp, "event": "research_result", "action_id": "a-old", "execution": "searched", "rounds_after": 1, "material_ids": [], "note": "old tree result"}
    )
    ledger.payload["actions"]["a-old"]["status"] = "completed"
    ledger.payload["tree"]["version"] = 2
    ledger.payload["tree"]["accepted_at"] = timestamp
    ledger.save()

    args = {
        "target_agent": "researcher",
        "task": "continue on the new tree",
        "stage": "research",
        "metadata": {"report_gap_ids": [gaps[0]]},
    }
    state["messages"] = [AIMessage(content="", tool_calls=[{"name": "sp_delegate", "args": dict(args), "id": "call-a", "type": "tool_call"}])]
    assert _gate(state, "call-a", args) is None


def test_outline_external_revision_is_not_blocked_by_unrelated_open_gap():
    state, gaps = _active_report_with_gaps(2)
    outline_args = {"target_agent": "outline", "task": "adjust the report scope", "stage": "revision", "metadata": {"report_task": "revise"}}
    state["messages"] = [AIMessage(content="", tool_calls=[{"name": "sp_delegate", "args": dict(outline_args), "id": "outline-a", "type": "tool_call"}])]

    blocked = _gate(state, "outline-a", outline_args)
    assert blocked is not None and "have not been attempted" in blocked

    state["sp_report_protocol_error"] = {"reason": "invalid_report_tree", "errors": "previous delivery incomplete"}
    assert _gate(state, "outline-a", outline_args) is None

    state.pop("sp_report_protocol_error")
    ledger = ReportLedger.load_for_context(hooks.report_context(state))
    ledger.add_user_input("请调整报告范围，不要求继续旧缺口。", source="user_message", source_message_id="m-new")
    ledger.save()
    updated = {**state, "sp_report_protocol_error": None}
    assert _gate(updated, "outline-a", outline_args) is None


def test_research_gap_already_attempted_is_not_redispatched_before_outline():
    from deerflow.sp.report.ledger import utc_now_iso

    state, gaps = _active_report_with_gaps(1)
    ledger = ReportLedger.load_for_context(hooks.report_context(state))
    ledger.begin_research_action("a-r1", [gaps[0]])
    now = utc_now_iso()
    gap = ledger.require_gap(gaps[0])
    gap["status"] = "awaiting_outline"
    gap["updated_at"] = now
    gap.setdefault("history", []).append(
        {"at": now, "event": "research_result", "action_id": "a-r1", "execution": "searched", "rounds_after": 1, "material_ids": [], "note": "test result"}
    )
    ledger.payload["actions"]["a-r1"]["status"] = "completed"
    ledger.save()

    args = {
        "target_agent": "researcher",
        "task": "retry the same gap",
        "stage": "research",
        "metadata": {"report_gap_ids": [gaps[0]]},
    }
    state["messages"] = [AIMessage(content="", tool_calls=[{"name": "sp_delegate", "args": dict(args), "id": "call-a", "type": "tool_call"}])]
    error = _gate(state, "call-a", args)
    assert error is not None and ("already returned" in error or "only open" in error)


def test_failed_research_counts_as_returned_without_auto_redispatch():
    from deerflow.sp.report.ledger import utc_now_iso

    state, gaps = _active_report_with_gaps(1)
    ledger = ReportLedger.load_for_context(hooks.report_context(state))
    ledger.begin_research_action("a-failed", [gaps[0]])
    ledger.fail_research_action("a-failed", "timeout")
    gap = ledger.require_gap(gaps[0])
    gap["updated_at"] = utc_now_iso()
    ledger.save()

    researcher_args = {
        "target_agent": "researcher",
        "task": "retry the failed gap",
        "stage": "research",
        "metadata": {"report_gap_ids": [gaps[0]]},
    }
    state["messages"] = [AIMessage(content="", tool_calls=[{"name": "sp_delegate", "args": dict(researcher_args), "id": "call-a", "type": "tool_call"}])]
    error = _gate(state, "call-a", researcher_args)
    assert error is not None and "already returned" in error

    outline_args = {"target_agent": "outline", "task": "judge the failed attempt", "stage": "revision", "metadata": {"report_task": "revise"}}
    state["messages"] = [AIMessage(content="", tool_calls=[{"name": "sp_delegate", "args": dict(outline_args), "id": "outline-a", "type": "tool_call"}])]
    assert _gate(state, "outline-a", outline_args) is None


def test_independent_research_actually_overlaps_and_merges_ledger_updates():
    state, gaps = _active_report_with_gaps(2)
    executor = _BarrierExecutor()
    handler = DelegateHandler(executor=executor)

    actions = [
        SPAction.create(
            ActionType.DELEGATE,
            action_id=f"a-research-{index}",
            reason="independent research",
            task=f"research gap {gap_id}",
            target_agent="researcher",
            stage="research",
            metadata={"report_gap_ids": [gap_id]},
        )
        for index, gap_id in enumerate(gaps)
    ]
    contexts = [
        HandlerContext(state=state, stack=TaskMemoryStack(), thread_id="t-overlap", run_id="run-overlap")
        for _ in gaps
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: handler.handle(pair[0], pair[1]), zip(actions, contexts)))

    assert all(result.next_step == "continue" for result in results), [result.error for result in results]
    assert len(executor.started) == 2
    assert {action_id for action_id, _ in executor.started} == {"a-research-0", "a-research-1"}

    ledger = ReportLedger.load_for_context(hooks.report_context(state))
    assert ledger.action("a-research-0")["status"] == "completed"
    assert ledger.action("a-research-1")["status"] == "completed"
    assert {gap["gap_id"]: gap["status"] for gap in ledger.gaps() if gap["gap_id"] in gaps} == {
        gaps[0]: "awaiting_outline",
        gaps[1]: "awaiting_outline",
    }


def test_serial_gate_keeps_invalid_call_out_of_serial_slot():
    state = _active_report_state()
    invalid = {"target_agent": "wizard", "task": "research g1"}
    valid = {"target_agent": "researcher", "task": "research g2"}
    state = {
        **state,
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "sp_delegate", "args": dict(invalid), "id": "call-invalid", "type": "tool_call"},
                    {"name": "sp_delegate", "args": dict(valid), "id": "call-valid", "type": "tool_call"},
                ],
            )
        ],
    }

    assert _serial_gate(state, "call-invalid") is True
    assert _serial_gate(state, "call-valid") is True
    payload = _payload_for(state, invalid, "call-invalid")
    with pytest.raises(ActionValidationError):
        ActionRouter.effective_delegate_target(payload, state, run_id=None)


def test_serial_gate_propagates_unknown_role_inference_failure(monkeypatch):
    state = _active_report_state()
    state = {
        **state,
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "sp_delegate", "args": {"target_agent": "researcher", "task": "research g1"}, "id": "call-a", "type": "tool_call"},
                ],
            )
        ],
    }

    def boom(*args, **kwargs):
        raise RuntimeError("role inference exploded")

    monkeypatch.setattr(ActionRouter, "effective_delegate_target", boom)
    with pytest.raises(RuntimeError):
        _serial_gate(state, "call-a")


def test_active_report_precheck_failure_returns_recoverable_tool_error(monkeypatch):
    state = _active_report_state()
    state = {
        **state,
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "sp_delegate", "args": {"target_agent": "researcher", "task": "research g1"}, "id": "call-a", "type": "tool_call"},
                ],
            )
        ],
    }
    request = SimpleNamespace(
        tool_call={"name": "sp_delegate", "args": {"target_agent": "researcher", "task": "research g1"}, "id": "call-a"},
        state=state,
        runtime=SimpleNamespace(context={"run_id": "run1", "thread_id": "t1"}),
    )

    import deerflow.sp.report as report_package

    def boom(*args, **kwargs):
        raise RuntimeError("precheck exploded")

    monkeypatch.setattr(report_package, "report_delegate_dispatch_error", boom)
    command = SPControlActionMiddleware()._execute_control_tool(request)

    payload = json.loads(command.update["messages"][0].content)
    assert payload["next_step"] == "error_recoverable"
    assert "precheck failed" in payload["error"]


def test_serial_gate_does_not_mutate_payload_ledger_or_task_memory():
    state = _active_report_state()
    state["sp_task_memory"] = {"entries": []}
    args = {"task": "research g1", "stage": "research"}
    state = {
        **state,
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "sp_delegate", "args": dict(args), "id": "call-a", "type": "tool_call"},
                    {"name": "sp_delegate", "args": dict(args), "id": "call-b", "type": "tool_call"},
                ],
            )
        ],
    }
    gate_payload = _payload_for(state, args, "call-a")
    payload_before = deepcopy(gate_payload)
    memory_before = deepcopy(state["sp_task_memory"])
    outputs = Path(state["thread_data"]["outputs_path"])
    files_before = {path.relative_to(outputs): path.read_bytes() for path in outputs.rglob("*") if path.is_file()}

    assert (
        report_delegate_is_serial(
            state,
            "call-a",
            payload=gate_payload,
            payload_builder=lambda raw, cid: _payload_for(state, raw, cid),
            run_id=None,
        )
        is True
    )

    files_after = {path.relative_to(outputs): path.read_bytes() for path in outputs.rglob("*") if path.is_file()}
    assert gate_payload == payload_before
    assert state["sp_task_memory"] == memory_before
    assert files_before == files_after


def test_graph_report_delegate_batch_executes_only_first_valid_report_task(monkeypatch):
    thread_id = f"report-w-graph-{uuid4().hex[:8]}"
    executor = _RecordingExecutor()
    args = {"task": "读取 https://api.example.com/data 的官方接口并整理结果", "stage": "implementation"}
    model = _model(
        [
            _start_call(),
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "sp_delegate", "args": dict(args), "id": "call-a", "type": "tool_call"},
                    {"name": "sp_delegate", "args": dict(args), "id": "call-b", "type": "tool_call"},
                ],
            ),
            AIMessage(content="done"),
        ]
    )

    result = _invoke_with_executor(model, thread_id=thread_id, executor=executor, monkeypatch=monkeypatch)

    assert len(executor.tasks) == 1
    assert executor.tasks[0].subagent_type == "researcher"
    serial_messages = [message for message in _tool_messages(result) if getattr(message, "tool_call_id", "") == "call-b"]
    assert serial_messages
    assert "missing report_gap_ids" in serial_messages[-1].content


def test_full_graph_parallel_independent_research_overlaps(monkeypatch):
    thread_id = f"report-w-graph-parallel-{uuid4().hex[:8]}"
    first = _invoke(_model([_start_call(), AIMessage(content="started")]), thread_id=thread_id)
    outputs = Path(first["thread_data"]["outputs_path"])
    ledger = ReportLedger.load_for_context(hooks.report_context({"thread_data": {"outputs_path": str(outputs)}}))
    gap_ids = []
    for index in range(2):
        gap = ledger.register_gap(
            mode="new",
            question=f"graph research question {index + 1}",
            constraints={},
            why_insufficient="not researched yet",
            stop_condition="a saved locatable source answers the question",
            requirement_refs=[{"input_id": "u1", "quote": "test report"}],
            node_ids=[],
            materials_checked=[],
        )
        gap_ids.append(str(gap["gap_id"]))
    ledger.payload["tree"] = {"version": 1, "path": "", "virtual_path": ""}
    ledger.save()

    executor = _BarrierExecutor()
    calls = [
        {
            "name": "sp_delegate",
            "args": {
                "target_agent": "researcher",
                "task": f"research graph gap {gap_id}",
                "stage": "research",
                "metadata": {"report_gap_ids": [gap_id]},
            },
            "id": f"call-graph-{index}",
            "type": "tool_call",
        }
        for index, gap_id in enumerate(gap_ids)
    ]
    model = _model(
        [
            AIMessage(content="", tool_calls=calls),
            AIMessage(content="done"),
        ]
    )
    result = _invoke_with_executor(model, thread_id=thread_id, executor=executor, monkeypatch=monkeypatch)

    assert result["messages"][-1].content == "done"
    assert len(executor.started) == 2
    ledger = ReportLedger.load_for_context(hooks.report_context({"thread_data": {"outputs_path": str(outputs)}}))
    assert all(ledger.action(action_id)["status"] == "completed" for action_id, _ in executor.started)


def test_graph_ordinary_double_delegation_still_runs_both(monkeypatch):
    thread_id = f"ordinary-w-graph-{uuid4().hex[:8]}"
    executor = _RecordingExecutor()
    args = {"target_agent": "researcher", "task": "research the same topic", "stage": "research"}
    model = _model(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "sp_delegate", "args": dict(args), "id": "call-a", "type": "tool_call"},
                    {"name": "sp_delegate", "args": dict(args), "id": "call-b", "type": "tool_call"},
                ],
            ),
            AIMessage(content="done"),
        ]
    )

    result = _invoke_with_executor(model, thread_id=thread_id, executor=executor, monkeypatch=monkeypatch)

    assert len(executor.tasks) == 2
    assert result["messages"][-1].content == "done"


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
