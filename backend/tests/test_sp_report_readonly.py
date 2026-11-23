import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from test_sp_report_p3 import _finish, _seed, _send_review
from test_sp_report_turns import accepted, followup

from deerflow.sp.actions.handlers.replan import ReplanHandler
from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.report import store as native
from deerflow.sp.report.turns import adapter
from deerflow.sp.report.turns.store import file_manifest


@pytest.mark.parametrize("long_line", [False, True])
def test_compact_provenance_uses_saved_locator_not_material_prefix(tmp_path, long_line):
    ctx, ledger = _seed(tmp_path, body="Report conclusion [S1].\n", with_sources=True, node_evidence=[{"material_id": "m1", "source_ids": ["S1"], "locator": {"lines": [35, 35]}, "use": "Report interpretation, not a source quote"}])
    material = ledger.payload["materials"][0]
    passage = ("long introduction " * 300 if long_line else "") + "Actual supporting passage at saved locator."
    text = "\n".join(["Unrelated introduction"] * 34 + [passage] + ["Other text"] * 10) + "\n"
    Path(material["path"]).write_text(text)
    material.update(sha256=native.sha256_text(text), chars=len(text), lines=45)
    for item in ledger.payload["materials"]:
        data = native.read_text(Path(item["path"]))
        path = ledger.paths.material_file(item["source_id"], item["sha256"])
        native.write_text_atomic(path, data)
        item.update(path=str(path), virtual_path=ledger.paths.virtual(path))
    ledger.save()
    assert not _send_review(ctx, status="ready", outcome="deliver").error
    assert not _finish(ctx).error
    followup(ctx, text="Explain the source, without changing the report")
    before = file_manifest(tmp_path / "report/r1")
    action = SPAction.create(ActionType.REPLAN, task="Read evidence", reason="readonly", metadata={"report_action": "inspect_version", "view": "provenance:n2"})
    result = ReplanHandler().handle(action, ctx)
    assert not result.error
    payload = result.tool_observation
    assert payload["sections"][0]["title"] == "指标"
    item = payload["sections"][0]["evidence"][0]
    assert item["saved_locator"] == {"lines": [35, 35]}
    assert "Actual supporting passage" in item["content"]
    assert item["source"]["source_id"] == "S1"
    if long_line:
        assert item["truncated"] and "[Middle omitted]" in item["content"]
        assert len(item["numbered_excerpt_char_ranges"]) == 2
    assert "Report interpretation" not in item["content"]
    assert item["report_use"].startswith("Report interpretation")
    assert len(json.dumps(payload, ensure_ascii=False)) < 10000
    assert file_manifest(tmp_path / "report/r1") == before


@pytest.mark.parametrize("finish_reason,body", [("length", ""), ("stop", ""), ("length", "Partial, incomplete answer")])
def test_empty_or_truncated_answer_is_failed_with_notice_and_unchanged_head(tmp_path, finish_reason, body):
    ctx, store = accepted(tmp_path)
    followup(ctx, text="Read only")
    store, _, head = adapter.session(ctx.state, thread_id="t1")
    before = file_manifest(tmp_path / "report/r1")
    ctx.state["messages"].append(AIMessage(id="answer", content=body, response_metadata={"finish_reason": finish_reason}))
    runtime = SimpleNamespace(context={"thread_id": "t1", "run_id": "run-m2"})
    update = adapter.finalize_run(ctx.state, runtime)
    turn = store.inspect(store.index()["requests"]["m2"])
    assert turn["status"] == "failed"
    assert turn["diagnostic"]["phase"] == "dialogue_completion"
    assert turn["diagnostic"]["finish_reason"] == finish_reason
    assert store.index()["head"] == head["turn_id"]
    assert update["messages"][0].additional_kwargs["deerflow_error_fallback"]
    assert "未能完成" in update["messages"][0].content
    assert (store.directory(turn["turn_id"]) / "response.md").exists()
    store.verify(turn["turn_id"])
    ctx.state["messages"][-1] = update["messages"][0]
    assert adapter.finalize_run(ctx.state, runtime) is None
    assert file_manifest(tmp_path / "report/r1") == before


def test_successful_response_still_archived_and_no_baseline_has_no_fallback(tmp_path):
    ctx, store = accepted(tmp_path)
    followup(ctx, text="Read only")
    ctx.state["messages"].append(AIMessage(content="This is a limited answer from saved evidence.", response_metadata={"finish_reason": "stop"}))
    assert adapter.finalize_run(ctx.state, SimpleNamespace(context={"thread_id": "t1"})) is None
    turn = store.inspect(store.index()["requests"]["m2"])
    assert turn["status"] == "read_only"
    assert adapter.finalize_run({"messages": [AIMessage(content="")]}, SimpleNamespace(context={"thread_id": "fresh"})) is None


def test_after_agent_propagates_failure_notice_with_no_memory_stack(tmp_path):
    from deerflow.sp.middlewares.task_memory_middleware import TaskMemoryMiddleware

    ctx, store = accepted(tmp_path)
    followup(ctx, text="Read only")
    ctx.state["messages"].append(AIMessage(content="", id="empty", response_metadata={"finish_reason": "length"}))
    update = TaskMemoryMiddleware().after_agent(ctx.state, SimpleNamespace(context={"thread_id": "t1", "run_id": "run-m2"}))
    assert update["messages"][0].id == "empty"
    assert update["messages"][0].additional_kwargs["deerflow_error_fallback"]
    assert store.inspect(store.index()["requests"]["m2"])["status"] == "failed"


