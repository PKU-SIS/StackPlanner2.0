"""Outline metrics distinguish accepted trees, evidence breadth and missing scope."""

import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("outline_eval", Path(__file__).resolve().parents[1] / "scripts/evaluate_report_outline.py")
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def test_metrics_expose_source_collapse_and_unmapped_requirements():
    tree = {"requirements": [{"id": "R1"}, {"id": "R2"}], "nodes": [{"node_id": "n1", "kind": "writable", "requirement_ids": ["R1"], "evidence": [{"material_id": "m1", "source_ids": ["S1"], "locator": {"lines": [1, 2]}}]}]}
    ledger = {"sources": [{"source_id": "S1"}], "materials": [{"material_id": "m1", "source_id": "S1", "lines": 10}]}
    result = evaluation.outline_metrics(tree, ledger, 50)
    assert result["outline_sources"] == 1
    assert result["summary_sources"] == 50
    assert result["unmapped_requirements"] == ["R2"]
    assert result["nodes_with_evidence"] == 1


def test_extra_source_ids_and_bad_lines_are_not_hidden_by_acceptance():
    tree = {"requirements": [], "nodes": [{"node_id": "n1", "kind": "writable", "evidence": [{"material_id": "m1", "source_ids": ["S1", "S99"], "locator": {"lines": [1, 20]}}]}]}
    ledger = {"sources": [{"source_id": "S1"}], "materials": [{"material_id": "m1", "source_id": "S1", "lines": 10}]}
    result = evaluation.outline_metrics(tree, ledger, 5)
    assert result["evidence_issues"]


def test_readable_outline_keeps_evidence_location_and_user_requirement():
    tree = {
        "report_title": "Report",
        "requirements": [{"id": "R1", "text": "User scope"}],
        "nodes": [
            {"node_id": "n1", "parent_id": None, "kind": "section", "title": "Section"},
            {
                "node_id": "n2",
                "parent_id": "n1",
                "kind": "writable",
                "title": "Evidence",
                "question": "Question?",
                "requirement_ids": ["R1"],
                "evidence": [{"material_id": "m3", "source_ids": ["S2"], "locator": {"lines": [4, 7]}}],
                "gap_ids": ["g1"],
            },
        ],
    }
    rendered = evaluation.render_outline(tree)
    assert "### Evidence（n2）" in rendered
    assert "S2/m3:L4–7" in rendered
    assert "User scope" in rendered
    assert "g1" in rendered


def test_clone_rebases_only_metadata_and_preserves_evidence(tmp_path):
    source = tmp_path / "source"
    report = source / "runtime/user-data/outputs/report/r1"
    report.mkdir(parents=True)
    request = report / "request.md"
    request.write_text("Original request")
    body = "# Real synthesis\nEvidence [S1]."
    summary = report / "research/a1/summary.md"
    summary.parent.mkdir(parents=True)
    summary.write_text(body)
    ledger = {"request": {"path": str(request)}, "tree": None, "materials": [], "summaries": [{"path": str(summary)}]}
    (report / "ledger.json").write_text(json.dumps(ledger))
    (source / "ledger.json").write_text(json.dumps(ledger))
    (source / "summary.md").write_text(body)
    (source / "metrics.json").write_text('{"summary_ok":true,"rich_citations":true}')
    destination = tmp_path / "clone/user-data"
    original = (report / "ledger.json").read_bytes()
    evaluation.clone_upstream(source, destination)
    copied = json.loads((destination / "outputs/report/r1/ledger.json").read_text())
    assert copied["request"]["path"].startswith(str(destination))
    assert Path(copied["summaries"][0]["path"]).read_text() == body
    assert (report / "ledger.json").read_bytes() == original
    (source / "metrics.json").write_text('{"summary_ok":false}')
    with pytest.raises(ValueError, match="blocked_upstream"):
        evaluation.clone_upstream(source, tmp_path / "bad")
