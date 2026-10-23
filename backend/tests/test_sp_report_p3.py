"""P3 assembly, integrate-review, final report, delivery, and resume behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.handlers.finish import FinishHandler
from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.artifacts import SPArtifactAdapter
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.report import hooks
from deerflow.sp.report import store as report_store
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus, SPSubagentTask


def _context(tmp_path: Path) -> HandlerContext:
    state = {
        "thread_data": {"outputs_path": str(tmp_path)},
        "messages": [HumanMessage(content="请写一份关于示例公司的报告。", id="m1")],
    }
    ctx = HandlerContext(state=state, stack=TaskMemoryStack(), thread_id="t1", run_id="run1")
    hooks.start_report(ctx, SPAction.create(ActionType.REPLAN, reason="start", task="start", metadata={"report_action": "start"}))
    return ctx


def _paths(ctx: HandlerContext) -> report_store.ReportPaths:
    context = hooks.report_context(ctx.state)
    assert context.outputs_path and context.report_id
    return report_store.ReportPaths(context.outputs_path, context.report_id)


def _seed(tmp_path: Path, *, write_status: str | None = None, body: str = "Seed body for n2.\n", with_sources: bool = False) -> tuple[HandlerContext, ReportLedger]:
    ctx = _context(tmp_path)
    paths = _paths(ctx)
    ledger = ReportLedger.load(paths)
    tree = {
        "version": 1,
        "report_title": "示例公司报告",
        "requirements": [{"id": "R1", "input_id": "u1", "text": "示例公司 FY2024 偿付能力", "hard": True}],
        "nodes": [
            {"node_id": "n1", "parent_id": None, "kind": "section", "title": "偿付能力"},
            {
                "node_id": "n2",
                "parent_id": "n1",
                "kind": "writable",
                "title": "指标",
                "question": "FY2024 指标如何？",
                "argument": ["读取官方附注"],
                "conclusion": "待证据",
                "evidence": [],
                "constraints": {"period": "FY2024"},
                "requirement_ids": ["R1"],
                "relations": [],
                "gap_ids": [],
                "status": "planned",
            },
        ],
    }
    tree_path = paths.tree_file(1)
    report_store.write_json_atomic(tree_path, tree)
    ledger.payload["tree"] = {
        "version": 1,
        "path": str(tree_path),
        "virtual_path": paths.virtual(tree_path),
        "sha256": report_store.sha256_text(tree_path.read_text(encoding="utf-8")),
        "node_ids": ["n1", "n2"],
        "accepted_at": "2026-10-20T00:00:00+00:00",
    }
    ledger.payload["nodes"] = {"n1": {"node_id": "n1", "kind": "section", "title": "偿付能力"}, "n2": {"node_id": "n2", "kind": "writable", "title": "指标"}}
    draft_path = paths.draft_file("n2", "d1")
    report_store.write_text_atomic(draft_path, body)
    ledger.payload["drafts"] = [
        {
            "draft_id": "d1",
            "node_id": "n2",
            "write_key": "w1",
            "action_id": "a-writer",
            "path": str(draft_path),
            "virtual_path": paths.virtual(draft_path),
            "sha256": report_store.sha256_text(body),
            "chars": len(body),
            "is_current": True,
            "review_status": "none",
            "status": "current",
            "applicable_fingerprint": "node-fp",
        }
    ]
    if write_status:
        ledger.payload["write_requests"] = [
            {
                "key": "w1",
                "node_ids": ["n2"],
                "feedback_wanted": False,
                "status": write_status,
                "round": 1,
                "attempts": [{"action_id": "a-writer", "status": "completed" if write_status == "completed" else "in_flight"}],
            }
        ]
    if with_sources:
        ledger.payload["sources"] = [{"source_id": "S1", "url": "https://example.com/source", "title": "示例来源", "normalized_url": "https://example.com/source"}]
        material_paths = [tmp_path / "m1.txt", tmp_path / "m2.txt"]
        material_texts = ["material one\n", "material two\n"]
        for path, text in zip(material_paths, material_texts, strict=True):
            report_store.write_text_atomic(path, text)
        ledger.payload["materials"] = [
            {
                "material_id": "m1",
                "source_id": "S1",
                "path": str(material_paths[0]),
                "sha256": report_store.sha256_text(material_texts[0]),
                "chars": len(material_texts[0]),
                "lines": 1,
                "virtual_path": "/mnt/user-data/outputs/m1.txt",
            },
            {
                "material_id": "m2",
                "source_id": "S1",
                "path": str(material_paths[1]),
                "sha256": report_store.sha256_text(material_texts[1]),
                "chars": len(material_texts[1]),
                "lines": 1,
                "virtual_path": "/mnt/user-data/outputs/m2.txt",
            },
        ]
    ledger.save()
    return ctx, ledger


def _prepare_review(ctx: HandlerContext, *, action_id: str = "a-review") -> tuple[SPAction, SPSubagentTask, object]:
    # Lower-level assembly/edit tests intentionally exercise the legacy review
    # apply path without the fixed host action registry.  Production entry
    # points are covered separately by test_sp_report_acceptance_*.
    from deerflow.sp.report import assembly as report_assembly, packets as report_packets

    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    report_assembly.ensure_candidate(ledger)
    packet = report_packets.build_integrate_review_packet(ledger)
    action = SPAction.create(
        ActionType.DELEGATE,
        action_id=action_id,
        reason="integration review",
        task="review candidate",
        target_agent="outline",
        stage="verification",
        metadata={"report_task": "integrate_review"},
    )
    meta: dict = {
        "stage": "verification",
        "report_id": ledger.payload.get("report_id"),
        "report_role": "outline",
        "report_task": "integrate_review",
    }
    refs: dict = {"report_packet": packet}
    task = SPSubagentTask(
        action_id=action_id,
        subagent_type="outline",
        task="review candidate",
        description="review",
        metadata=meta,
        context_refs=refs,
        stage="verification",
    )
    entry = ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="verification")
    return action, task, entry


def _send_review(
    ctx: HandlerContext,
    *,
    status: str,
    outcome: str,
    edits: list[dict] | None = None,
    findings: list[dict] | None = None,
    action_id: str = "a-review",
) -> object:
    action, task, entry = _prepare_review(ctx, action_id=action_id)
    packet = task.context_refs["report_packet"]
    content = json.dumps(
        {
            "inputs_digest": packet["inputs_digest"],
            "reviewed_candidate_sha256": packet["candidate_sha256"],
            "review": {
                "status": status,
                "outcome": outcome,
                "findings": findings or [],
                "edits": edits or [],
                "covered_requirement_ids": ["R1"],
            },
        },
        ensure_ascii=False,
    )
    result = SPSubagentResult(
        status=SPSubagentStatus.COMPLETED,
        result="review",
        artifact_content=content,
        artifact_type="integration_review",
        artifact_metadata={"artifact_type": "integration_review", "report_task": "integrate_review", "status": status},
    )
    handler_result = hooks.handle_success(action, ctx, result, task, artifact_adapter=SPArtifactAdapter(), delegate_entry=entry)
    if handler_result is not None and handler_result.state_update:
        ctx.state.update(handler_result.state_update)
    return handler_result


def _finish(ctx: HandlerContext) -> object:
    action = SPAction.create(ActionType.FINISH, action_id="a-finish", reason="done", task="done")
    return FinishHandler().handle(action, ctx)


def _ledger(ctx: HandlerContext) -> ReportLedger:
    return ReportLedger.load_for_context(hooks.report_context(ctx.state))


def test_dedupe_edit_can_delete_redundant_sentence(tmp_path):
    ctx, _ = _seed(tmp_path, body="Keep this sentence. Redundant sentence.\n")
    reviewed = _send_review(
        ctx,
        status="issues",
        outcome="edit",
        edits=[{"draft_id": "d1", "kind": "dedupe", "anchor": " Redundant sentence.", "replacement": "", "reason": "删除重复句"}],
        action_id="a-dedupe",
    )
    assert reviewed.next_step == "continue", reviewed.error
    ledger = _ledger(ctx)
    candidate = ledger.payload["candidate"]
    content = Path(candidate["path"]).read_text(encoding="utf-8")
    assert "Keep this sentence." in content
    assert "Redundant sentence" not in content
    assert "Keep this sentence. Redundant sentence." in Path(ledger.payload["drafts"][0]["path"]).read_text(encoding="utf-8")


def test_assembly_follows_parent_child_order_not_array_order(tmp_path):
    ctx = _context(tmp_path)
    paths = _paths(ctx)
    ledger = ReportLedger.load(paths)
    tree = {
        "version": 1,
        "report_title": "Order report",
        "requirements": [],
        "nodes": [
            {"node_id": "A", "parent_id": None, "kind": "section", "title": "A"},
            {"node_id": "B", "parent_id": None, "kind": "section", "title": "B"},
            {"node_id": "A_child", "parent_id": "A", "kind": "section", "title": "A child"},
            {"node_id": "B_child", "parent_id": "B", "kind": "section", "title": "B child"},
        ],
    }
    tree_path = paths.tree_file(1)
    report_store.write_json_atomic(tree_path, tree)
    ledger.payload["tree"] = {
        "version": 1,
        "path": str(tree_path),
        "virtual_path": paths.virtual(tree_path),
        "sha256": report_store.sha256_text(tree_path.read_text(encoding="utf-8")),
        "node_ids": ["A", "B", "A_child", "B_child"],
    }
    ledger.save()
    _action, task, _entry = _prepare_review(ctx, action_id="a-order-review")
    content = task.context_refs["report_packet"]["candidate"]["content"]
    assert content.index("## A\n") < content.index("### A child") < content.index("## B\n") < content.index("### B child")


def test_draft_file_change_after_review_blocks_reuse_and_delivery(tmp_path):
    ctx, _ = _seed(tmp_path)
    assert _send_review(ctx, status="ready", outcome="deliver", action_id="a-review-file").next_step == "continue"
    ledger = _ledger(ctx)
    draft_path = Path(ledger.payload["drafts"][0]["path"])
    draft_path.write_text("Changed outside the ledger.\n", encoding="utf-8")
    blocked = _finish(ctx)
    assert blocked.next_step == "error_recoverable"
    assert "content hash does not match the ledger" in blocked.error
    assert _ledger(ctx).payload["delivered"] is None
    with pytest.raises(ReportValidationError):
        _prepare_review(ctx, action_id="a-review-file")


def test_review_acceptance_rejects_changed_input_files(tmp_path):
    ctx, _ = _seed(tmp_path)
    action, task, entry = _prepare_review(ctx, action_id="a-review-stale")
    packet = task.context_refs["report_packet"]
    review_content = json.dumps(
        {
            "inputs_digest": packet["inputs_digest"],
            "reviewed_candidate_sha256": packet["candidate_sha256"],
            "review": {"status": "ready", "outcome": "deliver", "findings": [], "edits": [], "covered_requirement_ids": ["R1"]},
        }
    )
    Path(_ledger(ctx).payload["drafts"][0]["path"]).write_text("Changed during review.\n", encoding="utf-8")
    result = SPSubagentResult(
        status=SPSubagentStatus.COMPLETED,
        result="review",
        artifact_content=review_content,
        artifact_type="integration_review",
        artifact_metadata={"artifact_type": "integration_review", "report_task": "integrate_review", "status": "ready"},
    )
    rejected = hooks.handle_success(action, ctx, result, task, artifact_adapter=SPArtifactAdapter(), delegate_entry=entry)
    assert rejected.next_step == "error_recoverable"
    assert "content hash does not match the ledger" in rejected.error
    assert _ledger(ctx).payload["final_report"] is None


def test_finish_replay_returns_same_delivery_and_final_ref_without_outer_state(tmp_path):
    ctx, _ = _seed(tmp_path)
    assert _send_review(ctx, status="ready", outcome="deliver", action_id="a-review-replay").next_step == "continue"
    ledger = _ledger(ctx)
    final = ledger.payload["final_report"]
    first_action = SPAction.create(ActionType.FINISH, action_id="a-finish-replay", reason="done", task="done")
    first = FinishHandler().handle(first_action, ctx)
    assert first.next_step == "finish", first.error
    delivery = _ledger(ctx).payload["delivered"]
    assert delivery is not None
    # Deliberately do not apply first.state_update: the outer state is stale.
    replay = FinishHandler().handle(first_action, ctx)
    assert replay.next_step == "finish", replay.error
    assert replay.state_update["sp_last_final_artifact_ref"] == final["artifact_id"]
    assert replay.state_update["sp_report_delivery_id"] == delivery["delivery_id"]
    assert _ledger(ctx).payload["delivered"]["delivery_id"] == delivery["delivery_id"]

    replay_with_ref = FinishHandler().handle(
        SPAction.create(
            ActionType.FINISH,
            action_id="a-finish-replay",
            reason="done",
            task="done",
            metadata={"final_artifact_ref": final["artifact_id"]},
        ),
        ctx,
    )
    assert replay_with_ref.next_step == "finish", replay_with_ref.error
    assert replay_with_ref.state_update["sp_last_final_artifact_ref"] == final["artifact_id"]
    assert replay_with_ref.state_update["sp_report_delivery_id"] == delivery["delivery_id"]

    ordinary = FinishHandler().handle(
        SPAction.create(ActionType.FINISH, action_id="a-ordinary-after-delivery", reason="next", task="next", metadata={"allow_without_artifact": True}),
        ctx,
    )
    assert ordinary.next_step == "finish", ordinary.error
    assert "sp_report_delivery_id" not in ordinary.state_update

    old_ref = FinishHandler().handle(
        SPAction.create(
            ActionType.FINISH,
            action_id="a-new-old-ref",
            reason="bad",
            task="bad",
            metadata={"final_artifact_ref": final["artifact_id"], "allow_without_artifact": True},
        ),
        ctx,
    )
    assert old_ref.next_step == "error_recoverable"
    assert "historical delivered report final" in old_ref.error


def test_finish_rejects_old_final_ref_and_returns_current_final(tmp_path):
    ctx, _ = _seed(tmp_path)
    assert _send_review(ctx, status="ready", outcome="deliver", action_id="a-review-v1").next_step == "continue"
    ledger = _ledger(ctx)
    v1 = ledger.payload["final_report"]
    v1_content = Path(v1["path"]).read_text(encoding="utf-8")
    edited = _send_review(
        ctx,
        status="issues",
        outcome="edit",
        edits=[{"draft_id": "d1", "kind": "format", "anchor": "Seed body", "replacement": "Changed final wording", "reason": "措辞调整"}],
        action_id="a-review-edit-v2",
    )
    assert edited.next_step == "continue", edited.error
    assert _send_review(ctx, status="ready", outcome="deliver", action_id="a-review-v2").next_step == "continue"
    ledger = _ledger(ctx)
    v2 = ledger.payload["final_report"]
    v2_content = Path(v2["path"]).read_text(encoding="utf-8")
    assert v1_content != v2_content
    assert v1["artifact_id"] != v2["artifact_id"]

    rejected = FinishHandler().handle(
        SPAction.create(
            ActionType.FINISH,
            action_id="a-finish-old",
            reason="done",
            task="done",
            metadata={"final_artifact_ref": v1["artifact_id"]},
        ),
        ctx,
    )
    assert rejected.next_step == "error_recoverable"
    assert "does not match the current reviewed report final" in rejected.error
    assert _ledger(ctx).payload["delivered"] is None

    finished = _finish(ctx)
    assert finished.next_step == "finish", finished.error
    assert finished.state_update["sp_last_final_artifact_ref"] == v2["artifact_id"]
    ledger = _ledger(ctx)
    assert ledger.payload["delivered"]["final_path"] == v2["virtual_path"]
    assert ledger.payload["delivered"]["final_artifact_id"] == v2["artifact_id"]
    assert Path(v2["path"]).read_text(encoding="utf-8") == v2_content


def test_cancelled_or_delivered_report_does_not_take_over_ordinary_finish(tmp_path):
    ctx, _ = _seed(tmp_path)
    closed = hooks.close_report(
        ctx,
        SPAction.create(ActionType.REPLAN, reason="cancel", task="cancel", metadata={"report_action": "close", "report_input_id": "u1"}),
        reason="用户取消",
    )
    assert closed["status"] == "abandoned"
    ordinary = FinishHandler().handle(
        SPAction.create(ActionType.FINISH, action_id="a-ordinary-cancelled", reason="done", task="done", metadata={"allow_without_artifact": True}),
        ctx,
    )
    assert ordinary.next_step == "finish", ordinary.error

    # A delivered report is also historical; a later ordinary finish is not a replay.
    ctx2, _ = _seed(tmp_path / "delivered")
    assert _send_review(ctx2, status="ready", outcome="deliver", action_id="a-review-delivered").next_step == "continue"
    ledger2 = _ledger(ctx2)
    historical_artifact = ledger2.payload["final_report"]["artifact_id"]
    assert _finish(ctx2).next_step == "finish"
    explicit = FinishHandler().handle(
        SPAction.create(
            ActionType.FINISH,
            action_id="a-historical-explicit",
            reason="done",
            task="done",
            metadata={"final_artifact_ref": historical_artifact, "allow_without_artifact": True},
        ),
        ctx2,
    )
    assert explicit.next_step == "error_recoverable"
    assert "historical delivered report final" in explicit.error
    ordinary2 = FinishHandler().handle(
        SPAction.create(ActionType.FINISH, action_id="a-ordinary-delivered", reason="done", task="done", metadata={"allow_without_artifact": True}),
        ctx2,
    )
    assert ordinary2.next_step == "finish", ordinary2.error


def test_same_candidate_can_be_proactively_re_reviewed(tmp_path):
    ctx, _ = _seed(tmp_path)
    assert _send_review(ctx, status="ready", outcome="deliver", action_id="a-review-first").next_step == "continue"
    ledger = _ledger(ctx)
    first_final = ledger.payload["final_report"]
    assert _send_review(ctx, status="ready", outcome="deliver", action_id="a-review-second").next_step == "continue"
    ledger = _ledger(ctx)
    second_final = ledger.payload["final_report"]
    assert second_final["review_id"] != first_final["review_id"]
    assert second_final["candidate_id"] == first_final["candidate_id"]
    assert second_final["version"] == first_final["version"] + 1
    assert ledger.payload["final_reports"][0]["status"] == "superseded"
    assert ledger.payload["delivered"] is None


def test_sources_keep_identity_and_material_versions(tmp_path):
    ctx, _ = _seed(tmp_path, body="Seed body [S1].\n", with_sources=True)
    assert _send_review(ctx, status="ready", outcome="deliver", action_id="a-review-source").next_step == "continue"
    ledger = _ledger(ctx)
    final = Path(ledger.payload["final_report"]["path"]).read_text(encoding="utf-8")
    assert "[S1]" in final
    assert "material: m1, m2" in final
    assert ledger.payload["candidate"]["source_table"][0]["source_id"] == "S1"
    assert [item["material_id"] for item in ledger.payload["candidate"]["source_table"][0]["material_versions"]] == ["m1", "m2"]


def test_delivery_failure_does_not_mark_delivered_and_replay_is_idempotent(tmp_path):
    ctx, _ = _seed(tmp_path)
    assert _send_review(ctx, status="ready", outcome="deliver", action_id="a-review-delivery").next_step == "continue"
    ledger = _ledger(ctx)
    final = ledger.payload["final_report"]
    original = Path(final["path"]).read_text(encoding="utf-8")
    Path(final["path"]).write_text("corrupted", encoding="utf-8")
    failed = _finish(ctx)
    assert failed.next_step == "error_recoverable"
    assert "Report delivery failed" in failed.error or "no current final report" in failed.error
    assert _ledger(ctx).payload["delivered"] is None

    Path(final["path"]).write_text(original, encoding="utf-8")
    first = _finish(ctx)
    assert first.next_step == "finish", first.error
    first_delivery = _ledger(ctx).payload["delivered"]
    second = _finish(ctx)
    assert second.next_step == "finish", second.error
    ledger = _ledger(ctx)
    assert ledger.payload["delivered"]["delivery_id"] == first_delivery["delivery_id"]
    assert ledger.payload["delivery_history"] == []


def test_normal_candidate_review_final_and_first_finish(tmp_path):
    ctx, _ = _seed(tmp_path)
    reviewed = _send_review(ctx, status="ready", outcome="deliver", action_id="a-review-ready")
    assert reviewed.next_step == "continue", reviewed.error
    ledger = _ledger(ctx)
    candidate = ledger.payload["candidate"]
    final = ledger.payload["final_report"]
    assert candidate["status"] == "finalized"
    assert final["status"] == "current"
    assert final["candidate_id"] == candidate["candidate_id"]
    assert final["review_id"] == ledger.payload["review"]["review_id"]
    assert "Seed body for n2." in Path(final["path"]).read_text(encoding="utf-8")
    assert ledger.payload["delivered"] is None

    finished = _finish(ctx)
    assert finished.next_step == "finish", finished.error
    ledger = _ledger(ctx)
    assert ledger.payload["status"] == "delivered"
    assert ledger.payload["delivered"]["final_path"] == final["virtual_path"]
    assert ledger.payload["delivery_history"] == []
    assert Path(final["path"]).read_text(encoding="utf-8") == Path(candidate["path"]).read_text(encoding="utf-8")


def test_two_edit_rounds_reuse_edited_candidate_and_final_binds_content(tmp_path):
    ctx, _ = _seed(tmp_path)
    first = _send_review(
        ctx,
        status="issues",
        outcome="edit",
        edits=[{"draft_id": "d1", "kind": "bridge", "anchor": "Seed body", "replacement": "Edited one body", "reason": "衔接"}],
        action_id="a-edit-1",
    )
    assert first.next_step == "continue", first.error
    first_ledger = _ledger(ctx)
    first_candidate = first_ledger.payload["candidate"]
    assert first_candidate["candidate_id"] == "c2"
    assert first_candidate["status"] == "pending_review"
    assert "Edited one body" in Path(first_candidate["path"]).read_text(encoding="utf-8")
    assert first_ledger.payload["final_report"] is None

    packet_action, packet_task, _ = _prepare_review(ctx, action_id="a-review-2")
    packet = packet_task.context_refs["report_packet"]
    assert packet["candidate"]["candidate_id"] == "c2"
    assert "Edited one body" in packet["candidate"]["content"]
    # Prepare must reuse the edited candidate instead of rebuilding from the draft.
    assert "Seed body" in Path(first_ledger.payload["drafts"][0]["path"]).read_text(encoding="utf-8")
    del packet_action

    second = _send_review(
        ctx,
        status="issues",
        outcome="edit",
        edits=[{"draft_id": "d1", "kind": "terminology", "anchor": "for n2", "replacement": "for revised n2", "reason": "术语统一"}],
        action_id="a-edit-2",
    )
    assert second.next_step == "continue", second.error
    second_ledger = _ledger(ctx)
    second_candidate = second_ledger.payload["candidate"]
    assert second_candidate["candidate_id"] == "c3"
    second_content = Path(second_candidate["path"]).read_text(encoding="utf-8")
    assert "Edited one body" in second_content
    assert "for revised n2" in second_content
    assert "Seed body" not in second_content
    assert len(second_candidate["edits_applied"]) == 2

    ready = _send_review(ctx, status="ready", outcome="deliver", action_id="a-review-3")
    assert ready.next_step == "continue", ready.error
    ledger = _ledger(ctx)
    final = ledger.payload["final_report"]
    final_content = Path(final["path"]).read_text(encoding="utf-8")
    assert "Edited one body" in final_content
    assert "for revised n2" in final_content
    assert final["inputs_digest"] == ledger.payload["review"]["inputs_digest"]
    finished = _finish(ctx)
    assert finished.next_step == "finish", finished.error
    assert Path(final["path"]).read_text(encoding="utf-8") == Path(ledger.paths.final / f"report-{final['version']}.md").read_text(encoding="utf-8")


def test_substantive_review_blocks_final_until_revision(tmp_path):
    ctx, _ = _seed(tmp_path)
    reviewed = _send_review(
        ctx,
        status="issues",
        outcome="revise",
        findings=[{"kind": "evidence", "severity": "blocking", "node_ids": ["n2"], "draft_ids": ["d1"], "message": "证据不足"}],
        action_id="a-review-revise",
    )
    assert reviewed.next_step == "continue", reviewed.error
    ledger = _ledger(ctx)
    assert ledger.payload["final_report"] is None
    assert ledger.payload["review"]["status"] == "issues"
    assert "no current ready integration review" in (_finish(ctx).error or "")
    assert ledger.payload["delivered"] is None


@pytest.mark.parametrize("write_status", ["pending", "technical_failed"])
def test_pending_or_failed_write_blocks_delivery_but_not_review(tmp_path, write_status):
    ctx, _ = _seed(tmp_path, write_status=write_status)
    reviewed = _send_review(ctx, status="ready", outcome="deliver", action_id="a-review-blocked")
    assert reviewed.next_step == "continue", reviewed.error
    ledger = _ledger(ctx)
    assert ledger.payload["final_report"] is not None
    blocked = _finish(ctx)
    assert blocked.next_step == "error_recoverable"
    assert "unfinished write requests" in blocked.error
    assert ledger.payload["delivered"] is None

    ledger.payload["write_requests"][0]["status"] = "completed"
    ledger.save()
    finished = _finish(ctx)
    assert finished.next_step == "finish", finished.error
    assert _ledger(ctx).payload["status"] == "delivered"


def test_input_change_invalidates_ready_and_final_report(tmp_path):
    ctx, _ = _seed(tmp_path)
    assert _send_review(ctx, status="ready", outcome="deliver", action_id="a-review-old").next_step == "continue"
    ledger = _ledger(ctx)
    old_final = ledger.payload["final_report"]
    assert old_final is not None
    input_id = ledger.add_user_input("补充母公司口径说明。", source="user_message", source_message_id="m2")
    ledger.save()
    control = ledger.control_summary()
    assert control["review_status"] == "stale"
    blocked = _finish(ctx)
    assert blocked.next_step == "error_recoverable"
    assert "unapplied user inputs" in blocked.error

    # Apply the input, then explicitly re-review the rebuilt candidate.
    ledger = _ledger(ctx)
    ledger.input(input_id)["applied"] = True
    ledger.save()
    reviewed = _send_review(ctx, status="ready", outcome="deliver", action_id="a-review-new")
    assert reviewed.next_step == "continue", reviewed.error
    new_ledger = _ledger(ctx)
    assert new_ledger.payload["final_reports"][0]["status"] == "superseded"
    assert new_ledger.payload["candidate"]["candidate_id"] != old_final["candidate_id"] or new_ledger.payload["candidate"]["base_digest"] != old_final["base_digest"]
    assert new_ledger.payload["final_report"]["review_id"] != old_final["review_id"]
    assert _finish(ctx).next_step == "finish"


def test_delivered_resume_keeps_history_and_requires_new_review(tmp_path):
    ctx, _ = _seed(tmp_path)
    assert _send_review(ctx, status="ready", outcome="deliver", action_id="a-review-first").next_step == "continue"
    assert _finish(ctx).next_step == "finish"
    first = _ledger(ctx)
    assert first.payload["delivered"] is not None
    first_delivery = first.payload["delivered"]
    first_final = first.payload["final_report"]

    resumed = hooks.resume_report(ctx, SPAction.create(ActionType.REPLAN, reason="resume", task="resume", metadata={"report_action": "resume"}))
    assert resumed["status"] == "active"
    ledger = _ledger(ctx)
    assert ledger.payload["delivered"] is None
    assert len(ledger.payload["delivery_history"]) == 1
    assert ledger.payload["delivery_history"][0]["delivery_id"] == first_delivery["delivery_id"]
    assert ledger.payload["review"]["status"] == "stale"
    assert ledger.payload["final_report"] is None
    assert ledger.payload["final_reports"][-1]["version"] == first_final["version"]
    assert ledger.payload["drafts"][0]["is_current"] is True

    still_blocked = _finish(ctx)
    assert still_blocked.next_step == "error_recoverable"
    assert "no current ready integration review" in still_blocked.error
    assert _send_review(ctx, status="ready", outcome="deliver", action_id="a-review-resumed").next_step == "continue"
    assert _finish(ctx).next_step == "finish"
    ledger = _ledger(ctx)
    assert ledger.payload["status"] == "delivered"
    assert ledger.payload["delivered"]["delivery_id"] != first_delivery["delivery_id"]
    assert len(ledger.payload["delivery_history"]) == 1
    assert len(ledger.payload["final_reports"]) >= 2
