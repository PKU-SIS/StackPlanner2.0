"""Exhausted evidence recovery retains Central decision tools, default off."""

from types import SimpleNamespace

import pytest
from test_sp_scoped_recovery import Request, state_for

from deerflow.sp.agent_tools import SPFinishAvailabilityMiddleware
from deerflow.sp.extensions import SPExecutionExtensions


def test_limited_report_extension_is_trusted_and_default_off():
    assert SPExecutionExtensions().allow_limited_report is False
    with pytest.raises(ValueError):
        SPExecutionExtensions(allow_limited_report="true")


def test_exhausted_research_keeps_central_choices_without_calling_reporter():
    state = state_for(["same"] * 3)
    guard = SPFinishAvailabilityMiddleware(scoped_delegate_recovery=True, allow_limited_report=True)
    got = guard._filter_request(Request(state))
    names = {x.name for x in got.tools}
    assert {"sp_delegate", "sp_think", "sp_ask_human", "sp_replan"} <= names
    assert "sp_finish" not in names
    assert any("局限" in str(m.content) for m in got.messages)
    assert guard.after_model(state, None) is None
    # Actual Reporter choice is accepted; the same failed evidence work is not.
    req = SimpleNamespace(state=state, tool_call={"name": "sp_delegate", "id": "x", "args": {"target_agent": "reporter"}})
    assert guard._exhausted_tool_error(req) is None
    req.tool_call["args"]["target_agent"] = "researcher"
    assert guard._exhausted_tool_error(req) is not None
    assert not SPFinishAvailabilityMiddleware(scoped_delegate_recovery=True)._filter_request(Request(state)).tools


def test_unknown_scope_counts_since_last_success_instead_of_entire_history():
    from deerflow.sp.memory import TaskMemoryStack

    state = state_for([None] * 4)
    stack = TaskMemoryStack.from_dict(state["sp_task_memory"])
    # Add one successful completed task, then a new unsuccessful task.
    stack.append_observe("done", actor="researcher", run_id="r", stage="research", metadata={"target_agent": "researcher", "completion_status": "complete"})
    stack.append_delegate("new failed work", run_id="r", stage="research", metadata={"target_agent": "researcher"})
    stack.append_observe("missing", actor="researcher", run_id="r", stage="research", metadata={"target_agent": "researcher", "completion_status": "partial"})
    state["sp_task_memory"] = stack.to_dict()
    obs = stack.entries[-1]
    assert SPFinishAvailabilityMiddleware._recovery_attempt_count(state, obs, scoped_delegate_recovery=True, failure_chain_fallback=True) == 1


def test_actual_tool_wrapper_allows_reporter_and_rejects_exhausted_research():
    from langchain_core.messages import ToolMessage

    state = state_for(["same"] * 3)
    guard = SPFinishAvailabilityMiddleware(scoped_delegate_recovery=True, allow_limited_report=True)
    calls = []

    def handler(request):
        calls.append(request.tool_call["args"]["target_agent"])
        return ToolMessage(content="native delegate executed", tool_call_id="x")

    request = SimpleNamespace(state=state, tool_call={"name": "sp_delegate", "id": "x", "args": {"target_agent": "reporter"}})
    assert guard.wrap_tool_call(request, handler).content == "native delegate executed"
    request.tool_call["args"]["target_agent"] = "researcher"
    assert guard.wrap_tool_call(request, handler).status == "error"
    assert calls == ["reporter"]
