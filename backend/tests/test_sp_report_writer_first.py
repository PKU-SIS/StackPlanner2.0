import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_sp_report_p3 import _finish, _seed, _send_review
from test_sp_report_turns import followup

from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.artifacts import SPArtifactAdapter
from deerflow.sp.report import hooks
from deerflow.sp.report import store as native
from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.report.turns import adapter
from deerflow.sp.report.turns.store import file_manifest
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus, SPSubagentTask
from deerflow.subagents.config import SubagentConfig


def setup_edit(tmp_path, *, bind=True):
    ctx, ledger = _seed(tmp_path)
    tree = json.loads(ledger.paths.tree_file(1).read_text())
    other = deepcopy(tree["nodes"][1])
    other.update(node_id="n3", title="Unchanged section")
    tree["nodes"].append(other)
    native.write_json_atomic(ledger.paths.tree_file(1), tree)
    ledger.payload["tree"]["sha256"] = native.sha256_text(ledger.paths.tree_file(1).read_text())
    draft = deepcopy(ledger.payload["drafts"][0])
    path = ledger.paths.draft_file("n3", "d2")
    native.write_text_atomic(path, "Do not change this section.\n")
    draft.update(node_id="n3", draft_id="d2", path=str(path), virtual_path=ledger.paths.virtual(path), sha256=native.sha256_text(path.read_text()))
    ledger.payload["drafts"].append(draft)
    ledger.payload["draft_dispositions"] = [{"node_id": "n2", "draft_id": "d1", "action": "keep", "reason": "Prior turn decision"}]
    ledger.save()
    assert not _send_review(ctx, status="ready", outcome="deliver").error
    assert not _finish(ctx).error
    original = file_manifest(tmp_path / "report/r1")
    followup(ctx, text="Change Seed to Revised in the indicators section; keep everything else.")
    if bind:
        adapter.session(ctx.state, thread_id="t1")
    action = SPAction.create(ActionType.DELEGATE, action_id="local-edit", task="Change Seed to Revised", reason="local edit", target_agent="reporter", metadata={"report_task": "local_edit"})
    if not bind:
        return ctx, action, original
    meta, refs = {}, {}
    hooks.prepare_delegation(action, ctx, meta, refs)
    task = SPSubagentTask(action_id=action.action_id, task=action.task, description="edit", subagent_type="reporter", thread_id="t1", metadata=meta, context_refs=refs)
    cfg, tools, role = hooks.configure_report_subagent(task, state=ctx.state, available_tools=[SimpleNamespace(name="bash")], subagent_config=SubagentConfig(name="sp-reporter", description="writer"))
    return ctx, action, task, {t.name: t for t in tools}, original


def complete(ctx, action, task, **kwargs):
    entry = ctx.stack.append_observe("Writer edit", actor="central", thread_id="t1", run_id=ctx.run_id)
    result = hooks.handle_success(action, ctx, SPSubagentResult(status=SPSubagentStatus.COMPLETED, result="Edited the requested phrase.", **kwargs), task, artifact_adapter=SPArtifactAdapter(), delegate_entry=entry)
    if result.state_update:
        ctx.state.update(result.state_update)
    return result


def test_writer_first_edits_without_git_outline_or_output_envelope(tmp_path):
    ctx, action, task, tools, original = setup_edit(tmp_path)
    assert task.metadata["report_writer_first"] is True
    assert task.metadata["report_role"] == "writer"
    assert set(tools) == {"report_find_sections", "report_read_section", "report_replace_text", "report_read_material"}
    found = json.loads(tools["report_find_sections"].invoke({"query": "指标"}))
    assert found["sections"][0]["node_id"] == "n2"
    read = json.loads(tools["report_read_section"].invoke({"node_id": "n2"}))
    assert read["content"] == "Seed body for n2.\n"
    edit = {"node_id": "n2", "old_text": "Seed", "new_text": "Revised"}
    assert json.loads(tools["report_replace_text"].invoke(edit))["status"] == "saved"
    assert json.loads(tools["report_replace_text"].invoke(edit))["status"] == "saved"
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert len(ledger.payload["writer_first"]["edits"]) == 1
    completion = complete(ctx, action, task)
    assert not completion.error
    assert any(event["event_type"] == "sp.delegate.completed" for event in completion.run_events)
    ledger = ReportLedger.load(ledger.paths)
    assert ledger.payload["review"]["review_origin"] == "programmatic_local_edit"
    assert ledger.payload["review"]["semantic_review_performed"] is False
    assert ledger.payload["review"]["covered_requirement_ids"] == []
    assert hooks.finish_block_reason(ctx) is None
    assert not _finish(ctx).error
    assert file_manifest(tmp_path / "report/r1") == original
    store, _, head, turn = adapter.current_turn(ctx.state, thread_id="t1")
    assert turn["status"] == "accepted" and store.index()["head"] == turn["turn_id"]
    old = next(d for d in native.read_json(tmp_path / "report/r1/ledger.json")["drafts"] if d["node_id"] == "n3" and d["is_current"])
    new = next(d for d in ledger.payload["drafts"] if d["node_id"] == "n3" and d["is_current"])
    assert old["sha256"] == new["sha256"]
    restored = store.restore(turn["turn_id"])
    assert native.read_json(restored.ledger)["status"] == "delivered"


