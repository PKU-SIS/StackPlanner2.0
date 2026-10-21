"""Read-side report view derives run/pending/delivery without persistence."""

from __future__ import annotations

from pathlib import Path

from deerflow.sp.report import store as report_store
from deerflow.sp.report.ledger import ReportLedger, utc_now_iso
from deerflow.sp.report.presentation import derive_report_view


def _state(tmp_path: Path) -> dict:
    return {"thread_data": {"outputs_path": str(tmp_path)}, "messages": []}


def _ledger(tmp_path: Path) -> tuple[dict, ReportLedger]:
    paths = report_store.ReportPaths(tmp_path, "r1")
    ledger = ReportLedger.create(paths, request_text="report request")
    report_store.write_json_atomic(report_store.pointer_path(tmp_path), {"report_id": "r1"})
    return _state(tmp_path), ledger


def test_active_finished_run_is_incomplete_not_running(tmp_path):
    state, _ = _ledger(tmp_path)
    view = derive_report_view(state, run_status="success", run_id="run1")
    assert view is not None
    assert view["status"] == "incomplete"
    assert view["can_continue"] is True


def test_active_running_and_pending(tmp_path):
    state, _ = _ledger(tmp_path)
    assert derive_report_view(state, run_status="running", run_id="run1")["status"] == "running"
    pending_state = {**state, "sp_pending_human_interaction": {"interaction_id": "i1", "question": "Need input"}}
    assert derive_report_view(pending_state, run_status="success", run_id="run1")["status"] == "awaiting_user"


def test_interrupted_and_error_are_distinct(tmp_path):
    state, _ = _ledger(tmp_path)
    stopped = derive_report_view(state, run_status="interrupted", run_id="run1", stop_reason="user stopped")
    assert stopped["status"] == "stopped"
    assert stopped["can_continue"] is True
    failed = derive_report_view(state, run_status="error", run_id="run1")
    assert failed["status"] == "technical_error"


def test_delivered_is_scoped_to_delivery_run(tmp_path):
    state, ledger = _ledger(tmp_path)
    ledger.payload["status"] = "delivered"
    ledger.payload["delivered"] = {"delivery_id": "dlv1", "run_id": "run-delivery", "final_path": "/mnt/user-data/outputs/report/r1/final/report-1.md"}
    ledger.payload["final_report"] = {"virtual_path": "/mnt/user-data/outputs/report/r1/final/report-1.md", "is_current": True}
    ledger.save()

    assert derive_report_view(state, run_status="success", run_id="run-delivery")["status"] == "delivered"
    assert derive_report_view(state, run_status="success", run_id="run-ordinary")["status"] == "not_report"


def test_historical_technical_attempt_does_not_override_current_active(tmp_path):
    state, ledger = _ledger(tmp_path)
    ledger.payload["write_requests"] = [
        {
            "key": "w1",
            "node_ids": ["n1"],
            "status": "completed",
            "attempts": [{"action_id": "a-old", "status": "technical_failed", "finished_at": utc_now_iso()}],
        }
    ]
    ledger.save()
    view = derive_report_view(state, run_status="success", run_id="run1")
    assert view["status"] == "incomplete"
    assert view["status"] != "technical_error"
