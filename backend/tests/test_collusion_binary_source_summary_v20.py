"""Source-level binary review aggregates, independent of project risk labels."""

import copy
import hashlib
import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def vendor(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "vendor/bid_audit_tools"))


def test_literal_keys_count_groups_not_repetitions_and_keep_all_source_rows():
    from bid_audit.enhanced_pdf import raw_review_facts

    rows = [
        {"sha256": "a", "decoded_preview": "/Producer (x) /Producer (x)", "evidence": [{"raw_byte_offset": 17}]},
        {"sha256": "b", "decoded_preview": "/CID 1 /IDTree 2 /InfoExtra 3", "evidence": []},
        {"sha256": "c", "decoded_preview": "/ID [<123> <456>] /CreationDate (D:20260101)", "evidence": [{"raw_byte_offset": 29}]},
    ]
    before = copy.deepcopy(rows)
    metrics, observations = raw_review_facts(rows, [{"window_hash_groups": 2}, {"window_hash_groups": 1}])
    assert metrics["raw_representative_window_hash_groups_total"] == 3
    assert metrics["raw_representative_partition_complete"] is True
    assert metrics["raw_literal_pdf_key_group_counts"]["/Producer"] == 1
    assert metrics["raw_literal_pdf_key_group_counts"]["/ID"] == 1
    assert metrics["raw_literal_pdf_key_group_counts"]["/Info"] == 0
    assert [r["sha256"] for r in observations] == ["a", "c"]
    assert observations[0]["decoded_preview"] == rows[0]["decoded_preview"]
    assert observations[0]["evidence"] == rows[0]["evidence"]
    assert rows == before


def test_incomplete_partition_is_explicit_and_zero_keys_do_not_mean_whole_pdf_clear():
    from bid_audit.enhanced_pdf import raw_review_facts

    metrics, observations = raw_review_facts([{"decoded_preview": "/CID 1"}, {"decoded_preview": "/Kids [1 0 R]"}], [{"window_hash_groups": 1}])
    assert metrics["raw_representative_partition_complete"] is False
    assert not observations and not any(metrics["raw_literal_pdf_key_group_counts"].values())
    assert "not a whole-PDF" in metrics["raw_literal_pdf_key_scope"]


def test_real_pdf_scan_exposes_metadata_key_windows_and_partition_counts(tmp_path):
    from bid_audit.core import Context
    from bid_audit.enhanced_pdf import m12
    from pypdf import PdfWriter

    docs = []
    for i in range(2):
        path = tmp_path / f"bid{i}.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=600, height=800)
        writer.add_metadata({"/Producer": "shared-source-" + "abcdefghij" * 12, "/CreationDate": "D:20260101120000"})
        with path.open("wb") as stream:
            writer.write(stream)
        docs.append({"id": f"d{i + 1:03d}", "path": path.name, "role": "bid", "bidder": f"bidder{i}", "round": "1", "lot": "1", "expected_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": "1.0", "project_id": "synthetic", "data_root": ".", "documents": docs}))
    result = m12(Context(manifest, tmp_path / "cache", permitted_root=tmp_path))
    assert result["metrics"]["raw_representative_partition_complete"] is True
    assert result["metrics"]["raw_representative_window_hash_groups_total"] == result["metrics"]["raw_shared_window_hash_groups"]
    assert result["metrics"]["raw_literal_pdf_key_group_counts"]["/Producer"] > 0
    assert result["raw_metadata_key_observations"]
    actual = {r["sha256"] for r in result["findings"]}
    assert all(r["sha256"] in actual for r in result["raw_metadata_key_observations"])