@pytest.mark.parametrize("old,new", [("missing", "New"), ("Seed", "[S999]")])
def test_edit_mismatch_or_unknown_citation_is_recoverable_without_mutation(tmp_path, old, new):
    ctx, action, task, tools, _ = setup_edit(tmp_path)
    paths = ReportLedger.load_for_context(hooks.report_context(ctx.state)).paths
    before = file_manifest(paths.directory)
    response = json.loads(tools["report_replace_text"].invoke({"node_id": "n2", "old_text": old, "new_text": new}))
    assert response["status"] == "needs_adjustment"
    assert file_manifest(paths.directory) == before
    assert json.loads(tools["report_replace_text"].invoke({"node_id": "n2", "old_text": "Seed", "new_text": "Revised"}))["status"] == "saved"


def test_no_edits_are_not_published_and_failure_keeps_old_version(tmp_path):
    ctx, action, task, tools, original = setup_edit(tmp_path)
    assert not complete(ctx, action, task).error
    store, _, head, turn = adapter.current_turn(ctx.state, thread_id="t1")
    assert turn["status"] == "failed"
    assert store.index()["head"] == head["turn_id"]
    assert file_manifest(tmp_path / "report/r1") == original


def test_executor_failure_does_not_publish_partial_edits(tmp_path):
    ctx, action, task, tools, original = setup_edit(tmp_path)
    tools["report_replace_text"].invoke({"node_id": "n2", "old_text": "Seed", "new_text": "Revised"})
    hooks.handle_failure(action, ctx, SPSubagentResult(status=SPSubagentStatus.FAILED, error="injected"), task)
    store, _, head, turn = adapter.current_turn(ctx.state, thread_id="t1")
    assert turn["status"] == "failed" and store.index()["head"] == head["turn_id"]
    assert file_manifest(tmp_path / "report/r1") == original


def test_real_executor_writer_tools_and_native_finish(tmp_path, monkeypatch):
    import importlib
    import sys

    from _agent_e2e_helpers import FakeToolCallingModel
    from langchain_core.messages import AIMessage
    from test_sp_report_executor_e2e import _app_config

    from deerflow.sp.actions.handlers.delegate import DelegateHandler
    from deerflow.sp.subagents import DR2SubagentExecutorAdapter
    from deerflow.subagents import get_subagent_config

    key = "deerflow.subagents.executor"
    previous = sys.modules.pop(key, None)
    executor = importlib.import_module(key)
    try:
        ctx, action, original = setup_edit(tmp_path, bind=False)
        model = FakeToolCallingModel(
            responses=[
                AIMessage(content="", tool_calls=[{"name": "report_find_sections", "args": {"query": "指标"}, "id": "find"}]),
                AIMessage(content="", tool_calls=[{"name": "report_read_section", "args": {"node_id": "n2"}, "id": "read"}]),
                AIMessage(content="", tool_calls=[{"name": "report_replace_text", "args": {"node_id": "n2", "old_text": "Seed", "new_text": "Revised"}, "id": "edit"}]),
                AIMessage(content="Changed Seed to Revised; all other text retained."),
            ]
        )
        monkeypatch.setattr(executor, "create_chat_model", lambda **kwargs: model)
        config = _app_config()
        calls = []

        def factory(task):
            calls.append(task)
            cfg, tools, role = hooks.configure_report_subagent(task, state=ctx.state, available_tools=[], subagent_config=get_subagent_config("sp-reporter", app_config=config))
            assert role == "writer"
            return executor.SubagentExecutor(config=cfg, tools=tools, app_config=config, parent_model="fake-model", thread_data=ctx.state["thread_data"], thread_id="t1", run_id=ctx.run_id)

        handler = DelegateHandler(executor=DR2SubagentExecutorAdapter(factory))
        response = handler.handle(action, ctx)
        assert not response.error, response.error
        ctx.state.update(response.state_update)
        assert len(calls) == 1 and calls[0].metadata["report_writer_first"]
        assert not hooks.finish_block_reason(ctx)
        assert not _finish(ctx).error
        assert file_manifest(tmp_path / "report/r1") == original
        assert adapter.current_turn(ctx.state, thread_id="t1")[3]["status"] == "accepted"
    finally:
        if previous is not None:
            sys.modules[key] = previous
        else:
            sys.modules.pop(key, None)


