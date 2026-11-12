"""Navigation answers page/sort questions without delivering full source bodies."""

import asyncio
import json

import pytest
from test_collusion_html_candidate_delivery_v21 import Adapter
from test_collusion_html_candidate_navigation_v21 import index_fixture, query

from app.experiments.collusion_audit.m01_delivery import build_candidate_index_tool


def test_explicit_shared_character_sort_is_stable_and_keeps_original_rows(tmp_path):
    rows = index_fixture(tmp_path)
    rows[390]["non_template_shared_chars"] = 10
    rows[391]["non_template_shared_chars"] = 700
    rows[392]["non_template_shared_chars"] = 700
    path = tmp_path / "m01_candidates/index.json"
    path.write_text(json.dumps({"audit_run_id": "current-run", "candidates": rows}))
    before = path.read_bytes()
    result = query(tmp_path, document_pair="d007|d010", sort_by="shared_chars", limit=2)
    assert result["candidates"] == rows[391:393]
    assert result["sort_by"] == "shared_chars" and result["next_offset"] == 2
    assert result["navigation_only"] and not result["full_passages_delivered"]
    assert path.read_bytes() == before
    assert query(tmp_path, document_pair="d007|d010", limit=2)["candidates"] == rows[390:392]


def test_page_map_comes_from_actual_paired_source_identity_not_other_record(tmp_path):
    rows = index_fixture(tmp_path)
    root = tmp_path / "m01_candidates"
    evidence = [
        {"document_id": "d007", "page": 13, "physical_pages": [13, 14], "quote": "原文甲"},
        {"document_id": "d010", "page": 27, "physical_pages": [27], "quote": "原文乙"},
    ]
    record = {"candidate_id": rows[390]["candidate_id"], "candidate": {"text_a": "甲", "text_b": "乙"}, "evidence": evidence}
    path = root / (record["candidate_id"] + ".json")
    path.write_text(json.dumps(record, ensure_ascii=False))
    before = path.read_bytes()
    result = query(tmp_path, document_pair="d007|d010", limit=1)
    assert result["source_locations"][record["candidate_id"]] == [{"document_id": "d007", "page": 13, "physical_pages": [13, 14]}, {"document_id": "d010", "page": 27, "physical_pages": [27]}]
    assert result["candidates"] == rows[390:391]
    assert "原文甲" not in json.dumps(result, ensure_ascii=False)
    assert path.read_bytes() == before


def test_missing_source_file_is_explicitly_unavailable_not_invented(tmp_path):
    index_fixture(tmp_path)
    result = query(tmp_path, document_pair="d007|d010", limit=2)
    assert result["source_locations"] == {}
    assert result["source_location_unavailable_ids"] == [r["candidate_id"] for r in result["candidates"]]


def test_large_page_maps_keep_actual_cursor_and_bounded_whole_records(tmp_path):
    index_fixture(tmp_path)
    root = tmp_path / "m01_candidates"
    for number in range(391, 401):
        cid = f"C{number:06d}"
        (root / (cid + ".json")).write_text(json.dumps({"candidate_id": cid, "evidence": [{"document_id": "d007", "physical_pages": list(range(1, 700))}]}))
    result = query(tmp_path, document_pair="d007|d010", limit=10)
    assert 0 < len(result["candidates"]) < 10
    assert len(json.dumps(result, ensure_ascii=False)) <= 10000
    assert result["next_offset"] == len(result["candidates"])
    assert all(len(locations[0]["physical_pages"]) == 699 for locations in result["source_locations"].values())


def test_invalid_sort_not_silently_coerced(tmp_path):
    index_fixture(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(build_candidate_index_tool(Adapter(tmp_path)).ainvoke({"run_id": "current-run", "sort_by": "risk"}))
