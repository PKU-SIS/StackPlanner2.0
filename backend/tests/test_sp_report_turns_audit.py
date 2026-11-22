"""Review regressions: sealed native reports, failure recovery and source requirements."""

import json

import pytest
from langchain_core.messages import HumanMessage
from test_sp_report_turns import accepted, followup

from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.report import hooks
from deerflow.sp.report import store as native
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError
from deerflow.sp.report.turns import adapter
from deerflow.sp.report.turns.store import file_manifest


@pytest.mark.parametrize("operation", ["resume", "close"])
def test_sealed_native_version_is_protected_without_optin(tmp_path, operation):
    ctx, store = accepted(tmp_path)
    store.freeze("r1", resolved_query="Original")
    before = file_manifest(tmp_path / "report/r1")
    ctx.state["messages"].append(HumanMessage(content="Another request without dialogue metadata", id="m2"))
    action = SPAction.create(ActionType.REPLAN, task=operation, reason="request", metadata={"report_input_id": "u1"})
    with pytest.raises(ReportValidationError, match="sealed|fork"):
        if operation == "resume":
            hooks.resume_report(ctx, action)
        else:
            hooks.close_report(ctx, action, reason="request")
    assert file_manifest(tmp_path / "report/r1") == before


def test_activation_failure_ends_turn_and_allows_next_message(tmp_path, monkeypatch):
    ctx, store = accepted(tmp_path)
    followup(ctx)
    _, message, head = adapter.session(ctx.state, thread_id="t1")
    turn = store.prepare(head["turn_id"], message_id="m2", raw_query=message.content, resolved_query="Complete changed requirement")
    before = file_manifest(tmp_path / "report/r1")
    save = ReportLedger.save

    def fail_projection(self):
        if self.paths.report_id == turn["report_id"] and self.payload.get("dialogue_turn", {}).get("turn_id") == turn["turn_id"]:
            raise OSError("injected activation failure")
        return save(self)

    with monkeypatch.context() as patch:
        patch.setattr(ReportLedger, "save", fail_projection)
        with pytest.raises(OSError, match="activation failure"):
            adapter.activate(store, turn, ctx)
    failed = store.inspect(turn["turn_id"])
    assert failed["status"] == "failed"
    assert "activation failure" in failed["diagnostic"]["error"]
    assert hooks.report_context(ctx.state).report_id == "r1"
    followup(ctx, message_id="m3")
    assert adapter.session(ctx.state, thread_id="t1")
    next_turn = store.prepare(head["turn_id"], message_id="m3", raw_query=ctx.state["messages"][-1].content, resolved_query="Next complete requirement")
    assert next_turn["status"] == "prepared"
    assert file_manifest(tmp_path / "report/r1") == before


def test_freeze_recovers_manifest_write_after_snapshot_rename(tmp_path, monkeypatch):
    _, store = accepted(tmp_path)
    write = native.write_json_atomic
    failed = []

    def fail_manifest(path, payload):
        if path.name == "manifest.json" and not failed:
            failed.append(True)
            raise OSError("injected manifest write failure")
        return write(path, payload)

    with monkeypatch.context() as patch:
        patch.setattr(native, "write_json_atomic", fail_manifest)
        with pytest.raises(OSError, match="manifest write"):
            store.freeze("r1", resolved_query="Original")
    recovered = store.freeze("r1", resolved_query="Original")
    store.verify(recovered["turn_id"])
    assert store.index()["head"] == recovered["turn_id"]
    assert len(list((store.root / "turns").glob("t*"))) == 1


@pytest.mark.parametrize("mode", ["read_only", "exact_reuse"])
def test_other_message_cannot_reuse_during_prepared_revision(tmp_path, mode):
    _, store = accepted(tmp_path)
    head = store.freeze("r1", resolved_query="Original")
    store.prepare(head["turn_id"], message_id="m2", raw_query="change", resolved_query="Complete changed requirement")
    with pytest.raises(ReportValidationError, match="progress"):
        store.reuse(head["turn_id"], message_id="m3", raw_query="reuse", mode=mode)
    assert store.index()["head"] == head["turn_id"]


