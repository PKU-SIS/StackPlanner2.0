"""Stable-ID evidence reads preserve provenance and reject tampered paths/data."""

import json
from pathlib import Path

from langchain.tools import tool

from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.report.materials import build_fetch_material_tool
from deerflow.sp.report.read_material import build_read_material_tool
from deerflow.sp.report.store import ReportPaths


def seeded(tmp_path, text="First line\r\n第二行\r\nThird line\r\n"):
    ledger = ReportLedger.create(ReportPaths(tmp_path, "r1"), request_text="Research")
    ledger.begin_research_action("a1", [], allow_empty=True, kind="pre_research")
    ledger.save()

    @tool("web_fetch")
    def fetch(url: str) -> str:
        """Return a controlled evidence body."""
        return text

    build_fetch_material_tool(paths=ledger.paths, action_id="a1", gap_ids=[], fetch_tool=fetch).invoke({"url": "https://example.com/paper"})
    return ReportLedger.load(ledger.paths)


def test_reads_by_material_id_with_exact_source_and_line_numbers(tmp_path):
    ledger = seeded(tmp_path)
    reader = build_read_material_tool(paths=ledger.paths)
    result = json.loads(reader.invoke({"material_id": "m1", "start_line": 2, "end_line": 3}))
    assert result["material_id"] == "m1"
    assert result["source_id"] == "S1"
    assert result["lines"] == [2, 3]
    assert result["total_lines"] == 3
    assert result["content"] == "2: 第二行\n3: Third line"
    assert not result["content_truncated"]


def test_unknown_id_does_not_guess_a_path(tmp_path):
    ledger = seeded(tmp_path)
    result = json.loads(build_read_material_tool(paths=ledger.paths).invoke({"material_id": "m999"}))
    assert result["error"] == "unknown_material_id"


def test_hash_mismatch_and_escape_from_report_directory_are_rejected(tmp_path):
    ledger = seeded(tmp_path)
    material = ledger.material("m1")
    Path(material["path"]).write_text("tampered")
    reader = build_read_material_tool(paths=ledger.paths)
    assert json.loads(reader.invoke({"material_id": "m1"}))["error"] == "material_hash_mismatch"
    outside = tmp_path / "private.txt"
    outside.write_text("private")
    ledger.material("m1")["path"] = str(outside)
    ledger.save()
    assert json.loads(reader.invoke({"material_id": "m1"}))["error"] == "invalid_material_path"


def test_bounded_read_returns_honest_continuation_and_range_errors(tmp_path):
    ledger = seeded(tmp_path, "line\n" * 400)
    reader = build_read_material_tool(paths=ledger.paths)
    result = json.loads(reader.invoke({"material_id": "m1", "end_line": 400}))
    assert result["lines"] == [1, 200]
    assert result["next_start_line"] == 201
    assert result["content_truncated"]
    assert json.loads(reader.invoke({"material_id": "m1", "start_line": 401}))["error"] == "invalid_line_range"
