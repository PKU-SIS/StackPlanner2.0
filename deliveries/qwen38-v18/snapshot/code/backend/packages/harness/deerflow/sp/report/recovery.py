"""Conservative reconciliation of stale report in-flight executions.

The ledger is authoritative business state, but a process restart or user stop
can leave a research/write attempt marked ``in_flight`` after its run has
already ended.  This module only releases an attempt when the RunManager
returns a positive terminal status; a missing/unknown run is left untouched.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from deerflow.sp.actions.events import make_sp_event
from deerflow.sp.report import store as report_store
from deerflow.sp.report import writing as report_writing
from deerflow.sp.report.ledger import ReportLedger, utc_now_iso

logger = logging.getLogger(__name__)

_ACTIVE_RUN_STATUSES = frozenset({"pending", "running", "queued", "streaming"})
_TERMINAL_RUN_STATUSES = frozenset({"success", "error", "interrupted", "cancelled", "canceled"})


async def reconcile_report_inflight(
    state: Mapping[str, Any] | None,
    run_manager: Any,
) -> list[dict[str, Any]]:
    """Release stale report attempts only on positive run termination evidence."""
    if run_manager is None or not isinstance(state, Mapping):
        return []
    context = report_store.load_report_context(state)
    if not context.is_active or context.outputs_path is None or not context.report_id:
        return []
    paths = report_store.ReportPaths(context.outputs_path, context.report_id)
    try:
        ledger = ReportLedger.load(paths)
    except Exception:  # noqa: BLE001 - recovery must not replace the real action
        logger.warning("Could not load report ledger for in-flight reconciliation", exc_info=True)
        return []

    targets: list[tuple[str, str, str | None]] = []
    for action_id, action in _research_actions(ledger):
        if str(action.get("status") or "") != "in_flight":
            continue
        run_id = str(action.get("run_id") or "").strip() or None
        if run_id:
            targets.append(("research", action_id, run_id))
    for request in report_writing.write_requests(ledger):
        if str(request.get("status") or "") != "in_flight":
            continue
        run_id = str(request.get("last_run_id") or "").strip()
        if not run_id:
            run_id = _latest_attempt_run_id(request)
        if run_id:
            targets.append(("writer", str(request.get("key") or ""), run_id))

    if not targets:
        return []

    resolved: list[tuple[str, str, str, str, str]] = []
    for kind, identifier, run_id in targets:
        status, error = await _run_status(run_manager, run_id)
        if status not in _TERMINAL_RUN_STATUSES:
            continue
        stop_reason = "user_stopped" if status in {"interrupted", "cancelled", "canceled"} else "technical_error"
        resolved.append((kind, identifier, run_id, stop_reason, error or status))
    if not resolved:
        return []

    events: list[dict[str, Any]] = []
    with report_store.report_lock(paths):
        ledger = ReportLedger.load(paths)
        changed = False
        for kind, identifier, run_id, stop_reason, detail in resolved:
            if kind == "research":
                action = ledger.action(identifier)
                if action is None or str(action.get("status") or "") != "in_flight":
                    continue
                error = f"previous {stop_reason.replace('_', ' ')} run {run_id}: {detail}"[:2000]
                ledger.fail_research_action(identifier, error, execution=stop_reason)
                events.append(
                    make_sp_event(
                        "sp.report.inflight_recovered",
                        action_id=identifier,
                        run_id=run_id,
                        target_agent="researcher",
                        stop_reason=stop_reason,
                    )
                )
                changed = True
                continue
            request = report_writing.write_request(ledger, identifier)
            if request is None or str(request.get("status") or "") != "in_flight":
                continue
            error = f"previous {stop_reason.replace('_', ' ')} run {run_id}: {detail}"[:2000]
            report_writing.fail_writer_attempt(
                ledger,
                key=identifier,
                action_id=str(request.get("last_action_id") or ""),
                error=error,
                stop_reason=stop_reason,
            )
            events.append(
                make_sp_event(
                    "sp.report.inflight_recovered",
                    action_id=str(request.get("last_action_id") or ""),
                    run_id=run_id,
                    target_agent="reporter",
                    stop_reason=stop_reason,
                )
            )
            changed = True
        if changed:
            ledger.payload["last_recovery_at"] = utc_now_iso()
            ledger.save()
    return events


def _research_actions(ledger: ReportLedger) -> list[tuple[str, Mapping[str, Any]]]:
    raw = ledger.payload.get("actions")
    if not isinstance(raw, Mapping):
        return []
    return [(str(action_id), action) for action_id, action in raw.items() if isinstance(action, Mapping)]


def _latest_attempt_run_id(request: Mapping[str, Any]) -> str | None:
    attempts = request.get("attempts")
    if not isinstance(attempts, list):
        return None
    for attempt in reversed(attempts):
        if isinstance(attempt, Mapping) and attempt.get("run_id"):
            return str(attempt["run_id"])
    return None


async def _run_status(run_manager: Any, run_id: str) -> tuple[str | None, str | None]:
    try:
        record = await run_manager.get(run_id)
    except Exception:  # noqa: BLE001 - unknown status remains unknown
        logger.warning("Could not resolve report run %s status", run_id, exc_info=True)
        return None, None
    if record is None:
        return None, None
    status = getattr(getattr(record, "status", None), "value", None)
    if not isinstance(status, str):
        status = str(getattr(record, "status", "") or "").strip().lower() or None
    error = getattr(record, "error", None)
    return status, str(error) if error else None
