import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage
from test_sp_report_p3 import _finish, _seed, _send_review

from deerflow.runtime.user_context import get_effective_user_id
from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.report import hooks
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError
from deerflow.sp.report.store import ReportPaths
from deerflow.sp.report.turns import adapter
from deerflow.sp.report.turns.store import ReportTurnStore, file_manifest


def accepted(tmp_path):
    ctx, ledger = _seed(tmp_path)
    assert _send_review(ctx, status="ready", outcome="deliver").error is None
    assert _finish(ctx).error is None
    return ctx, ReportTurnStore(tmp_path, user_id=get_effective_user_id(), thread_id="t1")


def test_freeze_fork_and_restore_are_independent(tmp_path):
    ctx, store = accepted(tmp_path)
    original = file_manifest(tmp_path / "report/r1")
    first = store.freeze("r1", resolved_query="Original requirement")
    turn = store.prepare(first["turn_id"], message_id="m2", raw_query="Change one chapter", resolved_query="Full changed requirement")
    assert store.index()["head"] == first["turn_id"]
    assert store.prepare(first["turn_id"], message_id="m2", raw_query="Change one chapter", resolved_query="Full changed requirement") == turn
    fork = ReportLedger.load(ReportPaths(tmp_path, turn["report_id"]))
    assert Path(fork.payload["drafts"][0]["path"]).read_bytes() == Path(tmp_path / "report/r1/drafts/n2/d1.md").read_bytes()
    assert fork.payload["drafts"][0]["path"].startswith(str(tmp_path / "report/r2"))
    Path(fork.payload["drafts"][0]["path"]).write_text("changed")
    assert file_manifest(tmp_path / "report/r1") == original
    store.verify(first["turn_id"])
    restored = store.restore(first["turn_id"])
    assert restored.report_id == "r3"
    assert restored.draft_file("n2", "d1").read_text() == "Seed body for n2.\n"


def test_publish_requires_new_native_delivery(tmp_path):
    _, store = accepted(tmp_path)
    first = store.freeze("r1", resolved_query="Original")
    turn = store.prepare(first["turn_id"], message_id="m2", raw_query="change", resolved_query="Changed")
    with pytest.raises(ReportValidationError, match="delivery|delivered"):
        store.publish(turn["turn_id"])
    assert store.index()["head"] == first["turn_id"]
    store.end(turn["turn_id"], status="failed", diagnostic={"error": "injected", "usage": 123})
    with pytest.raises(ReportValidationError):
        store.publish(turn["turn_id"])
    assert store.inspect(turn["turn_id"])["diagnostic"]["usage"] == 123


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_terminal_retry_does_not_fork(tmp_path, status):
    _, store = accepted(tmp_path)
    first = store.freeze("r1", resolved_query="Original")
    turn = store.prepare(first["turn_id"], message_id="m2", raw_query="change", resolved_query="Changed")
    store.end(turn["turn_id"], status=status, diagnostic={"reason": status})
    again = store.prepare(first["turn_id"], message_id="m2", raw_query="change", resolved_query="Changed")
    assert again["status"] == status
    assert len(list((tmp_path / "report").glob("r*"))) == 2
    assert store.index()["head"] == first["turn_id"]


def test_reuse_and_readonly_archive_without_execution(tmp_path):
    _, store = accepted(tmp_path)
    first = store.freeze("r1", resolved_query="Original")
    original = file_manifest(tmp_path / "report/r1")
    for mode in ["exact_reuse", "read_only"]:
        turn = store.reuse(first["turn_id"], message_id=mode, raw_query=mode, response="answer", mode=mode)
        assert turn["status"] == mode
        store.verify(turn["turn_id"])
        assert turn["resolved_query"] == "Original"
    assert store.index()["head"] == first["turn_id"]
    assert file_manifest(tmp_path / "report/r1") == original
    assert len(list((tmp_path / "report").glob("r*"))) == 1


