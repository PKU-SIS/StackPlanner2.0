"""Trusted, run-local delegation extensions must not change native defaults."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from deerflow.sp.extensions import SPExecutionExtensions
from deerflow.sp.runtime import DR2SPExecutorProvider
from deerflow.sp.subagents import SPSubagentTask
from deerflow.subagents.config import SubagentConfig


@pytest.mark.parametrize("enabled", [False, True])
def test_extensions_reach_rendered_task_and_isolated_executor_only_when_enabled(monkeypatch, enabled):
    captured = {}
    original = SubagentConfig(name="sp-researcher", description="research", internal=True, skills=[])
    callback = object()
    middleware = object()
    tool = SimpleNamespace(name="local_search_tool")

    def prepare(task):
        task.context_refs["test_requirement"] = "full immutable rule"

    def configure(task, config):
        return replace(config, tools=["local_search_tool"], system_prompt=(config.system_prompt or "") + "\nscoped instruction")

    class Executor:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def execute(self, prompt):
            captured["prompt"] = prompt
            return SimpleNamespace(
                status="completed", result='{"summary":"done","artifact_content":"evidence","artifact_type":"research_observation","artifact_metadata":{"completion_status":"complete"}}', error=None, stop_reason=None, token_usage_records=[]
            )

    monkeypatch.setattr("deerflow.subagents.get_subagent_config", lambda *a, **kw: original)
    monkeypatch.setattr("deerflow.subagents.SubagentExecutor", Executor)
    extensions = SPExecutionExtensions(tools=(tool,), prepare_task=prepare, configure_subagent=configure, callbacks=(callback,), subagent_middlewares=lambda task: (middleware,)) if enabled else None
    provider = DR2SPExecutorProvider(app_config=SimpleNamespace(), parent_model="model", runnable_config={}, extensions=extensions)
    if not enabled:
        provider._tools = [tool]
    result = provider({}, SimpleNamespace(context={"thread_id": "t", "run_id": "r"}, stream_writer=None)).execute(SPSubagentTask("a", "researcher", "lookup", "test"))
    assert result.is_success
    assert ("full immutable rule" in captured["prompt"]) is enabled
    assert ("callbacks" in captured) is enabled
    assert ("extra_middlewares" in captured) is enabled
    if enabled:
        assert captured["extra_middlewares"] == [middleware]
        assert captured["callbacks"] == [callback]
        assert captured["config"].tools == ["local_search_tool"]
    else:
        assert captured["config"] is original
    assert original.tools is None


@pytest.mark.parametrize("extensions,requested,expected", [(None, 999, 20), (None, 5, 5), (SPExecutionExtensions(), 999, 20)])
def test_untrusted_runtime_cannot_raise_default_action_ceiling(extensions, requested, expected):
    from deerflow.sp.runtime import _central_action_budget

    assert _central_action_budget({"sp_max_actions": requested}, extensions) == expected


def test_trusted_resource_budget_reuses_native_action_guard():
    from deerflow.sp.agent_tools import SPActionBudgetMiddleware
    from deerflow.sp.runtime import _central_action_budget

    extensions = SPExecutionExtensions(max_actions=64)
    assert _central_action_budget({}, extensions) == 64
    assert _central_action_budget({"sp_max_actions": 999}, extensions) == 64
    guard = SPActionBudgetMiddleware(max_actions=_central_action_budget({}, extensions))
    assert guard.before_agent({}, None)["sp_max_loop_iterations"] == 64
    assert guard.before_model({"sp_loop_iteration": 20}, None) is None
    assert guard.before_model({"sp_loop_iteration": 64}, None)["jump_to"] == "end"


@pytest.mark.parametrize("invalid", [1, 257, True, 12.5])
def test_trusted_resource_budget_is_bounded(invalid):
    with pytest.raises(ValueError):
        SPExecutionExtensions(max_actions=invalid)
