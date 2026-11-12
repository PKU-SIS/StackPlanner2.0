"""Small collections return once; large rows remain lossless with honest cursors."""

import json

import pytest
from test_collusion_audit import scoped_profile as scoped_profile

from app.experiments.collusion_audit.bounded_records import read_bounded


def reader(rows):
    def page(run_id, collection, offset, limit):
        assert limit <= 20
        items = rows[offset : offset + limit]
        return {"run_id": run_id, "collection": collection, "offset": offset, "total": len(rows), "items": items, "next_offset": offset + len(items) if offset + len(items) < len(rows) else None}

    return page


def test_all_43_small_records_in_one_model_visible_response():
    rows = [{"id": i, "text": "原始限价观察"} for i in range(43)]
    result = read_bounded(reader(rows), "r", "budgets", 0, 43)
    assert result["items"] == rows and result["next_offset"] is None
    assert result["total"] == 43 and result["returned_count"] == 43


def test_character_budget_never_truncates_a_record_and_continues_exactly():
    rows = [{"id": i, "text": str(i) * 1200} for i in range(8)]
    all_rows = []
    offset = 0
    while offset is not None:
        result = read_bounded(reader(rows), "r", "sources", offset, 200, max_chars=4000)
        assert len(json.dumps(result, ensure_ascii=False)) <= 4000
        all_rows.extend(result["items"])
        offset = result["next_offset"]
    assert all_rows == rows


def test_giant_single_record_reports_read_file_path_instead_of_cutting_text():
    with pytest.raises(ValueError, match="read_file"):
        read_bounded(reader([{"text": "原文" * 6000}]), "r", "sources", 0, 1, max_chars=4000)


@pytest.mark.parametrize("offset,limit", [(-1, 20), (True, 20), (0, 0), (0, 201), (0, True)])
def test_invalid_cursor_or_limit_rejected(offset, limit):
    with pytest.raises(ValueError):
        read_bounded(reader([]), "r", "sources", offset, limit)


@pytest.mark.asyncio
async def test_actual_scoped_tool_quality_mode_reads_43_and_legacy_stays_20(scoped_profile):
    adapter = scoped_profile.adapters
    run_id = "M01_20260101T000000_abcdef1234"
    adapter.runs = [{"run_id": run_id}]
    root = adapter.result_root(run_id)
    root.mkdir(parents=True)
    (root / "result.json").write_text(json.dumps({"run_id": run_id, "collections": {"findings": {"count": 43, "file": "findings.jsonl"}}}))
    rows = [{"id": i, "text": "原始代码记录"} for i in range(43)]
    (root / "findings.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    adapter.quality_controls = True
    adapter.rule_level_review = True
    tool = next(t for t in adapter.build() if t.name == "audit_read_result")
    result = json.loads(await tool.ainvoke({"run_id": run_id, "collection": "findings", "offset": 0, "limit": 43}))
    assert result["items"] == rows and result["next_offset"] is None
    adapter.quality_controls = False
    legacy = json.loads(await tool.ainvoke({"run_id": run_id, "collection": "findings", "offset": 0, "limit": 43}))
    assert "error" in legacy and "20" in json.dumps(legacy)