def test_corruption_and_cross_session_are_rejected(tmp_path):
    _, store = accepted(tmp_path)
    first = store.freeze("r1", resolved_query="Original")
    with pytest.raises(ReportValidationError, match="owner"):
        ReportTurnStore(tmp_path, user_id="another", thread_id="t1").index()
    with pytest.raises(ReportValidationError):
        store.inspect("../../r1")
    snapshot = store.directory(first["turn_id"]) / "artifacts/drafts/n2/d1.md"
    snapshot.write_text("tampered")
    with pytest.raises(ReportValidationError, match="snapshot"):
        store.reuse(first["turn_id"], message_id="m2", raw_query="reuse")


def test_symlink_rejected(tmp_path):
    _, store = accepted(tmp_path)
    (tmp_path / "report/r1/escape").symlink_to("/etc/hosts")
    with pytest.raises(ReportValidationError, match="symlink"):
        store.freeze("r1", resolved_query="Original")


def followup(ctx, text="Change chapter n2", message_id="m2", **extra):
    ctx.state["messages"].append(HumanMessage(content=text, id=message_id, additional_kwargs={"report_dialogue": {"enabled": True, "report_id": "r1"}, **extra}))
    ctx.run_id = "run-" + message_id


def test_optin_requires_delivered_baseline_and_preserves_first_entry(tmp_path):
    from deerflow.sp.report.context import SPReportContextMiddleware
    from deerflow.sp.report.entry import explicit_report_entry_action

    runtime = SimpleNamespace(context={"thread_id": "t1"})
    ctx, _ = _seed(tmp_path)
    followup(ctx)
    assert adapter.session(ctx.state, thread_id="t1") is None
    assert not (tmp_path / "report_dialogue").exists()
    cfg = {"target_words": 10000, "min_words": 9000, "max_words": 11000}
    state = {"thread_data": {"outputs_path": str(tmp_path / "fresh")}, "messages": [HumanMessage(content="report", id="first", additional_kwargs={"report_length": cfg})]}
    before = explicit_report_entry_action(state, runtime)
    state["messages"][0].additional_kwargs["report_dialogue"] = {"enabled": True, "report_id": "r1"}
    after = explicit_report_entry_action(state, runtime)
    assert before.tool_calls == after.tool_calls
    assert SPReportContextMiddleware()._entry_update(state, runtime)["messages"][0].tool_calls == before.tool_calls


def test_git_profile_requires_binding_and_tools_are_restricted(tmp_path):
    from deerflow.sp.report.turns import tools
    from deerflow.sp.subagents import SPSubagentTask
    from deerflow.subagents.config import SubagentConfig

    ctx, _ = accepted(tmp_path)
    followup(ctx, report_length={"target_words": 10000})
    active = adapter.session(ctx.state, thread_id="t1")
    assert active
    store, message, head = active
    action = SPAction.create(ActionType.DELEGATE, task="resolve", reason="revision", target_agent="perception", metadata={"report_profile": "git_agent"})
    meta, refs = {}, {}
    hooks.prepare_delegation(action, ctx, meta, refs)
    assert meta["report_role"] == "git_agent"
    task = SPSubagentTask(action_id=action.action_id, task="resolve", description="resolve", subagent_type="perception", thread_id="t1", metadata=meta, context_refs=refs)
    cfg, selected, role = hooks.configure_report_subagent(task, state=ctx.state, available_tools=[SimpleNamespace(name="bash")], subagent_config=SubagentConfig(name="sp-perception", description="inspect"))
    assert {t.name for t in selected} == {"report_inspect_version", "report_prepare_turn", "report_reuse_version"}
    assert role == "git_agent" and cfg.max_turns <= 50 and cfg.tool_call_limit == 8
    assert selected[1].return_direct and selected[2].return_direct
    result = json.loads(selected[1].invoke({"base_version_id": head["turn_id"], "resolved_query": "Full requirement"}))
    assert result["status"] == "prepared"
    assert hooks.report_context(ctx.state).report_id == "r1"
    from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus

    handoff = tools.accept(action, ctx, SPSubagentResult(status=SPSubagentStatus.COMPLETED))
    assert handoff.next_step == "continue"
    assert hooks.report_context(ctx.state).report_id == "r2"
    with pytest.raises(ReportValidationError, match="duplicate"):
        hooks.prepare_delegation(action, ctx, {}, {})
    # Original source messages remain present and unchanged.
    assert ctx.state["messages"][-1].content == "Change chapter n2"
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert len(ledger.inputs()) == 1 and ledger.inputs()[0]["source"] == "resolved_query"
    hooks.sync_user_inputs(ctx.state)
    assert len(ReportLedger.load(ledger.paths).inputs()) == 1
    refs = adapter.scoped_refs(ctx.state, {"task_memory": "withdrawn", "original_query": "withdrawn", "report_packet": {"current": True}})
    assert "task_memory" not in refs and refs["original_query"] == "Full requirement"
    assert adapter.skill("report-revision-plan")


