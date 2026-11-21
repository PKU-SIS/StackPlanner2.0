"""Reserve draft IDs before concurrent writers complete; disambiguate old IDs."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from test_report_writer_pipeline import seeded
from test_sp_report_writing import _writer_action

from deerflow.sp.report import store, writing
from deerflow.sp.report.hooks import report_context
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError


def test_parallel_dispatch_reserves_ids_and_failed_ids_are_not_reused(tmp_path):
    ctx = seeded(tmp_path, 8)
    barrier = Barrier(8)

    def reserve(index):
        _, task, _ = _writer_action(ctx, f"w{index}", action_id=f"writer-{index}")
        barrier.wait(timeout=10)
        return task.metadata["report_draft_id"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(reserve, range(8)))
    assert len(set(ids)) == 8
    paths = ReportLedger.load_for_context(report_context(ctx.state)).paths
    with store.report_lock(paths):
        ledger = ReportLedger.load(paths)
        writing.fail_writer_attempt(ledger, key="w0", action_id="writer-0", error="test failure")
        ledger.save()
    _, task, _ = _writer_action(ctx, "w0", action_id="writer-retry")
    assert task.metadata["report_draft_id"] not in ids
    assert int(task.metadata["report_draft_id"][1:]) > 8


def legacy(tmp_path):
    ledger = ReportLedger.create(store.ReportPaths(tmp_path, "r1"), request_text="report")
    ledger.payload["drafts"] = [{"draft_id": "d1", "node_id": n, "is_current": True, "review_status": "open", "applicable_fingerprint": "fp"} for n in ("n2", "n3")]
    return ledger


def test_duplicate_old_ids_require_node_identity(tmp_path):
    ledger = legacy(tmp_path)
    with pytest.raises(ReportValidationError, match="ambiguous"):
        writing.draft(ledger, "d1")
    assert writing.draft(ledger, "d1", node_id="n3")["node_id"] == "n3"
    assert writing.draft(ledger, "d1", node_id="n2")["node_id"] == "n2"


def test_legacy_dispositions_do_not_merge_different_nodes(tmp_path):
    ledger = legacy(tmp_path)
    tree = {"nodes": [{"node_id": n, "kind": "writable"} for n in ("n2", "n3")], "draft_dispositions": [{"draft_id": "d1", "node_id": "n2", "action": "keep", "reason": "retain"}]}
    with pytest.raises(ReportValidationError, match="draft dispositions required"):
        writing.apply_draft_dispositions(ledger, tree, {"n2": "fp", "n3": "fp"})
    assert all(d["review_status"] == "open" for d in ledger.payload["drafts"])
    tree["draft_dispositions"].append({"draft_id": "d1", "node_id": "n3", "action": "keep", "reason": "retain"})
    applied = writing.apply_draft_dispositions(ledger, tree, {"n2": "fp", "n3": "fp"})
    assert {d["node_id"] for d in applied} == {"n2", "n3"}
    assert [d["draft_id"] for d in ledger.payload["drafts"]] == ["d1", "d1"]
