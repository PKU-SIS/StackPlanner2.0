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
    ledger.register_run("run1")
    ledger.save()
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


def test_unrelated_run_does_not_inherit_active_report_view(tmp_path):
    state, _ = _ledger(tmp_path)
    view = derive_report_view(state, run_status="running", run_id="ordinary-run")
    assert view["status"] == "not_report"


def test_known_report_run_gets_active_view(tmp_path):
    state, ledger = _ledger(tmp_path)
    ledger.begin_research_action("a1", [], allow_empty=True, run_id="report-run")
    ledger.save()
    view = derive_report_view(state, run_status="running", run_id="report-run")
    assert view["status"] == "running"


def test_writer_pipeline_view_is_read_only_and_scoped(tmp_path):
    state, ledger = _ledger(tmp_path)
    ledger.payload["nodes"] = {n: {"node_id": n, "kind": "writable", "title": title} for n, title in [("n1", "First"), ("n2", "Second"), ("n3", "Third")]}
    ledger.payload["tree"] = {"node_ids": ["n1", "n2", "n3"]}
    ledger.payload["writer_pipeline"] = {"managed": True, "max_concurrent": 4, "run_id": "run1"}
    ledger.payload["write_requests"] = [
        {"key": "old", "node_ids": ["n1"], "status": "superseded"},
        {"key": "w1", "node_ids": ["n1"], "status": "pending"},
        {"key": "w2", "node_ids": ["n2"], "status": "in_flight", "last_action_id": "writer-2", "last_run_id": "run1"},
        {"key": "w3", "node_ids": ["n3"], "status": "completed", "last_action_id": "writer-3", "last_run_id": "run1"},
    ]
    ledger.payload["drafts"] = [{"node_id": "n3", "is_current": True, "virtual_path": "/mnt/user-data/outputs/report/r1/drafts/n3/d1.md"}]
    ledger.save()
    before = ledger.paths.ledger.read_bytes()
    view = derive_report_view(state, run_status="running", run_id="run1")
    pipeline = view["writer_pipeline"]
    assert pipeline["max_concurrent"] == 4
    assert [n["status"] for n in pipeline["nodes"]] == ["pending", "in_flight", "completed"]
    assert pipeline["nodes"][1]["task_id"] == "writer-2"
    assert pipeline["nodes"][2]["draft_path"].startswith("/mnt/")
    assert "writer_pipeline" not in derive_report_view(state, run_status="running", run_id="other-run")
    assert derive_report_view(state, run_status="interrupted", run_id="run1")["writer_pipeline"]["nodes"][1]["status"] == "stopped"
    assert ledger.paths.ledger.read_bytes() == before