def test_central_projection_excludes_old_chat_and_memory(tmp_path):
    from deerflow.sp.report.context import SPReportContextMiddleware

    ctx, _ = accepted(tmp_path)
    ctx.state["messages"].append(HumanMessage(content="withdrawn stale requirement", name="sp_task_memory_context"))
    followup(ctx, text="Remove old condition; keep comparison")
    active = adapter.session(ctx.state, thread_id="t1")
    store, message, head = active
    turn = store.prepare(head["turn_id"], message_id="m2", raw_query=message.content, resolved_query="Complete requirement with comparison")
    adapter.activate(store, turn, ctx)
    original = list(ctx.state["messages"])
    runtime = SimpleNamespace(context={"thread_id": "t1"})
    annotation = HumanMessage(content="runtime hint", name="runtime_hint", additional_kwargs={"hide_from_ui": True})
    request = SimpleNamespace(messages=[*original, annotation], state=ctx.state, runtime=runtime)
    request.override = lambda **kw: SimpleNamespace(**kw)
    projected = SPReportContextMiddleware()._augment_request(request)
    assert all("withdrawn stale requirement" not in str(m.content) for m in projected.messages)
    assert next(m for m in projected.messages if m.id == "m2").content == "Complete requirement with comparison"
    assert annotation in projected.messages
    assert ctx.state["messages"] == original


