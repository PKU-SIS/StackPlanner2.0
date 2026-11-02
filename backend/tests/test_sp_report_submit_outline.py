"""Structured staging serializes once and never bypasses Outline acceptance."""

import json

from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.report.store import ReportPaths
from deerflow.sp.report.submit_outline import build_submit_outline_tool


def test_structured_tree_quotes_are_saved_without_committing_the_ledger(tmp_path):
    paths = ReportPaths(tmp_path, "r1")
    ReportLedger.create(paths, request_text="Research", inputs=[{"text": "Research"}])
    delivery = paths.directory / "outline/delivery-a1.json"
    proposal = {
        "report_title": '中文 “引号” and "quotes"',
        "requirements": [{"id": "R1", "input_id": "u1", "text": "Research", "hard": True}],
        "nodes": [{"node_id": "n1", "parent_id": None, "kind": "section", "title": 'Theory \\nu "Raman"'}],
        "removed_node_ids": [],
        "write_requests": [],
    }
    result = json.loads(build_submit_outline_tool(paths=paths, delivery_path=delivery).invoke({"tree": proposal}))
    assert result["saved"]
    assert result["validation_ok"]
    assert json.loads(delivery.read_text()) == proposal
    assert ReportLedger.load(paths).payload["tree"] is None


def test_invalid_proposal_is_retained_but_not_accepted(tmp_path):
    paths = ReportPaths(tmp_path, "r1")
    ReportLedger.create(paths, request_text="Research")
    delivery = paths.directory / "outline/delivery-a1.json"
    proposal = {"nodes": []}
    result = json.loads(build_submit_outline_tool(paths=paths, delivery_path=delivery).invoke({"tree": proposal}))
    assert result["error"] == "invalid_outline_tree"
    assert json.loads(delivery.read_text()) == proposal
    assert ReportLedger.load(paths).payload["tree"] is None


def test_source_correction_uses_material_identity_once_and_keeps_original():
    from deerflow.sp.report.submit_outline import normalize_evidence_sources

    class Ledger:
        payload = {"sources": [{"source_id": "S7"}]}

        def material(self, key):
            return {"source_id": "S7"} if key == "m8" else None

    original = {"nodes": [{"node_id": "n1", "evidence": [{"material_id": "m8", "source_ids": ["S8"], "locator": {"lines": [1, 3]}, "use": "unchanged"}, {"material_id": "unknown", "source_ids": ["S3"]}]}]}
    corrected, changes = normalize_evidence_sources(original, Ledger())
    assert corrected["nodes"][0]["evidence"][0]["source_ids"] == ["S7"]
    assert original["nodes"][0]["evidence"][0]["source_ids"] == ["S8"]
    assert corrected["nodes"][0]["evidence"][0]["locator"] == {"lines": [1, 3]}
    assert corrected["nodes"][0]["evidence"][1] == original["nodes"][0]["evidence"][1]
    assert len(changes) == 1
    assert normalize_evidence_sources(corrected, Ledger())[1] == []