def test_partial_undo_reads_history_and_retains_other_edits(tmp_path):
    ctx, action, task, tools, original = setup_edit(tmp_path)
    store, _, origin, _ = adapter.current_turn(ctx.state, thread_id="t1")
    tools["report_replace_text"].invoke({"node_id": "n2", "old_text": "Seed", "new_text": "Revised"})
    tools["report_replace_text"].invoke({"node_id": "n3", "old_text": "Do not change", "new_text": "Retained change to"})
    assert not complete(ctx, action, task).error
    assert not _finish(ctx).error
    second_manifest = file_manifest(tmp_path / "report/r2")
    followup(ctx, text="Undo only the indicators change; retain the other edit.", message_id="m3")
    action = SPAction.create(ActionType.DELEGATE, action_id="undo-one", task="Undo only n2", reason="partial undo", target_agent="reporter", metadata={"report_task": "local_edit"})
    meta, refs = {}, {}
    hooks.prepare_delegation(action, ctx, meta, refs)
    task = SPSubagentTask(action_id=action.action_id, task=action.task, description="undo", subagent_type="reporter", thread_id="t1", metadata=meta, context_refs=refs)
    _, selected, _ = hooks.configure_report_subagent(task, state=ctx.state, available_tools=[], subagent_config=SubagentConfig(name="sp-reporter", description="writer"))
    tools = {t.name: t for t in selected}
    historic = json.loads(tools["report_read_section"].invoke({"node_id": "n2", "version_id": origin["turn_id"]}))
    assert historic["content"] == "Seed body for n2.\n"
    tools["report_replace_text"].invoke({"node_id": "n2", "old_text": "Revised", "new_text": "Seed"})
    assert not complete(ctx, action, task).error
    assert not _finish(ctx).error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    drafts = {d["node_id"]: d for d in ledger.payload["drafts"] if d["is_current"]}
    assert native.read_text(Path(drafts["n2"]["path"])) == "Seed body for n2.\n"
    assert native.read_text(Path(drafts["n3"]["path"])) == "Retained change to this section.\n"
    assert file_manifest(tmp_path / "report/r1") == original
    assert file_manifest(tmp_path / "report/r2") == second_manifest


@pytest.mark.parametrize("cancelled", [False, True])
def test_cancel_or_truncation_never_publishes_saved_partial_edits(tmp_path, cancelled):
    ctx, action, task, tools, original = setup_edit(tmp_path)
    tools["report_replace_text"].invoke({"node_id": "n2", "old_text": "Seed", "new_text": "Revised"})
    if cancelled:
        hooks.handle_failure(action, ctx, SPSubagentResult(status=SPSubagentStatus.CANCELLED), task)
    else:
        assert complete(ctx, action, task, finish_reason="length").error
    store, _, head, turn = adapter.current_turn(ctx.state, thread_id="t1")
    assert turn["status"] == ("cancelled" if cancelled else "failed")
    assert store.index()["head"] == head["turn_id"]
    assert file_manifest(tmp_path / "report/r1") == original


def test_native_length_constraint_is_not_relaxed(tmp_path):
    ctx, action, task, tools, _ = setup_edit(tmp_path)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    ledger.payload["length_contract"] = {"min_words": 10, "target_words": 15, "max_words": 20}
    ledger.save()
    tools["report_replace_text"].invoke({"node_id": "n2", "old_text": "Seed", "new_text": "Very long " * 100})
    response = complete(ctx, action, task)
    assert response.error and "length contract" in response.error
    store, _, head, turn = adapter.current_turn(ctx.state, thread_id="t1")
    assert turn["status"] == "executing" and store.index()["head"] == head["turn_id"]


def test_writer_prompt_has_no_json_handoff_requirement(tmp_path):
    from deerflow.sp.subagents.dr2_adapter import render_sp_subagent_prompt

    _, _, task, _, _ = setup_edit(tmp_path)
    prompt = render_sp_subagent_prompt(task)
    assert "no JSON artifact or task_result envelope is required" in prompt
    assert "return compact JSON with summary" not in prompt


@pytest.mark.parametrize("mode", ["read_only", "exact_reuse"])
def test_direct_version_read_and_archive_need_no_agent_or_fork(tmp_path, mode):
    from test_sp_report_turns import accepted

    from deerflow.sp.actions.handlers.replan import ReplanHandler

    ctx, store = accepted(tmp_path)
    before = file_manifest(tmp_path / "report/r1")
    followup(ctx, text="Read or reuse the accepted report")
    read = SPAction.create(ActionType.REPLAN, task="Read report", reason="question", metadata={"report_action": "inspect_version", "view": "draft:n2"})
    response = ReplanHandler().handle(read, ctx)
    assert not response.error
    assert "Seed body for n2." in response.memory_entries[0].content
    action = SPAction.create(ActionType.REPLAN, task="Archive", reason="archive", metadata={"report_action": "reuse_version", "mode": mode, "response": "Answer from saved report"})
    response = ReplanHandler().handle(action, ctx)
    assert not response.error
    assert not ReplanHandler().handle(action, ctx).error
    assert not _finish(ctx).error
    store, _, head, turn = adapter.current_turn(ctx.state, thread_id="t1")
    assert turn["status"] == mode and store.index()["head"] == head["turn_id"]
    assert len(list((tmp_path / "report").glob("r*"))) == 1
    assert file_manifest(tmp_path / "report/r1") == before