def test_native_revision_one_node_then_history_restore(tmp_path, monkeypatch):
    from test_sp_report_writing import _accept_writer, _context, _outline, _tree, _writer_action, _writer_result

    from deerflow.sp.report import writing
    from deerflow.sp.report.submit_outline import build_submit_outline_tool

    ctx = _context(tmp_path)
    tree = _tree(write_requests=[{"key": "w2", "node_ids": ["n2"], "feedback_wanted": False}, {"key": "w3", "node_ids": ["n3"], "feedback_wanted": False}])
    third = deepcopy(tree["nodes"][1])
    third.update(node_id="n3", title="Preserved chapter")
    tree["nodes"].append(third)
    assert not _outline(ctx, tree).error
    for node in ("n2", "n3"):
        action, task, path = _writer_action(ctx, "w" + node[1:], action_id="initial-" + node)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Original " + node + "\n")
        assert not _accept_writer(ctx, action, task, _writer_result(None, task)).error
    assert not _send_review(ctx, status="ready", outcome="deliver", action_id="review1").error
    assert not _finish(ctx).error
    before = file_manifest(tmp_path / "report/r1")
    followup(ctx, text="Add comparison to chapter n2")
    store, message, head = adapter.session(ctx.state, thread_id="t1")
    first_id = head["turn_id"]
    turn = store.prepare(first_id, message_id="m2", raw_query=message.content, resolved_query="Report with comparison")
    adapter.activate(store, turn, ctx)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    proposal = deepcopy(tree)
    proposal["requirements"][0]["input_id"] = ledger.inputs()[0]["input_id"]
    proposal["write_requests"] = [{"key": "rev-n2", "node_ids": ["n2"], "feedback_wanted": False}]
    proposal["user_decisions"] = [{"input_id": ledger.inputs()[0]["input_id"], "action": "apply_to_plan", "reason": "Current complete requirement"}]
    submit = build_submit_outline_tool(paths=ledger.paths, delivery_path=ledger.paths.directory / "outline/proposal.json")
    staged = json.loads(submit.func(tree=proposal, keep_nodes=["n3"], rewrite_nodes=["n2"], drop_nodes=[], decision_reason="Only n2 changed"))
    assert staged["validation_ok"], staged
    accepted_outline = _outline(ctx, json.loads((ledger.paths.directory / "outline/proposal.json").read_text()), action_id="rev-outline")
    assert not accepted_outline.error, accepted_outline.error
    action, task, path = _writer_action(ctx, "rev-n2", action_id="rev-writer")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("Updated n2 with comparison\n")
    assert not _accept_writer(ctx, action, task, _writer_result(None, task)).error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert Path(writing.current_draft(ledger, "n3")["path"]).read_text() == "Original n3\n"
    assert not _send_review(ctx, status="ready", outcome="deliver", action_id="review2").error
    save = ReportTurnStore._save
    failures = []

    def inject_publish_failure(self, index):
        if index["head"] == turn["turn_id"] and not failures:
            failures.append("injected archive index failure")
            raise OSError(failures[-1])
        return save(self, index)

    monkeypatch.setattr(ReportTurnStore, "_save", inject_publish_failure)
    interrupted = _finish(ctx)
    assert interrupted.error and "injected archive index failure" in interrupted.error
    assert store.index()["head"] == first_id
    # Native delivery exists, but accepted-head publication still needs recovery.
    assert hooks.report_context(ctx.state).status == "delivered"
    result = _finish(ctx)
    assert not result.error, result.error
    assert store.index()["head"] == turn["turn_id"]
    assert (store.directory(turn["turn_id"]) / "response.md").exists()
    changes = json.loads((store.directory(turn["turn_id"]) / "changes.json").read_text())
    assert changes["kept_nodes"] == ["n3"] and changes["changed_nodes"] == ["n2"]
    assert file_manifest(tmp_path / "report/r1") == before
    store.verify(turn["turn_id"])
    # Third turn restores a historical base while retaining another active edit.
    followup(ctx, text="Remove a condition but retain comparison", message_id="m3")
    with pytest.raises(ReportValidationError, match="fork"):
        hooks.resume_report(ctx, SPAction.create(ActionType.REPLAN, task="resume", reason="third turn must fork"))
    third = store.prepare(first_id, message_id="m3", raw_query=ctx.state["messages"][-1].content, resolved_query="Report retaining comparison without withdrawn condition")
    adapter.activate(store, third, ctx)
    derived = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert derived.payload["dialogue_turn"]["resolved_query"] == "Report retaining comparison without withdrawn condition"
    assert Path(writing.current_draft(derived, "n2")["path"]).read_text() == "Original n2\n"
    assert store.index()["head"] == turn["turn_id"]
    store.end(third["turn_id"], status="cancelled", diagnostic={"reason": "offline test complete"})
    store.reuse(first_id, message_id="restore-old", raw_query="Restore the original accepted report")
    assert store.index()["head"] == turn["turn_id"]
    assert store.index()["active_version"] == first_id
    followup(ctx, text="A local edit after ExactReuse", message_id="m4")
    assert adapter.session(ctx.state, thread_id="t1")[2]["turn_id"] == first_id