def test_first_delivered_hitl_requirements_reach_git_profile(tmp_path):
    from test_sp_report_p3 import _finish, _seed, _send_review

    ctx, ledger = _seed(tmp_path)
    revised = ledger.add_user_input("改为 FY2025，撤销 FY2024；仍保留偿付能力研究", source="hitl_feedback", source_message_id="correction", applied=True)
    tree = json.loads(ledger.paths.tree_file(1).read_text())
    tree["requirements"] = [{"id": "R1", "input_id": revised, "text": "示例公司 FY2025 偿付能力", "hard": True}]
    native.write_json_atomic(ledger.paths.tree_file(1), tree)
    ledger.payload["tree"]["sha256"] = native.sha256_text(native.read_text(ledger.paths.tree_file(1)))
    ledger.save()
    assert not _send_review(ctx, status="ready", outcome="deliver").error
    assert not _finish(ctx).error
    before = file_manifest(ledger.paths.directory)
    followup(ctx, text="只修改措辞，保持已确认范围")
    metadata, refs = {}, {}
    hooks.prepare_delegation(SPAction.create(ActionType.DELEGATE, task="resolve", reason="follow-up", target_agent="perception", metadata={"report_profile": "git_agent"}), ctx, metadata, refs)
    state = refs["report_dialogue"]["active_state"]
    context = state["native_requirement_context"]
    assert context["requirements"][0]["text"] == "示例公司 FY2025 偿付能力"
    assert context["inputs"][-1]["text"] == "改为 FY2025，撤销 FY2024；仍保留偿付能力研究"
    assert context["inputs"][-1]["source_message_id"] == "correction"
    assert state["requirements_status"] == "native_accepted"
    assert file_manifest(ledger.paths.directory) == before


def test_restore_never_reuses_an_archived_native_report_id(tmp_path):
    import shutil

    _, store = accepted(tmp_path)
    head = store.freeze("r1", resolved_query="Original")
    shutil.rmtree(tmp_path / "report/r1")
    restored = store.restore(head["turn_id"])
    assert restored.report_id == "r2"


@pytest.mark.parametrize("executing", [False, True])
def test_bound_report_cannot_delegate_an_unscoped_coder(tmp_path, executing):
    from deerflow.sp.actions.handlers.delegate import DelegateHandler

    ctx, store = accepted(tmp_path)
    followup(ctx)
    _, message, head = adapter.session(ctx.state, thread_id="t1")
    if executing:
        turn = store.prepare(head["turn_id"], message_id="m2", raw_query=message.content, resolved_query="Complete requirement")
        adapter.activate(store, turn, ctx)

    class NoExecutor:
        def execute(self, task):
            raise AssertionError("unscoped coder must never run in a bound report turn")

    result = DelegateHandler(executor=NoExecutor()).handle(SPAction.create(ActionType.DELEGATE, task="write a script", reason="incorrect report dispatch", target_agent="coder", stage="implementation"), ctx)
    assert result.error and "native report roles" in result.error


def test_native_candidate_close_releases_only_the_new_turn(tmp_path):
    ctx, store = accepted(tmp_path)
    before = file_manifest(tmp_path / "report/r1")
    followup(ctx, text="Revise the chapter; stop if the requirements cannot be met")
    _, message, head = adapter.session(ctx.state, thread_id="t1")
    turn = store.prepare(head["turn_id"], message_id="m2", raw_query=message.content, resolved_query="Full revised requirements; stop if infeasible")
    adapter.activate(store, turn, ctx)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    result = hooks.close_report(ctx, SPAction.create(ActionType.REPLAN, task="close", reason="infeasible", metadata={"report_input_id": ledger.inputs()[0]["input_id"]}), reason="infeasible")
    assert result["closed"]
    assert store.inspect(turn["turn_id"])["status"] == "cancelled"
    assert hooks.report_context(ctx.state).report_id == "r1"
    followup(ctx, message_id="m3")
    assert adapter.session(ctx.state, thread_id="t1")
    assert file_manifest(tmp_path / "report/r1") == before


def test_native_pointer_drift_cannot_dispatch_a_report_role(tmp_path):
    from deerflow.sp.actions.handlers.delegate import DelegateHandler

    ctx, store = accepted(tmp_path)
    followup(ctx)
    _, message, head = adapter.session(ctx.state, thread_id="t1")
    turn = store.prepare(head["turn_id"], message_id="m2", raw_query=message.content, resolved_query="Complete requirement")
    adapter.activate(store, turn, ctx)
    native.write_json_atomic(native.pointer_path(tmp_path), {"report_id": "r1"})

    class NoExecutor:
        def execute(self, task):
            raise AssertionError("pointer drift must fail before execution")

    result = DelegateHandler(executor=NoExecutor()).handle(SPAction.create(ActionType.DELEGATE, task="Revise the report", reason="follow-up", target_agent="outline", metadata={"report_task": "revise"}), ctx)
    assert result.error and "working copy" in result.error
