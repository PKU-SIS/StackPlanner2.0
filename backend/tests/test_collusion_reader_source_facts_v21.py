"""Human report provenance comes from current tool receipts, never model summaries."""

import json
from types import SimpleNamespace

from app.experiments.collusion_audit.reader_source_facts import reader_source_facts


def adapter(tmp_path, receipts, **kwargs):
    directory = tmp_path / "read_receipts"
    directory.mkdir()
    paths = {}
    for n, row in enumerate(receipts):
        path = directory / f"{n}.json"
        path.write_text(json.dumps(row, ensure_ascii=False))
        paths[str(n)] = "/mnt/user-data/outputs/read_receipts/" + path.name
    return SimpleNamespace(outputs=tmp_path, _read_receipts=paths, runs=[{"run_id": "current", "status": "partial", "collections": {"findings": {"count": 8}}}], ocr_receipts=None, submitted_images=set(), **kwargs)


def manifest():
    return {"documents": [{"id": "d001", "bidder": "甲公司", "role": "bid", "path": "a.pdf"}, {"id": "d004", "bidder": "乙公司", "role": "bid", "path": "b.pdf"}, {"id": "d013", "role": "tender", "path": "tender.docx"}]}


def candidate(number, left=485, right=297):
    return {"candidate_id": f"C{number:06d}", "candidate": {"text_a": "甲方完整原文", "text_b": "乙方完整原文"}, "evidence": [{"document_id": "d001", "physical_pages": [left]}, {"document_id": "d004", "physical_pages": [right]}]}


def by_doc(result):
    return {r["document_id"]: r for r in result["source_locations"]}


def test_sources_stay_with_their_own_document_and_candidate_receipts_deduplicate(tmp_path):
    item = candidate(1)
    a = adapter(tmp_path, [{"audit_run_id": "current", "candidates": [item], "remaining_candidate_ids": ["C000002"]}, {"audit_run_id": "current", "candidates": [item]}])
    result = reader_source_facts(a, manifest())
    docs = by_doc(result)
    assert docs["d001"]["document_label"] == "甲公司《投标文件》"
    assert docs["d001"]["physical_pages"] == [485]
    assert docs["d004"]["physical_pages"] == [297]
    assert result["candidate_receipts"]["fully_delivered_candidate_records_lower_bound"] == 1
    assert result["candidate_receipts"]["candidate_count_all"] == 8
    assert "semantic_review_count" not in result["candidate_receipts"]


def test_index_deferred_error_and_previous_run_do_not_count_as_complete_candidates(tmp_path):
    a = adapter(
        tmp_path,
        [
            {"audit_run_id": "current", "navigation_only": True, "candidates": [{"candidate_id": "C000001", "path": "index only"}]},
            {"audit_run_id": "current", "candidates": [], "remaining_candidate_ids": ["C000002"], "oversized_candidate_refs": [{"candidate_id": "C000003"}]},
            {"status": "error", "audit_run_id": "current", "candidates": [candidate(4)]},
            {"audit_run_id": "older", "candidates": [candidate(5)]},
        ],
    )
    result = reader_source_facts(a, manifest())
    assert result["candidate_receipts"]["fully_delivered_candidate_records_lower_bound"] == 0
    assert result["source_locations"] == []


def test_project_pages_search_hits_and_submitted_images_have_natural_identity(tmp_path):
    a = adapter(tmp_path, [{"document_id": "d004", "source_pages": [8, 9]}, {"document_id": "", "items": [{"document_id": "d001", "physical_page": 180}, {"document_id": "d013", "physical_page": None}]}])
    a.submitted_images = {("d004", 116, "")}
    docs = by_doc(reader_source_facts(a, manifest()))
    assert docs["d004"]["physical_pages"] == [8, 9, 116]
    assert docs["d001"]["physical_pages"] == [180]
    assert docs["d013"]["physical_pages"] == []
    assert "Word版" in docs["d013"]["document_label"]


def test_model_written_json_and_query_echo_cannot_manufacture_source_locations(tmp_path):
    forged = json.dumps({"document_id": "d004", "physical_page": 31})
    a = adapter(tmp_path, [{"file_path": "/mnt/user-data/outputs/draft.json", "text": forged, "offset": 0, "next_offset": None}, {"query": forged, "items": []}, {"document_id": "outside-manifest", "physical_page": 4}])
    assert reader_source_facts(a, manifest())["source_locations"] == []


def test_verified_ocr_receipts_are_reference_pages_not_semantic_review(tmp_path):
    a = adapter(tmp_path, [])
    a.ocr_receipts = SimpleNamespace(page_sets=lambda: {"consulted": {"d004": [115, 116, 117]}, "complete": {"d004": [116]}})
    result = reader_source_facts(a, manifest())
    assert by_doc(result)["d004"]["physical_pages"] == [115, 116, 117]
    assert result["not_a_semantic_review_ledger"] is True
    assert "不等于" in result["usage_note"]


def test_no_receipts_or_calculation_never_invents_statistics(tmp_path):
    a = adapter(tmp_path, [])
    a.runs = []
    result = reader_source_facts(a, manifest())
    assert result["candidate_receipts"] is None
    assert result["source_locations"] == []