@pytest.mark.parametrize("mode", ["read_only", "exact_reuse"])
def test_archived_turn_finishes_without_research_or_writer(tmp_path, mode):
    from deerflow.sp.actions.handlers.delegate import DelegateHandler
    from deerflow.sp.actions.handlers.finish import FinishHandler
    from deerflow.sp.report.turns.tools import build_tools
    from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus

    ctx, _ = accepted(tmp_path)
    followup(ctx, text="Read or reuse report")
    calls = []

    class Executor:
        def execute(self, task):
            calls.append(task.subagent_type)
            store, message, head = adapter.session(ctx.state, thread_id="t1")
            tools = build_tools(store, message)
            tools[2].invoke({"version_id": head["turn_id"], "mode": mode, "response": "Archived answer"})
            return SPSubagentResult(status=SPSubagentStatus.COMPLETED, result="handoff")

    handler = DelegateHandler(executor=Executor())
    action = SPAction.create(ActionType.DELEGATE, task="Inspect accepted report", reason="follow-up", target_agent="perception", metadata={"report_profile": "git_agent"})
    result = handler.handle(action, ctx)
    assert not result.error, result.error
    assert calls == ["perception"]
    forbidden = handler.handle(SPAction.create(ActionType.DELEGATE, task="write", reason="incorrect dispatch", target_agent="reporter"), ctx)
    assert forbidden.error
    finished = FinishHandler().handle(SPAction.create(ActionType.FINISH, task="answer", reason="done"), ctx)
    assert finished.next_step == "finish"
    assert "Archived answer" in finished.state_update["sp_last_run_summary"]
    assert calls == ["perception"]
    assert len(list((tmp_path / "report").glob("r*"))) == 1


def test_git_failure_does_not_retry_and_keeps_usage(tmp_path):
    from deerflow.sp.actions.handlers.delegate import DelegateHandler
    from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus

    ctx, _ = accepted(tmp_path)
    followup(ctx)
    calls = []

    class Executor:
        def execute(self, task):
            calls.append(task)
            return SPSubagentResult(status=SPSubagentStatus.FAILED, error="injected failure", token_usage_records=[{"input_tokens": 17}])

    handler = DelegateHandler(executor=Executor())

    def action():
        return SPAction.create(ActionType.DELEGATE, task="resolve", reason="revision", target_agent="perception", metadata={"report_profile": "git_agent"})

    assert handler.handle(action(), ctx).error
    assert handler.handle(action(), ctx).error
    assert len(calls) == 1
    store, _, head = adapter.session(ctx.state, thread_id="t1")
    assert store.index()["git_attempts"]["m2"]["diagnostic"]["usage_records"] == [{"input_tokens": 17}]
    assert store.index()["head"] == head["turn_id"]


def test_cancelled_run_keeps_head_and_preserves_diagnostic(tmp_path):
    from threading import Event

    ctx, _ = accepted(tmp_path)
    before = file_manifest(tmp_path / "report/r1")
    followup(ctx)
    store, message, head = adapter.session(ctx.state, thread_id="t1")
    turn = store.prepare(head["turn_id"], message_id="m2", raw_query=message.content, resolved_query="Full requirement")
    adapter.activate(store, turn, ctx)
    abort = Event()
    abort.set()
    from deerflow.sp.middlewares import TaskMemoryMiddleware

    TaskMemoryMiddleware().after_agent(ctx.state, SimpleNamespace(context={"thread_id": "t1", "__run_abort_event": abort}))
    assert store.inspect(turn["turn_id"])["status"] == "cancelled"
    assert store.index()["head"] == head["turn_id"]
    assert hooks.report_context(ctx.state).report_id == "r1"
    assert file_manifest(tmp_path / "report/r1") == before


def test_revision_write_tool_rejects_ledger_and_old_report(tmp_path):
    from langchain.tools import tool

    @tool("write_file")
    def write(path: str, content: str) -> str:
        """Save text to a file."""
        Path(path).write_text(content)
        return "saved"

    paths = ReportPaths(tmp_path, "r2")
    assigned = paths.draft_file("n2", "d2")
    assigned.parent.mkdir(parents=True)
    scoped = adapter.scoped_write_tool(write, paths, assigned)
    import inspect

    assert scoped.args_schema is write.args_schema
    assert inspect.signature(scoped.func) == inspect.signature(write.func)
    for path in [paths.ledger, tmp_path / "report/r1/drafts/n2/d1.md", tmp_path / "report_dialogue/index.json"]:
        with pytest.raises(ReportValidationError, match="host-assigned"):
            scoped.invoke({"path": str(path), "content": "bad"})
    assert scoped.invoke({"path": str(assigned), "content": "good"}) == "saved"


