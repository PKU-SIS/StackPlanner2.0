"""Fixed report acceptance gate.

This module owns only the deterministic hand-off from "the report body is
complete" to the existing Outline review / revision / finish actions.  It does
not call a model itself and it does not keep a second task ledger; progress is
stored on the existing report ledger under a small ``acceptance`` object.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

from deerflow.sp.report.ledger import ReportLedger, ReportValidationError


ACCEPTANCE_ORIGIN = "harness"
_CONTINUATION_ONLY_RE = re.compile(
    r"^\s*(?:继续|接着|继续执行|继续吧|继续做|go\s+on|continue|resume|ok(?:ay)?|好的|收到|嗯)[\s。.!！?？]*$",
    re.IGNORECASE,
)
_ACCEPTANCE_DEFAULTS: dict[str, Any] = {
    "status": "idle",
    "review_count": 0,
    "content_revision_used": False,
    "technical_failure_count": 0,
    "pending_action_id": None,
    "input_count_at_block": None,
}


def _report_context(state: Mapping[str, Any]):
    from deerflow.sp.report.hooks import report_context

    return report_context(state)


def _load_ledger(state: Mapping[str, Any]) -> ReportLedger | None:
    report = _report_context(state)
    if not report.is_active or not report.outputs_path or not report.report_id:
        return None
    try:
        return ReportLedger.load_for_context(report)
    except (FileNotFoundError, ReportValidationError, ValueError, OSError):
        return None


def acceptance_state(ledger: ReportLedger) -> dict[str, Any]:
    """Return the ledger's acceptance projection, creating defaults once."""
    value = ledger.payload.get("acceptance")
    if not isinstance(value, dict):
        value = {**_ACCEPTANCE_DEFAULTS, "input_count_at_block": None}
        ledger.payload["acceptance"] = value
        ledger.save()
        return value
    for key, default in _ACCEPTANCE_DEFAULTS.items():
        value.setdefault(key, default)
    return value


def _save(ledger: ReportLedger) -> None:
    ledger.save()


def _has_new_explicit_input(ledger: ReportLedger, acceptance: Mapping[str, Any]) -> bool:
    inputs = ledger.inputs()
    previous = int(acceptance.get("input_count_at_block") or 0)
    new_inputs = inputs[previous:]
    if not new_inputs:
        return False
    return any(not _CONTINUATION_ONLY_RE.match(str(item.get("preview") or "")) for item in new_inputs)


