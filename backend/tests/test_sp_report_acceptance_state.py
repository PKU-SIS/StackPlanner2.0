"""Host-identity and failure-boundary tests for fixed acceptance."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_sp_report_p3 import _seed

from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.artifacts import SPArtifactAdapter
from deerflow.sp.report import hooks
from deerflow.sp.report.acceptance import next_acceptance_action
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus, SPSubagentTask


def _host_review(ctx: HandlerContext):
    message = next_acceptance_action(ctx.state)
    assert message is not None
    call = message.tool_calls[0]
    args = dict(call["args"])
    action_id = f"spact_{call['id']}"
    action = SPAction.create(
        ActionType.DELEGATE,
        action_id=action_id,
        reason=args["reason"],
        task=args["task"],
        target_agent=args["target_agent"],
        stage=args["stage"],
        expected_output=args.get("expected_output"),
        metadata=dict(args["metadata"]),
    )
    meta: dict = {}
    refs: dict = {}
    hooks.prepare_delegation(action, ctx, meta, refs)
    task = SPSubagentTask(
        action_id=action.action_id,
        subagent_type="outline",
        task=action.task or "review",
        description=action.reason,
        metadata=meta,
        context_refs=refs,
        stage=action.stage,
    )
    entry = ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="verification")
    return action, task, entry


def _review_result(task: SPSubagentTask, *, status: str, outcome: str, edits: list[dict] | None = None) -> SPSubagentResult:
    packet = task.context_refs["report_packet"]
    content = json.dumps(
        {
            "inputs_digest": packet["inputs_digest"],
            "reviewed_candidate_sha256": packet["candidate_sha256"],
            "review": {
                "status": status,
                "outcome": outcome,
                "findings": [],
                "edits": edits or [],
                "covered_requirement_ids": ["R1"],
            },
        },
        ensure_ascii=False,
    )
    return SPSubagentResult(
        status=SPSubagentStatus.COMPLETED,
        result="review",
        artifact_content=content,
        artifact_type="integration_review",
        artifact_metadata={"artifact_type": "integration_review", "report_task": "integrate_review", "status": status},
    )


def test_fixed_action_identity_rejects_empty_claim(tmp_path):
    ctx, _ = _seed(tmp_path)
    valid_action, _, _ = _host_review(ctx)
    action = SPAction.create(
        ActionType.DELEGATE,
        action_id=valid_action.action_id,
        reason="review",
        task="review",
        target_agent="outline",
        stage="verification",
        metadata={"report_task": "integrate_review", "report_acceptance_origin": "harness"},
    )
    with pytest.raises(ReportValidationError, match="must be registered by the host"):
        hooks.prepare_delegation(action, ctx, {}, {})


def test_fixed_review_edit_outcome_is_rejected(tmp_path):
    ctx, _ = _seed(tmp_path)
    action, task, entry = _host_review(ctx)
    result = _review_result(
        task,
        status="issues",
        outcome="edit",
        edits=[{"draft_id": "d1", "kind": "format", "anchor": "Seed body", "replacement": "Changed", "reason": "minor"}],
    )
    handled = hooks.handle_success(action, ctx, result, task, artifact_adapter=SPArtifactAdapter(), delegate_entry=entry)
    assert handled is not None
    assert handled.next_step == "error_recoverable"
    assert "supports ready/revise only" in (handled.error or "")
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.payload.get("review") is None


def test_fixed_review_cancel_sets_technical_exit_without_review(tmp_path):
    ctx, _ = _seed(tmp_path)
    action, task, _ = _host_review(ctx)
    task.metadata["report_acceptance_origin"] = "harness"
    task.metadata["report_task"] = "integrate_review"
    result = SPSubagentResult(
        status=SPSubagentStatus.CANCELLED,
        result="",
        error="cancelled",
        artifact_metadata={"artifact_type": "integration_review", "report_task": "integrate_review"},
    )
    hooks.handle_failure(action, ctx, result, task)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    acceptance = ledger.payload["acceptance"]
    assert acceptance["status"] == "technical_failed"
    assert acceptance["retry_kind"] == "review"
    assert acceptance["review_count"] == 0
    assert ledger.payload.get("review") is None
