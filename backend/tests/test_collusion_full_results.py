"""Full code facts are lossless, complete and independent of20-row pagination."""

import json

import pytest

from app.experiments.collusion_audit.full_results import pack_complete_result, unpack_complete_result


def fixture(tmp_path):
    rows = [{"requirement": f"requirement{i}", "response": f"response{i}", "bidder": "A" if i < 25 else "B", "round": "1", "evidence_refs": [f"E{i}"]} for i in range(53)]
    evidence = [{"evidence_id": f"E{i}", "document_id": "d1", "path": "original.pdf", "sha256": "sourcehash", "bidder": "A", "round": "1", "page": 3, "quote": f"response{i}", "extra": {"$ref": "literal source text"}} for i in range(53)]
    evidence[1]["nullable"] = None
    result = {
        "rule_id": "M04",
        "run_id": "M04_20261107T000000_0123456789",
        "status": "partial",
        "collections": {"review_rows": {"file": "review_rows.jsonl", "count": 53}, "evidence": {"file": "evidence.jsonl", "count": 53}, "findings": {"file": "findings.jsonl", "count": 0}},
    }
    raw = {"review_rows": rows, "evidence": evidence, "findings": []}
    for key, values in raw.items():
        (tmp_path / (key + ".jsonl")).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in values))
    (tmp_path / "result.json").write_text(json.dumps(result))
    (tmp_path / "sources.json").write_text(json.dumps({"sources": ["original.pdf"]}))
    return result, raw


def test_complete_packet_roundtrips_all53_rows_and_all_source_fields(tmp_path):
    result, raw = fixture(tmp_path)
    packet = pack_complete_result(tmp_path)
    decoded = unpack_complete_result(packet)
    assert packet["complete"] is True
    assert decoded["summary"] == result
    assert decoded["collections"] == raw
    assert len(decoded["collections"]["review_rows"]) == 53
    assert decoded["sources"] == {"sources": ["original.pdf"]}
    assert len(json.dumps(packet, ensure_ascii=False)) < len(json.dumps(decoded, ensure_ascii=False))


def test_missing_rows_or_foreign_path_never_become_complete_packet(tmp_path):
    result, raw = fixture(tmp_path)
    (tmp_path / "review_rows.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="count"):
        pack_complete_result(tmp_path)
    result["collections"]["review_rows"]["file"] = "../other.jsonl"
    (tmp_path / "result.json").write_text(json.dumps(result))
    with pytest.raises(ValueError, match="scope"):
        pack_complete_result(tmp_path)


def test_grouping_preserves_json_scalar_types_and_original_record_order(tmp_path):
    result, raw = fixture(tmp_path)
    values = [{"page": True, "quote": "first"}, {"page": 1, "quote": "second"}, {"page": 1.0, "quote": "third"}, {"quote": "missing"}, {"page": None, "quote": "null"}]
    result["collections"]["evidence"]["count"] = len(values)
    (tmp_path / "result.json").write_text(json.dumps(result))
    (tmp_path / "evidence.jsonl").write_text("".join(json.dumps(v) + "\n" for v in values))
    decoded = unpack_complete_result(pack_complete_result(tmp_path))["collections"]["evidence"]
    assert json.dumps(decoded, sort_keys=True) == json.dumps(values, sort_keys=True)


def test_document_metadata_is_shared_across_distinct_page_and_table_positions(tmp_path):
    rows = [
        {
            "evidence_id": f"E{i:06d}",
            "document_id": "d001",
            "path": "original-project-folder/" * 8 + "original.pdf",
            "sha256": "a" * 64,
            "bidder": "Original bidder",
            "round": "1",
            "lot": "1",
            "page": i + 1,
            "table_bbox": [i, i + 1, i + 2, i + 3],
            "quote": f"full unedited source text {i}",
        }
        for i in range(120)
    ]
    summary = {"collections": {"evidence": {"file": "evidence.jsonl", "count": len(rows)}}}
    (tmp_path / "result.json").write_text(json.dumps(summary))
    (tmp_path / "sources.json").write_text("[]")
    (tmp_path / "evidence.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    packet = pack_complete_result(tmp_path)
    decoded = unpack_complete_result(packet)
    assert json.dumps(decoded["collections"]["evidence"], sort_keys=True) == json.dumps(rows, sort_keys=True)
    assert len(packet["collections"]["evidence"]["groups"]) == 1
    assert "page" not in packet["collections"]["evidence"]["groups"][0]["common"]
    assert len(json.dumps(packet)) < len(json.dumps(decoded)) / 2
