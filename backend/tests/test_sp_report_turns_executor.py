"""GitAgent profile through the real executor, with an offline fake LLM."""

import importlib
import sys

from _agent_e2e_helpers import FakeToolCallingModel
from langchain_core.messages import AIMessage
from test_sp_report_executor_e2e import _app_config
from test_sp_report_turns import accepted, followup

from deerflow.sp.actions.handlers.delegate import DelegateHandler
from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.report import hooks
from deerflow.sp.report.turns import adapter
from deerflow.sp.subagents import DR2SubagentExecutorAdapter
from deerflow.subagents import get_subagent_config


def test_git_profile_real_toolnode_handoff(tmp_path, monkeypatch):
    key = "deerflow.subagents.executor"
    previous = sys.modules.pop(key, None)
    executor = importlib.import_module(key)
    try:
        ctx, _ = accepted(tmp_path)
        followup(ctx)
        store, _, head = adapter.session(ctx.state, thread_id="t1")
        model = FakeToolCallingModel(
            responses=[
                AIMessage(content="", tool_calls=[{"name": "report_inspect_version", "args": {"version_id": head["turn_id"]}, "id": "inspect"}]),
                AIMessage(content="", tool_calls=[{"name": "report_prepare_turn", "args": {"base_version_id": head["turn_id"], "resolved_query": "Complete current requirement"}, "id": "prepare"}]),
            ]
        )
        monkeypatch.setattr(executor, "create_chat_model", lambda **kwargs: model)
        config = _app_config()
        called = []

        def factory(task):
            called.append(task)
            cfg, tools, role = hooks.configure_report_subagent(task, state=ctx.state, available_tools=[], subagent_config=get_subagent_config("sp-perception", app_config=config))
            assert role == "git_agent"
            return executor.SubagentExecutor(config=cfg, tools=tools, app_config=config, parent_model="fake-model", thread_data=ctx.state["thread_data"], thread_id="t1", run_id=ctx.run_id)

        handler = DelegateHandler(executor=DR2SubagentExecutorAdapter(factory))
        result = handler.handle(SPAction.create(ActionType.DELEGATE, task="Resolve current report requirements", reason="report follow-up", target_agent="perception", stage="perception", metadata={"report_profile": "git_agent"}), ctx)
        assert not result.error, result.error
        assert len(called) == 1
        assert set(called[0].context_refs) == {"report_dialogue"}
        assert hooks.report_context(ctx.state).report_id == "r2"
        assert store.index()["head"] == head["turn_id"]
        assert "Complete current requirement" in result.memory_entries[0].content
    finally:
        if previous is not None:
            sys.modules[key] = previous
        else:
            sys.modules.pop(key, None)
