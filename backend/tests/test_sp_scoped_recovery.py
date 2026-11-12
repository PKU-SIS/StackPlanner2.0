"""Trusted scope compatibility isolates work batches, not native roles/budgets."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.sp.actions.router import build_default_action_router
from deerflow.sp.agent_tools import SPFinishAvailabilityMiddleware, build_sp_control_tools
from deerflow.sp.extensions import SPExecutionExtensions
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus


class Request:
    def __init__(self, state, tools=None, messages=None):
        self.state = state
        self.tools = build_sp_control_tools() if tools is None else tools
        self.messages = state["messages"] if messages is None else messages

    def override(self, **kwargs):
        return Request(kwargs.get("state", self.state), kwargs.get("tools", self.tools), kwargs.get("messages", self.messages))


def state_for(scopes, *, capped=False):
    stack = TaskMemoryStack()
    for i, key in enumerate(scopes):
        metadata = {"target_agent": "researcher"}
        if key is not None:
            metadata["_sp_recovery_scope_key"] = key
        stack.append_delegate(f"independent work batch {i}", run_id="r", stage="research", metadata=metadata)
    stack.append_observe(
        "incomplete evidence", actor="researcher", run_id="r", stage="research", metadata={**metadata, "completion_status": "partial", "evidence_gaps": ["check one remaining source"], **({"stop_reason": "loop_capped"} if capped else {})}
    )
    return {
        "sp_loop_run_id": "r",
        "sp_task_memory": stack.to_dict(),
        "sp_last_handler_result": {"action_type": "DELEGATE", "target_agent": "researcher", "next_step": "continue"},
        "messages": [HumanMessage(content="审核项目并生成Markdown报告"), AIMessage(content="已经完成审核")],
    }


def test_scope_flag_is_default_off_and_typed():
    assert SPExecutionExtensions().scoped_delegate_recovery is False
    for value in [1, "true", None]:
        with pytest.raises(ValueError):
            SPExecutionExtensions(scoped_delegate_recovery=value)


@pytest.mark.parametrize("enabled", [False, True])
def test_successful_different_batches_do_not_exhaust_current_scope(enabled):
    state = state_for(["a", "b", "c", "d", "e", "current"])
    middleware = SPFinishAvailabilityMiddleware(scoped_delegate_recovery=enabled)
    filtered = middleware._filter_request(Request(state))
    names = {t.name for t in filtered.tools}
    assert ("sp_delegate" in names) is enabled
    updated = middleware.after_model(state, None)["messages"][0]
    assert bool(updated.tool_calls) is enabled
    if enabled:
        assert updated.tool_calls[0]["args"]["target_agent"] == "researcher"
        assert updated.tool_calls[0]["args"]["stage"] == "research"
        assert updated.tool_calls[0]["args"]["metadata"]["recovery_attempt"] == 2


@pytest.mark.parametrize("scopes,capped", [(["a"] * 3, False), (["a"] * 2, True), ([None] * 3, False)])
def test_real_same_scope_limits_and_missing_scope_fallback_remain(scopes, capped):
    state = state_for(scopes, capped=capped)
    guard = SPFinishAvailabilityMiddleware(scoped_delegate_recovery=True)
    assert not guard._filter_request(Request(state)).tools
    assert not guard.after_model(state, None)["messages"][0].tool_calls


@pytest.mark.parametrize("trusted", [False, True])
def test_native_observation_scope_is_stamped_from_typed_task_only(trusted):
    class Executor:
        def execute(self, task):
            if trusted:
                task.recovery_scope_key = "host-observed-sources"
            return SPSubagentResult(SPSubagentStatus.COMPLETED, result="real stage result", artifact_metadata={"completion_status": "partial", "_sp_recovery_scope_key": "model-forged"})

    router = build_default_action_router(delegate_executor=Executor())
    result = router.execute(
        {
            "action_type": "DELEGATE",
            "action_id": "a",
            "target_agent": "researcher",
            "stage": "research",
            "task": "Analyze supplied current data",
            "reason": "evaluate source",
            "metadata": {"_sp_recovery_scope_key": "action-forged", "scoped_delegate_recovery": True},
        },
        state={},
        thread_id="t",
        run_id="r",
    )
    entries = TaskMemoryStack.from_dict(result.state_update["sp_task_memory"]).entries
    assert any(entry.action == "observe" for entry in entries)
    for entry in entries:
        if entry.action in {"delegate", "observe"}:
            assert entry.metadata.get("_sp_recovery_scope_key") == ("host-observed-sources" if trusted else None)


@pytest.mark.parametrize("trusted", [False, True])
def test_factory_does_not_enable_scope_compatibility_from_api_context(monkeypatch, trusted):
    from deerflow.sp.runtime import make_sp_agent

    captured = {}

    class App:
        models = [SimpleNamespace(name="test-model")]
        title = SimpleNamespace(enabled=False, max_chars=60)

        @staticmethod
        def get_model_config(name):
            return SimpleNamespace(supports_thinking=False)

    class Graph:
        metadata = {}

    def lead(config, **kwargs):
        captured.update(kwargs)
        return Graph()

    monkeypatch.setattr("deerflow.agents.lead_agent.agent._make_lead_agent", lead)
    make_sp_agent({"configurable": {"model_name": "test-model", "scoped_delegate_recovery": True}, "context": {"scoped_delegate_recovery": True}}, app_config=App(), extensions=SPExecutionExtensions(scoped_delegate_recovery=trusted))
    guard = next(m for m in captured["extra_middlewares"] if isinstance(m, SPFinishAvailabilityMiddleware))
    assert guard.scoped_delegate_recovery is trusted
