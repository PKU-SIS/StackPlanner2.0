from test_sp_report_writer_first import setup_edit

from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.report import hooks
from deerflow.sp.report.turns import adapter
from deerflow.sp.report.turns.store import file_manifest


def test_direct_outline_revision_prepares_current_copy_without_git_agent(tmp_path):
    ctx, _, original = setup_edit(tmp_path, bind=False)
    action = SPAction.create(ActionType.DELEGATE, target_agent="outline", task="Add independent chapter", reason="Structural edit", metadata={"report_task": "revise", "base_version_id": "t9999"})
    meta, refs = {}, {}
    hooks.prepare_delegation(action, ctx, meta, refs)
    store, _, active, turn = adapter.current_turn(ctx.state, thread_id="t1")
    assert turn["status"] == "executing" and turn["base_version_id"] == active["turn_id"]
    assert meta["report_role"] == "outline" and meta["report_id"] == turn["report_id"]
    assert "report_packet" in refs
    assert not store.index().get("git_attempts")
    assert file_manifest(tmp_path / "report/r1") == original
    first = turn["report_id"]
    hooks.prepare_delegation(action, ctx, {}, {})
    assert adapter.current_turn(ctx.state, thread_id="t1")[3]["report_id"] == first


def test_direct_outline_binding_does_not_activate_without_optin(tmp_path):
    ctx, _, original = setup_edit(tmp_path, bind=False)
    ctx.state["messages"][-1].additional_kwargs["report_dialogue"]["enabled"] = False
    action = SPAction.create(ActionType.DELEGATE, target_agent="outline", task="revise", reason="revise", metadata={"report_task": "revise"})
    hooks.prepare_delegation(action, ctx, {}, {})
    assert not (tmp_path / "report_dialogue").exists()
    assert file_manifest(tmp_path / "report/r1") == original


def test_direct_outline_adds_and_removes_tree_branch_keeping_old_drafts(tmp_path):
    import json
    from copy import deepcopy
    from pathlib import Path

    from test_sp_report_p3 import _finish, _send_review
    from test_sp_report_turns import followup
    from test_sp_report_writing import _accept_writer, _delegate, _writer_action, _writer_result

    from deerflow.sp.artifacts import SPArtifactAdapter
    from deerflow.sp.report.ledger import ReportLedger
    from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus

    ctx, _, original = setup_edit(tmp_path, bind=False)
    ctx.state["messages"][-1].content = "Add one independent chapter, preserve both existing drafts."

    def submit_outline(action_id, remove=False):
        action, task = _delegate(ctx, action_id, "outline", {"report_task": "revise"})
        ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
        tree = deepcopy(json.loads(Path(ledger.payload["tree"]["path"]).read_text()))
        tree.pop("version", None)
        tree.pop("accepted_at", None)
        uid = ledger.inputs()[0]["input_id"]
        for req in tree["requirements"]:
            req["input_id"] = uid
        tree["user_decisions"] = [{"input_id": uid, "action": "apply_to_plan", "reason": "Current edit"}]
        tree["draft_dispositions"] = [
            {"node_id": d["node_id"], "draft_id": d["draft_id"], "action": "drop" if remove and d["node_id"] == "n5" else "keep", "reason": "Preserve unaffected body; drop removed chapter"}
            for d in ledger.payload["drafts"]
            if d.get("is_current")
        ]
        if remove:
            tree["nodes"] = [n for n in tree["nodes"] if n["node_id"] not in {"n4", "n5"}]
            tree["removed_node_ids"] = ["n4", "n5"]
            tree["write_requests"] = []
        else:
            node = deepcopy(next(n for n in tree["nodes"] if n["node_id"] == "n2"))
            node.update(node_id="n5", parent_id="n4", title="New content", status="ready")
            tree["nodes"].extend([{"node_id": "n4", "parent_id": None, "kind": "section", "title": "Independent new chapter"}, node])
            tree["write_requests"] = [{"key": "new-chapter", "node_ids": ["n5"], "feedback_wanted": False}]
        result = SPSubagentResult(status=SPSubagentStatus.COMPLETED, result="Updated tree", artifact_type="outline", artifact_content=json.dumps(tree), artifact_metadata={"completion_status": "complete"})
        entry = ctx.stack.append_delegate("outline", thread_id="t1", run_id=ctx.run_id)
        response = hooks.handle_success(action, ctx, result, task, artifact_adapter=SPArtifactAdapter(), delegate_entry=entry)
        assert not response.error, response.error
        return ledger.paths

    paths = submit_outline("add-tree")
    action, task, draft = _writer_action(ctx, "new-chapter", action_id="write-new-chapter")
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text("Only new chapter content.\n")
    assert not _accept_writer(ctx, action, task, _writer_result(None, task)).error
    assert not _send_review(ctx, status="ready", outcome="deliver", action_id="review-added").error
    assert not _finish(ctx).error
    added = file_manifest(paths.directory)
    followup(ctx, text="Remove only the new independent chapter.", message_id="m3")
    paths = submit_outline("remove-tree", remove=True)
    assert not _send_review(ctx, status="ready", outcome="deliver", action_id="review-removed").error
    assert not _finish(ctx).error
    ledger = ReportLedger.load(paths)
    tree = json.loads(Path(ledger.payload["tree"]["path"]).read_text())
    assert not {"n4", "n5"} & {n["node_id"] for n in tree["nodes"]}
    current = {d["node_id"]: Path(d["path"]).read_text() for d in ledger.payload["drafts"] if d.get("is_current")}
    assert current == {"n2": "Seed body for n2.\n", "n3": "Do not change this section.\n"}
    assert file_manifest(tmp_path / "report/r1") == original
    assert file_manifest(tmp_path / "report/r2") == added
