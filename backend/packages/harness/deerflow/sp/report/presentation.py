"""Read-only projection of report state for the thread UI.

The report ledger remains the only persisted business state.  This module
derives a small, current view from that ledger plus the active run and pending
human interaction; it intentionally does not write a new ThreadState field.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.report.store import ReportContext, load_report_context

logger = logging.getLogger(__name__)

REPORT_VIEW_STATUSES = frozenset(
    {
        "running",
        "incomplete",
        "awaiting_user",
        "stopped",
        "technical_error",
        "delivered",
        "not_report",
    }
)


def derive_report_view(
    state: Mapping[str, Any] | None,
    *,
    run_status: str | None = None,
    run_id: str | None = None,
    stop_reason: str | None = None,
) -> dict[str, Any] | None:
    """Return the current report view, or ``None`` when no report is involved.

    ``run_status`` is the current/latest run status supplied by the Gateway.
    ``pending`` is read from the thread state so a successful worker run with a
    pending human-input card is not presented as a delivered report.
    """

    if not isinstance(state, Mapping):
        return None
    context = load_report_context(state)
    if context.status == "disabled":
        return None
    if context.status == "error":
        return {
            "status": "technical_error",
            "report_id": context.report_id,
            "run_id": run_id,
            "can_continue": False,
            "reason": str(context.error or "report ledger unavailable")[:1000],
        }

    try:
        ledger = ReportLedger.load_for_context(context)
    except Exception as exc:  # noqa: BLE001 - presentation must fail closed
        logger.warning("Unable to read report ledger for report view", exc_info=True)
        return {
            "status": "technical_error",
            "report_id": context.report_id,
            "run_id": run_id,
            "can_continue": False,
            "reason": f"report ledger cannot be read: {exc}"[:1000],
        }

    status = str(ledger.payload.get("status") or context.status).strip().lower()
    delivered = ledger.payload.get("delivered") if isinstance(ledger.payload.get("delivered"), Mapping) else None
    final = ledger.payload.get("final_report") if isinstance(ledger.payload.get("final_report"), Mapping) else None
    pending = state.get("sp_pending_human_interaction")
    run_normalized = str(run_status or "").strip().lower()

    if status == "delivered":
        delivery_run_id = str(delivered.get("run_id") or "") if delivered else ""
        # A delivered report belongs to the run that delivered it.  Do not let
        # an old delivery card reappear on a later ordinary task in this thread.
        if run_id and delivery_run_id and run_id != delivery_run_id:
            return _not_report_view(context)
        if delivered is None or final is None:
            return _view(
                context,
                "technical_error",
                run_id=run_id,
                can_continue=False,
                reason="report is marked delivered but has no current final/delivery record",
            )
        return _view(
            context,
            "delivered",
            run_id=run_id,
            can_continue=False,
            delivered=True,
            final_path=delivered.get("final_path") or final.get("virtual_path"),
            delivery_id=delivered.get("delivery_id"),
        )

    if status == "abandoned":
        return _not_report_view(context)

    # Active report work must belong to the current run.  A new ordinary chat
    # run can share the thread with an old active report; it must not inherit
    # that report's "running"/"incomplete" presentation.  When the association
    # is unknown, fail closed to not_report instead of guessing.
    known_runs = _report_run_ids(ledger)
    if not run_id or run_id not in known_runs:
        return _not_report_view(context)

    if isinstance(pending, Mapping):
        return _view(context, "awaiting_user", run_id=run_id, can_continue=True, reason=_pending_reason(pending))

    if run_normalized in {"pending", "running", "queued", "streaming"}:
        return _view(context, "running", run_id=run_id, can_continue=True)

    if run_normalized in {"interrupted", "cancelled", "canceled"}:
        return _view(
            context,
            "stopped",
            run_id=run_id,
            can_continue=True,
            reason=(stop_reason or "run stopped; the report remains resumable")[:1000],
        )

    if run_normalized in {"error", "failed"}:
        return _view(context, "technical_error", run_id=run_id, can_continue=True, reason="the current run ended with an error")

    # Active report with a finished (or unknown) current run: it is not running
    # now and it has not been delivered.  This is the important distinction
    # between a resumable incomplete report and a live execution.
    blockers = _safe_blockers(ledger)
    return _view(
        context,
        "incomplete",
        run_id=run_id,
        can_continue=True,
        reason=blockers,
    )


def _view(
    context: ReportContext,
    status: str,
    *,
    run_id: str | None,
    can_continue: bool,
    delivered: bool = False,
    reason: str | None = None,
    final_path: str | None = None,
    delivery_id: str | None = None,
) -> dict[str, Any]:
    view: dict[str, Any] = {
        "status": status,
        "report_id": context.report_id,
        "run_id": run_id,
        "can_continue": bool(can_continue),
    }
    if delivered:
        view["delivered"] = True
    if reason:
        view["reason"] = reason
    if final_path:
        view["final_path"] = final_path
    if delivery_id:
        view["delivery_id"] = delivery_id
    return view


def _not_report_view(context: ReportContext) -> dict[str, Any]:
    return {
        "status": "not_report",
        "report_id": context.report_id,
        "can_continue": False,
    }


def _pending_reason(pending: Mapping[str, Any]) -> str:
    question = str(pending.get("question") or "").strip()
    if question:
        return question[:1000]
    return "the report is waiting for user input"


def _safe_blockers(ledger: ReportLedger) -> str | None:
    try:
        reason = ledger.block_finish_reason()
    except Exception:  # noqa: BLE001 - presentation must remain available
        return None
    return str(reason)[:1000] if reason else None


def _report_run_ids(ledger: ReportLedger) -> set[str]:
    """Return run identities already associated with this report."""
    payload = ledger.payload
    runs: set[str] = set()
    raw_runs = payload.get("run_ids")
    if isinstance(raw_runs, list):
        runs.update(str(value) for value in raw_runs if isinstance(value, str) and value)
    for key in ("started_run_id",):
        value = payload.get(key)
        if isinstance(value, str) and value:
            runs.add(value)
    resume = payload.get("resume")
    if isinstance(resume, Mapping):
        value = resume.get("run_id")
        if isinstance(value, str) and value:
            runs.add(value)
    actions = payload.get("actions")
    if isinstance(actions, Mapping):
        for action in actions.values():
            if isinstance(action, Mapping):
                value = action.get("run_id")
                if isinstance(value, str) and value:
                    runs.add(value)
    requests = payload.get("write_requests")
    if isinstance(requests, list):
        for request in requests:
            if not isinstance(request, Mapping):
                continue
            value = request.get("last_run_id")
            if isinstance(value, str) and value:
                runs.add(value)
            attempts = request.get("attempts")
            if isinstance(attempts, list):
                for attempt in attempts:
                    if isinstance(attempt, Mapping):
                        attempt_run = attempt.get("run_id")
                        if isinstance(attempt_run, str) and attempt_run:
                            runs.add(attempt_run)
    return runs