def test_central_graph_empty_terminal_response_returns_notice_not_success(tmp_path, monkeypatch):
    from langgraph.graph.message import add_messages
    from test_sp_report_wiring import _app_config, _model

    import deerflow.agents.lead_agent.agent as lead
    import deerflow.sp.runtime as runtime
    from deerflow.agents.middlewares.dynamic_context_middleware import DynamicContextMiddleware
    from deerflow.config import paths as paths_module
    from deerflow.runtime.user_context import get_effective_user_id

    monkeypatch.setattr(paths_module, "_paths", paths_module.Paths(tmp_path / "runtime"))
    out = paths_module.get_paths().sandbox_outputs_dir("t1", user_id=get_effective_user_id())
    out.mkdir(parents=True)
    ctx, store = accepted(out)
    injected = DynamicContextMiddleware(app_config=_app_config(), inject_memory=False).before_agent(ctx.state, None)
    ctx.state["messages"] = add_messages(ctx.state["messages"], injected["messages"])
    followup(ctx, text="只读解释现有资料")
    ctx.state["sp_task_memory"] = ctx.stack.to_dict()
    ctx.state["sp_new_conversation"] = False
    model = _model([AIMessage(content="", response_metadata={"finish_reason": "length"})])
    monkeypatch.setattr(lead, "create_chat_model", lambda **kwargs: model)
    graph = runtime.make_sp_agent({"configurable": {"model_name": "fake-model"}}, app_config=_app_config())
    state = graph.invoke(dict(ctx.state), config={"recursion_limit": 80}, context={"thread_id": "t1", "run_id": "run-m2"})
    assert "未能完成" in state["messages"][-1].content
    assert store.inspect(store.index()["requests"]["m2"])["status"] == "failed"


def test_report_tool_reply_preserves_data_beyond_short_task_memory(tmp_path, monkeypatch):
    from langgraph.graph.message import add_messages
    from test_sp_report_wiring import _CAPTURED_REQUESTS, _app_config, _model

    import deerflow.agents.lead_agent.agent as lead
    import deerflow.sp.runtime as runtime
    from deerflow.agents.middlewares.dynamic_context_middleware import DynamicContextMiddleware
    from deerflow.config import paths as paths_module
    from deerflow.runtime.user_context import get_effective_user_id

    monkeypatch.setattr(paths_module, "_paths", paths_module.Paths(tmp_path / "runtime"))
    out = paths_module.get_paths().sandbox_outputs_dir("t1", user_id=get_effective_user_id())
    out.mkdir(parents=True)
    ctx, ledger = _seed(out, body="Long existing report passage. " * 150 + "Source conclusion at end.\n")
    assert not _send_review(ctx, status="ready", outcome="deliver").error
    assert not _finish(ctx).error
    injected = DynamicContextMiddleware(app_config=_app_config(), inject_memory=False).before_agent(ctx.state, None)
    ctx.state["messages"] = add_messages(ctx.state["messages"], injected["messages"])
    followup(ctx, text="Read only")
    ctx.state["sp_task_memory"] = ctx.stack.to_dict()
    ctx.state["sp_new_conversation"] = False
    model = _model(
        [
            AIMessage(content="", tool_calls=[{"name": "sp_replan", "args": {"task": "Read provenance", "reason": "readonly", "metadata": {"report_action": "inspect_version", "view": "provenance:n2"}}, "id": "read"}]),
            AIMessage(content="Limited answer from the saved report; source details are missing."),
        ]
    )
    monkeypatch.setattr(lead, "create_chat_model", lambda **kwargs: model)
    graph = runtime.make_sp_agent({"configurable": {"model_name": "fake-model"}}, app_config=_app_config())
    state = graph.invoke(dict(ctx.state), config={"recursion_limit": 100}, context={"thread_id": "t1", "run_id": "run-m2"})
    reply = next(m for m in state["messages"] if getattr(m, "name", None) == "sp_replan")
    payload = json.loads(json.loads(reply.content)["result"])
    assert payload["sections"][0]["content"].endswith("Source conclusion at end.\n")
    assert any("Source conclusion at end." in str(m.content) for request in _CAPTURED_REQUESTS for m in request if getattr(m, "name", None) == "sp_replan")


def test_final_visible_answer_is_kept_separate_from_explicit_readonly_archive(tmp_path):
    ctx, store = accepted(tmp_path)
    followup(ctx, text="Read only")
    store, message, head = adapter.session(ctx.state, thread_id="t1")
    turn = store.reuse(head["turn_id"], message_id="m2", raw_query=message.content, response="Archive answer", mode="read_only")
    before = file_manifest(tmp_path / "report/r1")
    ctx.state["messages"].append(AIMessage(content="Final visible answer with additional caveats."))
    assert adapter.finalize_run(ctx.state, SimpleNamespace(context={"thread_id": "t1"})) is None
    assert (store.directory(turn["turn_id"]) / "response.md").read_text() == "Archive answer"
    assert (store.directory(turn["turn_id"]) / "visible_response.md").read_text() == "Final visible answer with additional caveats."
    assert file_manifest(tmp_path / "report/r1") == before
