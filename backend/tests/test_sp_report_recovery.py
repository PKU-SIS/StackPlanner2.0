"""Conservative report in-flight reconciliation tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from deerflow.sp.report import store as report_store
from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.report.recovery import reconcile_report_inflight
from deerflow.sp.report.writing import next_action


def _state(tmp_path: Path) -> dict:
    return {"thread_data": {"outputs_path": str(tmp_path)}, "messages": []}


def _ledger(tmp_path: Path, ledger: ReportLedger) -> dict:
    report_store.write_json_atomic(report_store.pointer_path(tmp_path), {"report_id": ledger.paths.report_id})
    return _state(tmp_path)


class _Manager:
    def __init__(self, status: str | None) -> None:
        self.status = status

    async def get(self, run_id: str):
        if self.status is None:
            return None
        return SimpleNamespace(status=SimpleNamespace(value=self.status), error="recorded error")


def test_terminal_old_run_releases_research_without_charging_round(tmp_path):
    paths = report_store.ReportPaths(tmp_path, "r1")
    ledger = ReportLedger.create(paths, request_text="request")
    ledger.begin_research_action("a1", [], allow_empty=True, run_id="old-run")
    ledger.save()
    state = _ledger(tmp_path, ledger)

    events = asyncio.run(reconcile_report_inflight(state, _Manager("success")))
    assert events
    ledger = ReportLedger.load(paths)
    action = ledger.action("a1")
    assert action["status"] == "failed"
    assert action["stop_reason"] == "technical_error"


def test_unknown_run_is_not_treated_as_failed(tmp_path):
    paths = report_store.ReportPaths(tmp_path, "r1")
    ledger = ReportLedger.create(paths, request_text="request")
    ledger.begin_research_action("a1", [], allow_empty=True, run_id="unknown-run")
    ledger.save()
    state = _ledger(tmp_path, ledger)

    assert asyncio.run(reconcile_report_inflight(state, _Manager(None))) == []
    assert ReportLedger.load(paths).action("a1")["status"] == "in_flight"


def test_user_stopped_writer_becomes_retryable_technical_failed(tmp_path):
    paths = report_store.ReportPaths(tmp_path, "r1")
    ledger = ReportLedger.create(paths, request_text="request")
    ledger.payload["write_requests"] = [
        {
            "key": "w1",
            "node_ids": ["n1"],
            "feedback_wanted": False,
            "status": "in_flight",
            "round": 1,
            "attempts": [{"action_id": "a1", "run_id": "old-run", "status": "in_flight"}],
            "last_action_id": "a1",
            "last_run_id": "old-run",
        }
    ]
    ledger.save()
    state = _ledger(tmp_path, ledger)

    events = asyncio.run(reconcile_report_inflight(state, _Manager("interrupted")))
    assert events
    ledger = ReportLedger.load(paths)
    request = ledger.payload["write_requests"][0]
    assert request["status"] == "technical_failed"
    assert request["last_stop_reason"] == "user_stopped"
    assert next_action(request) == "delegate_writer"
