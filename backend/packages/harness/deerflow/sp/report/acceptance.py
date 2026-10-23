"""Fixed report acceptance gate.

The gate owns the deterministic hand-off from a complete report body to the
existing Outline review / revision and the existing finish delivery path.  It
does not call a model itself and it does not keep a second task ledger.
Progress is stored on the existing report ledger under a small ``acceptance``
object.

Important invariants:

* the gate dispatches an action before the action completes; dispatch and
  completion are different states;
* a technical failure keeps the same cycle and the same logical action so the
  existing idempotency/action-budget path can recover it;
* delivery is recognised only from the ledger's existing ``delivered`` record;
* a new acceptance cycle starts only after the existing explicit
  ``user_decisions`` record (``apply_to_plan``) is written for the new input.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

from deerflow.sp.report.ledger import ReportLedger, ReportValidationError


ACCEPTANCE_ORIGIN = "harness"
_ACCEPTANCE_DEFAULTS: dict[str, Any] = {
    "status": "idle",
    "cycle_id": 0,
    "review_round": 0,
    "review_count": 0,
    "last_review_id": None,
    "content_revision_used": False,
    "technical_failure_count": 0,
    "pending_action_id": None,
    "blocked_decision_count": None,
    "retry_kind": None,
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
        value = dict(_ACCEPTANCE_DEFAULTS)
        ledger.payload["acceptance"] = value
        ledger.save()
        return value
    for key, default in _ACCEPTANCE_DEFAULTS.items():
        value.setdefault(key, default)
    return value


def _save(ledger: ReportLedger) -> None:
    ledger.save()


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


def _delivered(ledger: ReportLedger) -> Mapping[str, Any] | None:
    delivered = ledger.payload.get("delivered")
    return delivered if isinstance(delivered, Mapping) else None


def _new_explicit_decision(ledger: ReportLedger, acceptance: Mapping[str, Any]) -> bool:
    """Use the existing explicit user-decision record, not text heuristics.

    Outline records a new user supplement as
    ``user_decisions[].action == "apply_to_plan"`` after it applies the input
    to the tree.  A normal question that never reaches that decision cannot
    restart acceptance.
    """
    decisions = ledger.payload.get("user_decisions")
    if not isinstance(decisions, list):
        return False
    baseline = int(acceptance.get("blocked_decision_count") or 0)
    for item in decisions[baseline:]:
        if isinstance(item, Mapping) and str(item.get("action") or "").strip().lower() == "apply_to_plan":
            return True
    return False


def _tool_call(tool_name: str, args: dict[str, Any], action_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": tool_name, "args": args, "id": action_id, "type": "tool_call"}],
    )


def _expected_action_id(action_id: str) -> str:
    return f"spact_{action_id}"


def _base_action_id(acceptance: Mapping[str, Any], kind: str) -> str:
    cycle = int(acceptance.get("cycle_id") or 0)
    round_number = int(acceptance.get("review_round") or 0)
    return f"sp-fixed-c{cycle}-{kind}-r{round_number}"


def _review_arguments(acceptance: Mapping[str, Any], action_id: str) -> dict[str, Any]:
    round_number = int(acceptance.get("review_round") or 0)
    cycle = int(acceptance.get("cycle_id") or 0)
    return {
        "target_agent": "outline",
        "task": (
            "Harness fixed minimum acceptance of the current assembled report candidate. "
            "Use the existing report_packet and existing integration-review JSON contract. "
            "Only major required-content omissions and placeholder replacement of required content are blocking; "
            "record other findings as advisory. Do not write files."
        ),
        "reason": f"Harness fixed acceptance review round {round_number}",
        "stage": "verification",
        "expected_output": "Existing integration-review JSON bound to the current candidate/inputs digest.",
        "metadata": {
            "report_task": "integrate_review",
            "report_acceptance_origin": ACCEPTANCE_ORIGIN,
            "report_acceptance_action_id": _expected_action_id(action_id),
            "report_acceptance_cycle": cycle,
            "report_acceptance_review_round": round_number,
        },
    }


def _revise_arguments(review: Mapping[str, Any], acceptance: Mapping[str, Any], action_id: str) -> dict[str, Any]:
    findings = review.get("findings") if isinstance(review.get("findings"), list) else []
    blocking = [
        dict(item)
        for item in findings
        if isinstance(item, Mapping) and str(item.get("severity") or "").strip().lower() == "blocking"
    ]
    if not blocking:
        blocking = [dict(item) for item in findings if isinstance(item, Mapping)]
    round_number = int(acceptance.get("review_round") or 0)
    cycle = int(acceptance.get("cycle_id") or 0)
    return {
        "target_agent": "outline",
        "task": (
            "Revise the accepted report tree only for the blocking findings from the harness minimum acceptance review. "
            "Preserve unaffected content; issue the existing new write requests for affected nodes. "
            "The complete blocking findings are below; the full review record is also in "
            "context_refs.report_packet.current_review. Review findings: "
            + json.dumps(blocking, ensure_ascii=False)
        ),
        "reason": f"Harness one-round content revision after review round {round_number}",
        "stage": "planning",
        "expected_output": "A revised Outline tree with existing new write requests for affected nodes.",
        "metadata": {
            "report_task": "revise",
            "report_acceptance_origin": ACCEPTANCE_ORIGIN,
            "report_acceptance_action_id": _expected_action_id(action_id),
            "report_acceptance_cycle": cycle,
            "report_acceptance_review_round": round_number,
            "report_acceptance_review_id": review.get("review_id"),
        },
    }


def _ask_user_arguments(review: Mapping[str, Any] | None) -> dict[str, Any]:
    findings = review.get("findings") if isinstance(review, Mapping) and isinstance(review.get("findings"), list) else []
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


def _start_review(ledger: ReportLedger, acceptance: dict[str, Any]) -> AIMessage:
    review_round = int(acceptance.get("review_count") or 0) + 1
    acceptance["review_round"] = review_round
    action_id = _base_action_id(acceptance, "review")
    acceptance["status"] = "review_requested"
    acceptance["retry_kind"] = None
    acceptance["pending_action_id"] = _expected_action_id(action_id)
    _save(ledger)
    return _tool_call("sp_delegate", _review_arguments(acceptance, action_id), action_id)


def _start_revision(ledger: ReportLedger, acceptance: dict[str, Any], review: Mapping[str, Any]) -> AIMessage:
    action_id = _base_action_id(acceptance, "revise")
    acceptance["status"] = "revise_requested"
    acceptance["retry_kind"] = None
    acceptance["pending_action_id"] = _expected_action_id(action_id)
    _save(ledger)
    return _tool_call("sp_delegate", _revise_arguments(review, acceptance, action_id), action_id)


def _request_finish(ledger: ReportLedger, acceptance: dict[str, Any], review: Mapping[str, Any]) -> AIMessage:
    review_id = str(review.get("review_id") or "current")
    action_id = f"{_base_action_id(acceptance, 'finish')}-{review_id}"
    acceptance["status"] = "finish_requested"
    acceptance["retry_kind"] = None
    acceptance["pending_action_id"] = _expected_action_id(action_id)
    _save(ledger)
    return _tool_call("sp_finish", {"summary": "报告最低验收已通过，交付当前最终报告。"}, action_id)


def _block_for_user(ledger: ReportLedger, acceptance: dict[str, Any], review: Mapping[str, Any] | None) -> AIMessage:
    review_id = str((review or {}).get("review_id") or "current")
    action_id = f"{_base_action_id(acceptance, 'ask')}-{review_id}"
    acceptance["status"] = "blocked_user"
    acceptance["blocked_decision_count"] = len(ledger.payload.get("user_decisions") or [])
    acceptance["pending_action_id"] = _expected_action_id(action_id)
    acceptance["retry_kind"] = None
    _save(ledger)
    return _tool_call("sp_ask_human", _ask_user_arguments(review), action_id)


def _start_new_cycle(ledger: ReportLedger, acceptance: dict[str, Any]) -> None:
    old_cycle = int(acceptance.get("cycle_id") or 0)
    reset = dict(_ACCEPTANCE_DEFAULTS)
    reset["cycle_id"] = old_cycle + 1
    ledger.payload["acceptance"] = reset
    _save(ledger)


def next_acceptance_action(state: Mapping[str, Any]) -> AIMessage | None:
    """Return the next deterministic harness action, if one is due."""
    ledger = _load_ledger(state)
    if ledger is None:
        return None
    acceptance = acceptance_state(ledger)
    status = str(acceptance.get("status") or "idle")

    if _delivered(ledger) is not None:
        if status != "delivered":
            acceptance["status"] = "delivered"
            acceptance["pending_action_id"] = None
            acceptance["retry_kind"] = None
            _save(ledger)
        return None

    if status == "cancelled":
        # Cancellation is terminal for this acceptance run.  Reuse the
        # existing run/cancel recovery path; the gate must not redispatch.
        return None

    if acceptance.get("blocked_decision_count") is not None:
        if _new_explicit_decision(ledger, acceptance):
            _start_new_cycle(ledger, acceptance)
            acceptance = acceptance_state(ledger)
            status = "idle"
        elif status == "blocked_user":
            return None

    last_result = state.get("sp_last_handler_result")
    if status in {"review_requested", "revise_requested"} and isinstance(last_result, Mapping) and last_result.get("error"):
        acceptance["status"] = "technical_failed"
        acceptance["retry_kind"] = "review" if status == "review_requested" else "revise"
        acceptance["technical_failure_count"] = int(acceptance.get("technical_failure_count") or 0) + 1
        _save(ledger)
        status = "technical_failed"

    review = _current_review(ledger)

    if status == "technical_failed":
        retry_kind = str(acceptance.get("retry_kind") or "")
        if retry_kind == "review":
            return _start_review(ledger, acceptance)
        if retry_kind == "revise" and review is not None and str(review.get("status") or "") == "issues":
            return _start_revision(ledger, acceptance, review)
        if retry_kind == "finish" and review is not None and _current_final(ledger) is not None:
            return _request_finish(ledger, acceptance, review)
        return None

    if review is not None:
        review_status = str(review.get("status") or "")
        if review_status == "ready":
            if _current_final(ledger) is None:
                return None
            return _request_finish(ledger, acceptance, review)
        if review_status == "issues":
            if int(acceptance.get("review_count") or 0) >= 2 or bool(acceptance.get("content_revision_used")):
                if status == "blocked_user":
                    return None
                return _block_for_user(ledger, acceptance, review)
            return _start_revision(ledger, acceptance, review)
        if review_status in {"edits_applied", "stale"}:
            return _block_for_user(ledger, acceptance, review)

    if status == "review_requested":
        return _start_review(ledger, acceptance)
    if status == "revise_requested":
        if review is not None and str(review.get("status") or "") == "issues":
            return _start_revision(ledger, acceptance, review)
        return None
    if status == "finish_requested":
        if review is not None and _current_final(ledger) is not None:
            return _request_finish(ledger, acceptance, review)
        return None

    if not _body_ready(ledger):
        return None
    if int(acceptance.get("review_count") or 0) >= 2:
        return _block_for_user(ledger, acceptance, review)
    return _start_review(ledger, acceptance)
