import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from test_sp_report_turns import accepted, followup

from deerflow.sp.report.turns import adapter
from deerflow.sp.report.turns.store import file_manifest


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_terminal_turn_returns_failure_notice_and_leaves_head_unchanged(tmp_path, status):
    ctx, store = accepted(tmp_path)
    followup(ctx)
    store, message, head = adapter.session(ctx.state, thread_id="t1")
    turn = store.prepare(head["turn_id"], message_id="m2", raw_query=message.content, resolved_query="Current edit")
    store.end(turn["turn_id"], status=status, diagnostic={"reason": "No changes saved"})
    before = file_manifest(tmp_path / "report/r1")
    update = adapter.terminal_failure_update(ctx.state, SimpleNamespace(context={"thread_id": "t1", "run_id": "run-m2"}))
    assert update["jump_to"] == "end"
    assert update["messages"][0].additional_kwargs["report_dialogue_failure"] == turn["turn_id"]
    assert "未完成" in update["messages"][0].content or "已取消" in update["messages"][0].content
    assert store.index()["head"] == head["turn_id"]
    assert store.inspect(turn["turn_id"])["status"] == status
    assert file_manifest(tmp_path / "report/r1") == before
    followup(ctx, message_id="m3")
    assert adapter.terminal_failure_update(ctx.state, SimpleNamespace(context={"thread_id": "t1"})) is None


def test_executing_turn_retains_its_native_repair_opportunities(tmp_path):
    ctx, store = accepted(tmp_path)
    followup(ctx)
    store, message, head = adapter.session(ctx.state, thread_id="t1")
    turn = store.prepare(head["turn_id"], message_id="m2", raw_query=message.content, resolved_query="Current edit")
    adapter.activate(store, turn, ctx)
    assert adapter.terminal_failure_update(ctx.state, SimpleNamespace(context={"thread_id": "t1"})) is None
    assert adapter.terminal_failure_update({"messages": []}, SimpleNamespace(context={"thread_id": "ordinary"})) is None


@pytest.mark.parametrize("writer_status", ["completed", "failed"])
def test_central_graph_ends_after_terminal_writer_outcome_without_redelegation(tmp_path, monkeypatch, writer_status):
    from langgraph.graph.message import add_messages
    from test_sp_report_wiring import _CAPTURED_REQUESTS, _app_config, _model

    import deerflow.agents.lead_agent.agent as lead
    import deerflow.sp.runtime as runtime
    from deerflow.agents.middlewares.dynamic_context_middleware import DynamicContextMiddleware
    from deerflow.config import paths as paths_module
    from deerflow.runtime.user_context import get_effective_user_id
    from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus

    monkeypatch.setattr(paths_module, "_paths", paths_module.Paths(tmp_path / "runtime"))
    out = paths_module.get_paths().sandbox_outputs_dir("t1", user_id=get_effective_user_id())
    out.mkdir(parents=True)
    ctx, store = accepted(out)
    injected = DynamicContextMiddleware(app_config=_app_config(), inject_memory=False).before_agent(ctx.state, None)
    ctx.state["messages"] = add_messages(ctx.state["messages"], injected["messages"])
    followup(ctx)
    ctx.state["sp_task_memory"] = ctx.stack.to_dict()
    ctx.state["sp_new_conversation"] = False
    before = file_manifest(out / "report/r1")
    model = _model(
        [
            AIMessage(content="", tool_calls=[{"name": "sp_delegate", "args": {"target_agent": "reporter", "task": "Edit current report", "reason": "local edit", "metadata": {"report_task": "local_edit"}}, "id": "writer"}]),
            AIMessage(
                content="", tool_calls=[{"name": "sp_delegate", "args": {"target_agent": "perception", "task": "Wrong automatic recovery", "reason": "must not execute", "metadata": {"report_profile": "git_agent"}}, "id": "wrong-retry"}]
            ),
        ]
    )
    monkeypatch.setattr(lead, "create_chat_model", lambda **kwargs: model)
    calls = []

    class WriterFailure:
        def execute(self, task):
            calls.append(task.subagent_type)
            return SPSubagentResult(status=SPSubagentStatus(writer_status), result="No edits saved", error="Injected save failure" if writer_status == "failed" else None, token_usage_records=[{"input_tokens": 12, "output_tokens": 3}])

    monkeypatch.setattr(runtime.DR2SPExecutorProvider, "__call__", lambda *args: WriterFailure())
    graph = runtime.make_sp_agent({"configurable": {"model_name": "fake-model"}}, app_config=_app_config())
    state = graph.invoke(dict(ctx.state), config={"recursion_limit": 100}, context={"thread_id": "t1", "run_id": "run-m2"})
    assert calls == ["reporter"]
    assert len(_CAPTURED_REQUESTS) == 1
    assert "未完成" in state["messages"][-1].content
    turn = adapter.current_turn(state, thread_id="t1")[3]
    assert turn["status"] == "failed"
    assert file_manifest(out / "report/r1") == before
    records = list((store.directory(turn["turn_id"]) / "runs").glob("*.json"))
    assert records and json.loads(records[0].read_text())["token_usage_records"]
