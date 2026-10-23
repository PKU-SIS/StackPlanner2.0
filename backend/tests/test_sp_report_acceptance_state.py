"""State tests for fixed acceptance: one revision and technical exits."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_sp_report_p3 import _prepare_review, _seed, _send_review

from deerflow.sp.report import hooks
from deerflow.sp.report.acceptance import next_acceptance_action
from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.artifacts import SPArtifactAdapter
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus


def _tool_name(message) -> str:
    return str(message.tool_calls[0]["name"])


def _acceptance(ctx):
    return ReportLedger.load_for_context(hooks.report_context(ctx.state)).payload["acceptance"]


def test_fixed_review_issue_requests_one_revision_then_blocks_second_issue(tmp_path):
    ctx, ledger = _seed(tmp_path)
    first = _send_review(
        ctx,
        status="issues",
        outcome="revise",
        findings=[{"kind": "coverage", "severity": "blocking", "node_ids": ["n2"], "message": "缺少必要内容"}],
    )
    assert first.next_step == "continue"
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    ledger.payload["acceptance"]["review_count"] = 1
    ledger.save()

    revision = next_acceptance_action(ctx.state)
    assert revision is not None
    assert _tool_name(revision) == "sp_delegate"
    assert revision.tool_calls[0]["args"]["metadata"]["report_task"] == "revise"
    assert revision.tool_calls[0]["args"]["metadata"]["report_acceptance_origin"] == "harness"
    state = _acceptance(ctx)
    assert state["status"] == "revise_requested"
    assert state["content_revision_used"] is True

    # Simulate a completed one-round revise, then a second blocking review.
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    ledger.payload["acceptance"].update({"status": "revision_complete", "review_count": 1, "pending_action_id": None})
    ledger.save()
    second = _send_review(
        ctx,
        status="issues",
        outcome="revise",
        findings=[{"kind": "coverage", "severity": "blocking", "node_ids": ["n2"], "message": "仍有缺口"}],
    )
    assert second.next_step == "continue"

    ask = next_acceptance_action(ctx.state)
    assert ask is not None
    assert _tool_name(ask) == "sp_ask_human"
    state = _acceptance(ctx)
    assert state["status"] == "blocked_user"


def test_fixed_review_technical_error_sets_technical_exit_without_revision(tmp_path):
    ctx, ledger = _seed(tmp_path)
    action, task, entry = _prepare_review(ctx, action_id="a-review-timeout")
    result = SPSubagentResult(
        status=SPSubagentStatus.COMPLETED,
        result="",
        error="subagent timed out",
        artifact_content=None,
        artifact_metadata={"artifact_type": "integration_review", "report_task": "integrate_review"},
    )
    handled = hooks.handle_success(action, ctx, result, task, artifact_adapter=SPArtifactAdapter(), delegate_entry=entry)
    assert handled is not None
    assert "technical abort" in (handled.error or "")
    state = _acceptance(ctx)
    assert state["status"] == "technical_failed"
    assert state["review_count"] == 0
    assert state["content_revision_used"] is False
    assert next_acceptance_action(ctx.state) is None


def test_fixed_harness_review_rejects_local_edit_channel(tmp_path):
    ctx, ledger = _seed(tmp_path)
    action, task, entry = _prepare_review(ctx, action_id="a-review-edit")
    packet = task.context_refs["report_packet"]
    task.metadata["report_acceptance_origin"] = "harness"
    content = json.dumps(
        {
            "inputs_digest": packet["inputs_digest"],
            "reviewed_candidate_sha256": packet["candidate_sha256"],
            "review": {
                "status": "issues",
                "outcome": "edit",
                "findings": [{"kind": "structure", "severity": "advisory", "node_ids": ["n2"], "message": "minor"}],
                "edits": [{"draft_id": "d1", "kind": "format", "anchor": "Seed body", "replacement": "Changed", "reason": "minor"}],
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
        artifact_metadata={"artifact_type": "integration_review", "report_task": "integrate_review", "status": "issues"},
    )
    handled = hooks.handle_success(action, ctx, result, task, artifact_adapter=SPArtifactAdapter(), delegate_entry=entry)
    assert handled is not None
    assert handled.next_step == "error_recoverable"
    assert "supports ready/revise only" in (handled.error or "")
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.payload.get("review") is None


def test_fixed_review_cancel_sets_technical_exit(tmp_path):
    ctx, ledger = _seed(tmp_path)
    action, task, entry = _prepare_review(ctx, action_id="a-review-cancel")
    task.metadata["report_acceptance_origin"] = "harness"
    result = SPSubagentResult(
        status=SPSubagentStatus.CANCELLED,
        result="",
        error="cancelled",
        artifact_metadata={"artifact_type": "integration_review", "report_task": "integrate_review"},
    )
    hooks.handle_failure(action, ctx, result, task)
    state = _acceptance(ctx)
    assert state["status"] == "technical_failed"
    assert state["review_count"] == 0
    assert next_acceptance_action(ctx.state) is None


def test_plain_continue_does_not_reset_blocked_acceptance_but_explicit_change_does(tmp_path):
    ctx, ledger = _seed(tmp_path)
    ledger.payload["acceptance"] = {
        "status": "blocked_user",
        "review_count": 2,
        "content_revision_used": True,
        "technical_failure_count": 0,
        "pending_action_id": None,
        "input_count_at_block": len(ledger.inputs()),
    }
    ledger.save()
    ledger.add_user_input("继续", source="user_message", applied=True)
    ledger.save()
    assert next_acceptance_action(ctx.state) is None
    assert _acceptance(ctx)["status"] == "blocked_user"

    ledger.add_user_input("请调整第二部分的比较范围并补充材料", source="user_message", applied=True)
    ledger.save()
    # A new explicit input resets the old cycle; the subsequent gate may issue
    # a fresh review instead of silently resuming the blocked one.
    action = next_acceptance_action(ctx.state)
    assert action is not None
    assert _tool_name(action) == "sp_delegate"