def test_central_graph_directly_dispatches_writer_and_finishes(tmp_path, monkeypatch):
    from langchain_core.messages import AIMessage
    from langgraph.graph.message import add_messages
    from test_sp_report_wiring import _app_config, _model

    import deerflow.agents.lead_agent.agent as lead
    import deerflow.sp.runtime as runtime
    from deerflow.agents.middlewares.dynamic_context_middleware import DynamicContextMiddleware
    from deerflow.config import paths as paths_module
    from deerflow.runtime.user_context import get_effective_user_id

    monkeypatch.setattr(paths_module, "_paths", paths_module.Paths(tmp_path / "runtime"))
    outputs = paths_module.get_paths().sandbox_outputs_dir("t1", user_id=get_effective_user_id())
    outputs.mkdir(parents=True)
    ctx, _, original = setup_edit(outputs, bind=False)
    ctx.state["messages"].pop()  # Normalize the accepted checkpoint before appending the follow-up.
    injected = DynamicContextMiddleware(app_config=_app_config(), inject_memory=False).before_agent(ctx.state, None)
    ctx.state["messages"] = add_messages(ctx.state["messages"], injected["messages"])
    followup(ctx, text="Change Seed to Revised in the indicators section; keep everything else.")
    ctx.state["sp_task_memory"] = ctx.stack.to_dict()
    ctx.state["sp_new_conversation"] = False
    calls = []

    class LocalExecutor:
        def __init__(self, state):
            self.state = state

        def execute(self, task):
            calls.append(task.subagent_type)
            _, selected, role = hooks.configure_report_subagent(task, state=self.state, available_tools=[], subagent_config=SubagentConfig(name="sp-reporter", description="writer"))
            assert role == "writer"
            tools = {t.name: t for t in selected}
            tools["report_read_section"].invoke({"node_id": "n2"})
            tools["report_replace_text"].invoke({"node_id": "n2", "old_text": "Seed", "new_text": "Revised"})
            return SPSubagentResult(status=SPSubagentStatus.COMPLETED, result="Edited requested phrase.")

    model = _model(
        [
            AIMessage(content="", tool_calls=[{"name": "sp_delegate", "args": {"target_agent": "reporter", "task": "Change Seed to Revised", "reason": "Small edit", "metadata": {"report_task": "local_edit"}}, "id": "local"}]),
            AIMessage(content="", tool_calls=[{"name": "sp_finish", "args": {"summary": "Edited requested phrase"}, "id": "finish"}]),
        ]
    )
    monkeypatch.setattr(lead, "create_chat_model", lambda **kwargs: model)
    monkeypatch.setattr(runtime.DR2SPExecutorProvider, "__call__", lambda self, state, runtime: LocalExecutor(state))
    graph = runtime.make_sp_agent({"configurable": {"model_name": "fake-model"}}, app_config=_app_config())
    from langgraph.errors import GraphRecursionError

    state = None
    try:
        for state in graph.stream(dict(ctx.state), config={"recursion_limit": 150}, context={"thread_id": "t1", "run_id": "run-m2"}, stream_mode="values"):
            pass
    except GraphRecursionError:
        observations = [(m.name, str(m.content)[:1500]) for m in state["messages"] if m.type == "tool"]
        pytest.fail(f"Central did not finish: calls={calls}; observations={observations[:6]}")
    assert calls == ["reporter"]
    store, _, _, turn = adapter.current_turn(state, thread_id="t1")
    assert turn["status"] == "accepted"
    assert file_manifest(outputs / "report/r1") == original


def test_local_edit_requires_optin_and_cannot_fork_first_turn(tmp_path):
    from test_sp_report_turns import accepted

    ctx, _ = accepted(tmp_path)
    before = file_manifest(tmp_path / "report/r1")
    action = SPAction.create(ActionType.DELEGATE, target_agent="reporter", task="edit", reason="edit", metadata={"report_task": "local_edit"})
    from deerflow.sp.report.ledger import ReportValidationError

    with pytest.raises(ReportValidationError, match="opted-in"):
        hooks.prepare_delegation(action, ctx, {}, {})
    assert file_manifest(tmp_path / "report/r1") == before
    assert not (tmp_path / "report_dialogue").exists()