def _body_ready(ledger: ReportLedger) -> bool:
    """Check body completeness without treating an empty writer queue as enough."""
    try:
        summary = ledger.control_summary()
    except (ReportValidationError, ValueError, OSError):
        return False
    if summary.get("gaps") or summary.get("unapplied_input_ids") or summary.get("research_actions_in_flight"):
        return False
    if summary.get("draft_review_due") or summary.get("feedback_open") or summary.get("outline_review_due"):
        return False
    write_requests = summary.get("write_requests") or []
    if any(str(item.get("status") or "") not in {"completed", "superseded"} for item in write_requests):
        return False
    tree = ledger.payload.get("tree")
    if not isinstance(tree, Mapping):
        return False
    try:
        tree_payload = json.loads(Path(str(tree.get("path") or "")).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    current_nodes = {
        str(item.get("node_id"))
        for item in ledger.payload.get("drafts") or []
        if isinstance(item, Mapping) and item.get("is_current") is True
    }
    writable = {
        str(node.get("node_id"))
        for node in tree_payload.get("nodes") or []
        if isinstance(node, Mapping) and str(node.get("kind") or "") == "writable"
    }
    return bool(writable) and writable <= current_nodes


def _current_review(ledger: ReportLedger):
    from deerflow.sp.report import assembly as report_assembly

    try:
        if report_assembly.review_binding_error(ledger, require_ready=False) is not None:
            return None
    except (ReportValidationError, ValueError, OSError):
        return None
    review = ledger.payload.get("review")
    return review if isinstance(review, Mapping) else None


def _current_final(ledger: ReportLedger):
    from deerflow.sp.report import assembly as report_assembly

    try:
        if report_assembly.final_binding_error(ledger) is not None:
            return None
    except (ReportValidationError, ValueError, OSError):
        return None
    final = ledger.payload.get("final_report")
    return final if isinstance(final, Mapping) else None


def _tool_call(tool_name: str, args: dict[str, Any], action_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": tool_name, "args": args, "id": action_id, "type": "tool_call"}],
    )


def _review_arguments(review_count: int, action_id: str) -> dict[str, Any]:
    return {
        "target_agent": "outline",
        "task": (
            "Harness fixed minimum acceptance of the current assembled report candidate. "
            "Use the existing report_packet and existing integration-review JSON contract. "
            "Only major required-content omissions and placeholder replacement of required content are blocking; "
            "record other findings as advisory. Do not write files."
        ),
        "reason": f"Harness fixed acceptance review #{review_count}",
        "stage": "verification",
        "expected_output": "Existing integration-review JSON bound to the current candidate/inputs digest.",
        "metadata": {
            "report_task": "integrate_review",
            "report_acceptance_origin": ACCEPTANCE_ORIGIN,
            "report_acceptance_action_id": f"spact_{action_id}",
        },
    }


def _revise_arguments(review: Mapping[str, Any], action_id: str) -> dict[str, Any]:
    findings = review.get("findings") if isinstance(review.get("findings"), list) else []
    compact = []
    for item in findings[:8]:
        if not isinstance(item, Mapping):
            continue
        compact.append(
            {
                "kind": item.get("kind"),
                "severity": item.get("severity"),
                "node_ids": item.get("node_ids") or [],
                "message": str(item.get("message") or "")[:600],
            }
        )
    return {
        "target_agent": "outline",
        "task": (
            "Revise the accepted report tree only for the blocking findings from the harness minimum acceptance review. "
            "Preserve unaffected content; issue the existing new write requests for affected nodes. "
            "Review findings: "
            + str(compact)
        ),
        "reason": "Harness one-round content revision after minimum acceptance findings",
        "stage": "planning",
        "expected_output": "A revised Outline tree with existing new write requests for affected nodes.",
        "metadata": {
            "report_task": "revise",
            "report_acceptance_origin": ACCEPTANCE_ORIGIN,
            "report_acceptance_action_id": f"spact_{action_id}",
        },
    }


def _ask_user_arguments(review: Mapping[str, Any]) -> dict[str, Any]:
    findings = review.get("findings") if isinstance(review.get("findings"), list) else []
    messages = [str(item.get("message") or "").strip() for item in findings if isinstance(item, Mapping)]
    messages = [item for item in messages if item]
    question = (
        "最低验收在完成一次自动内容返修后仍发现必要问题，是否接受当前结果、调整范围，或提供进一步材料？"
        if not messages
        else "最低验收仍发现必要问题：" + "；".join(messages[:3]) + "。是否接受当前结果、调整范围，或提供进一步材料？"
    )
    return {
        "question": question,
        "reason": "Harness minimum acceptance blocked after one content revision",
        "stage": "verification",
    }


def next_acceptance_action(state: Mapping[str, Any]) -> AIMessage | None:
    """Return the next deterministic harness action, if one is due.

    The returned message is a synthetic control-tool call.  The existing
    ``SPControlActionMiddleware`` therefore performs the same delegation,
    idempotency and event handling as any other SP action.
    """
    ledger = _load_ledger(state)
    if ledger is None:
        return None
    acceptance = acceptance_state(ledger)
    status = str(acceptance.get("status") or "idle")
    inputs = ledger.inputs()

    if status == "blocked_user":
        if _has_new_explicit_input(ledger, acceptance):
            # Explicit new user input starts a new task/acceptance cycle.  A
            # mere "continue" or technical recovery does not reset the cycle.
            reset = {**_ACCEPTANCE_DEFAULTS, "input_count_at_block": len(inputs)}
            ledger.payload["acceptance"] = reset
            _save(ledger)
            acceptance = reset
            status = "idle"
        else:
            return None
    last_result = state.get("sp_last_handler_result")
    if status in {"requested", "revise_requested"} and isinstance(last_result, Mapping) and last_result.get("error"):
        acceptance["status"] = "technical_failed"
        acceptance["technical_failure_count"] = int(acceptance.get("technical_failure_count") or 0) + 1
        _save(ledger)
        return None
    if status in {"technical_failed", "revise_requested", "passed"}:
        return None

    review = _current_review(ledger)
    if review is not None:
        review_status = str(review.get("status") or "")
        if review_status == "ready":
            final = _current_final(ledger)
            if final is None:
                return None
            if isinstance(ledger.payload.get("delivered"), Mapping):
                return None
            acceptance["status"] = "passed"
            action_id = f"sp-fixed-finish-{review.get('review_id') or 'current'}"
            acceptance["pending_action_id"] = action_id
            _save(ledger)
            return _tool_call("sp_finish", {"summary": "报告最低验收已通过，交付当前最终报告。"}, action_id)
        if review_status == "issues":
            count = int(acceptance.get("review_count") or 0)
            if count >= 2 or bool(acceptance.get("content_revision_used")):
                acceptance["status"] = "blocked_user"
                acceptance["input_count_at_block"] = len(inputs)
                _save(ledger)
                action_id = f"sp-fixed-ask-{review.get('review_id') or 'current'}"
                acceptance["pending_action_id"] = action_id
                _save(ledger)
                return _tool_call("sp_ask_human", _ask_user_arguments(review), action_id)
            acceptance["status"] = "revise_requested"
            acceptance["content_revision_used"] = True
            action_id = f"sp-fixed-revise-{review.get('review_id') or 'current'}"
            acceptance["pending_action_id"] = f"spact_{action_id}"
            _save(ledger)
            return _tool_call("sp_delegate", _revise_arguments(review, action_id), action_id)
        if review_status in {"edits_applied", "stale"}:
            # The fixed acceptance path has no edits→re-review loop.  A stale
            # candidate created by a non-fixed legacy operation is returned to
            # the user rather than silently entering a second review cycle.
            acceptance["status"] = "blocked_user"
            acceptance["input_count_at_block"] = len(inputs)
            _save(ledger)
            return _tool_call("sp_ask_human", _ask_user_arguments(review), f"sp-fixed-ask-{review.get('review_id') or 'current'}")

    if status == "requested" and review is None:
        # The previous fixed review did not produce a review record.  Do not
        # auto-retry it; the action error is already available to the agent/user.
        return None
    if status == "revise_requested":
        return None
    if not _body_ready(ledger):
        return None
    count = int(acceptance.get("review_count") or 0)
    if count >= 2:
        acceptance["status"] = "blocked_user"
        acceptance["input_count_at_block"] = len(inputs)
        _save(ledger)
        return _tool_call("sp_ask_human", _ask_user_arguments({}), "sp-fixed-ask-no-review")
    acceptance["status"] = "requested"
    acceptance["review_count"] = count + 1
    action_id = f"sp-fixed-review-{int(acceptance['review_count'])}"
    acceptance["pending_action_id"] = f"spact_{action_id}"
    _save(ledger)
    return _tool_call("sp_delegate", _review_arguments(int(acceptance["review_count"]), action_id), action_id)