def test_in_place_resume_and_concurrent_turn_are_rejected(tmp_path):
    ctx, _ = accepted(tmp_path)
    before = file_manifest(tmp_path / "report/r1")
    followup(ctx)
    store, _, head = adapter.session(ctx.state, thread_id="t1")
    with pytest.raises(ReportValidationError, match="fork"):
        hooks.resume_report(ctx, SPAction.create(ActionType.REPLAN, task="resume", reason="resume"))
    store.prepare(head["turn_id"], message_id="m2", raw_query="change", resolved_query="Full requirement")
    with pytest.raises(ReportValidationError, match="progress"):
        store.prepare(head["turn_id"], message_id="m3", raw_query="overlap", resolved_query="Another requirement")
    assert file_manifest(tmp_path / "report/r1") == before


def test_archive_finish_stays_available_for_revision_wording(tmp_path):
    from deerflow.sp.agent_tools import SPFinishAvailabilityMiddleware

    ctx, _ = accepted(tmp_path)
    followup(ctx, text="修改报告要求撤销，直接恢复旧报告")
    store, message, head = adapter.session(ctx.state, thread_id="t1")
    store.reuse(head["turn_id"], message_id="m2", raw_query=message.content)
    request = SimpleNamespace(state=ctx.state, runtime=SimpleNamespace(context={"thread_id": "t1"}), tools=[SimpleNamespace(name="sp_finish")])
    assert SPFinishAvailabilityMiddleware()._filter_request(request) is request


@pytest.mark.asyncio
async def test_model_cancellation_archives_and_restores_pointer(tmp_path):
    import asyncio

    from deerflow.sp.report.context import SPReportContextMiddleware

    ctx, _ = accepted(tmp_path)
    followup(ctx)
    store, message, head = adapter.session(ctx.state, thread_id="t1")
    turn = store.prepare(head["turn_id"], message_id="m2", raw_query=message.content, resolved_query="Complete requirement")
    adapter.activate(store, turn, ctx)
    request = SimpleNamespace(state=ctx.state, messages=ctx.state["messages"], runtime=SimpleNamespace(context={"thread_id": "t1"}))
    request.override = lambda **kw: SimpleNamespace(**kw)

    async def cancelled(_request):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await SPReportContextMiddleware().awrap_model_call(request, cancelled)
    assert store.inspect(turn["turn_id"])["status"] == "cancelled"
    assert hooks.report_context(ctx.state).report_id == "r1"


def test_rebase_updates_control_hash_binding_without_touching_drafts(tmp_path):
    ctx, ledger = _seed(tmp_path)
    tree = json.loads(ledger.paths.tree_file(1).read_text())
    tree["runtime_path"] = str(ledger.paths.directory / "materials")
    from deerflow.sp.report import store as native

    native.write_json_atomic(ledger.paths.tree_file(1), tree)
    ledger.payload["tree"]["sha256"] = native.sha256_text(native.read_text(ledger.paths.tree_file(1)))
    ledger.save()
    assert not _send_review(ctx, status="ready", outcome="deliver").error
    assert not _finish(ctx).error
    store = ReportTurnStore(tmp_path, user_id=get_effective_user_id(), thread_id="t1")
    first = store.freeze("r1", resolved_query="Original")
    restored = store.restore(first["turn_id"])
    copied = ReportLedger.load(restored)
    assert copied.payload["tree"]["sha256"] == native.sha256_text(native.read_text(restored.tree_file(1)))
    assert copied.payload["dialogue_restore"]["control_rebase"]["tree/1.json"]["derived_from"] == ledger.payload["tree"]["sha256"]
    assert restored.draft_file("n2", "d1").read_bytes() == ledger.paths.draft_file("n2", "d1").read_bytes()


def test_snapshot_restores_after_original_files_are_removed(tmp_path):
    import shutil

    from deerflow.sp.report.assembly import final_binding_error

    _, store = accepted(tmp_path)
    version = store.freeze("r1", resolved_query="Original")
    shutil.rmtree(tmp_path / "report/r1")
    restored = store.restore(version["turn_id"])
    assert restored.draft_file("n2", "d1").read_text() == "Seed body for n2.\n"
    assert final_binding_error(ReportLedger.load(restored)) is None
