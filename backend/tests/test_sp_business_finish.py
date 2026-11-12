"""Trusted finish acceptance keeps native routing and recovery."""

from types import SimpleNamespace

from deerflow.sp.actions.router import build_default_action_router
from deerflow.sp.extensions import SPExecutionExtensions


def test_default_has_no_business_guard():
    assert SPExecutionExtensions().finish_validator is None
    assert SPExecutionExtensions().observe_result is None


def test_native_router_business_guard_is_recoverable():
    router = build_default_action_router(finish_validator=lambda action, context: "Unreviewed rows: r0, r1")
    result = router.execute({"action_type": "FINISH", "action_id": "f", "reason": "done", "task": "complete current task", "metadata": {"summary": "done"}}, state={}, thread_id="t", run_id="r")
    assert result.next_step == "error_recoverable"
    assert "Unreviewed" in result.error
    assert result.state_update.get("sp_current_stage") != "finished"


def test_control_middleware_uses_trusted_guard_without_ending_graph():
    import json

    from deerflow.sp.agent_tools import SPControlActionMiddleware

    m = SPControlActionMiddleware(finish_validator=lambda action, context: "Missing judgement records")
    request = SimpleNamespace(tool_call={"name": "sp_finish", "id": "finish-call", "args": {"summary": "done"}}, state={}, runtime=SimpleNamespace(context={"thread_id": "t", "run_id": "r"}))
    result = m.wrap_tool_call(request, lambda request: None)
    message = result.update["messages"][0]
    assert json.loads(message.content)["next_step"] == "error_recoverable"
    assert "Missing judgement" in message.content
    assert result.update.get("sp_current_stage") != "finished"
