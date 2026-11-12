"""Exact page/chunk receipts prevent range guesses becoming coverage claims."""

import json

import pytest

from app.experiments.collusion_audit.knowledge import KnowledgeStore, order_chunks


def store(tmp_path):
    docs = {}
    for d in ["d001", "d002"]:
        folder = tmp_path / d
        folder.mkdir()
        rows = order_chunks([{"id": d + str(p), "content": "same text" if p in [1, 3] else "page two", "positions": [[p, 0, 1, 0, 1]]} for p in [1, 2, 3]])
        (folder / "chunks.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        (folder / "parsed.md").write_text("\n\n".join("[chunk " + r["id"] + " | pages " + str(r["positions"][0][0]) + "]\n" + r["content"] for r in rows))
        docs[d] = {"dataset_id": d, "document_id": "remote-" + d, "snapshot": d, "original_pages": 3}
    (tmp_path / "index.json").write_text(json.dumps({"ready": True, "documents": docs}))
    return KnowledgeStore(tmp_path)


def test_page_address_resolves_original_positions_and_keeps_frozen_snapshot(tmp_path):
    k = store(tmp_path)
    before = (tmp_path / "d001/chunks.jsonl").read_bytes()
    result = k.page_text("d001", 2, 3)
    assert "[chunk d0012" in result["text"] and "[chunk d0013" in result["text"]
    assert "[chunk d0011" not in result["text"]
    assert result["requested_pages"] == [2, 3]
    assert result["pages_without_text_chunks"] == []
    assert (tmp_path / "d001/chunks.jsonl").read_bytes() == before
    for a, b in [(0, 1), (2, 1), (1, 4)]:
        with pytest.raises(ValueError):
            k.page_text("d001", a, b)
    with pytest.raises(ValueError):
        k.page_text("outside", 1, 1)


def test_receipts_bind_chunk_position_not_duplicated_content_or_incomplete_window(tmp_path):
    from app.experiments.collusion_audit.source_receipts import OCRReadReceipts

    k = store(tmp_path)
    receipts = OCRReadReceipts(k)
    text = (tmp_path / "d001/parsed.md").read_text()
    stop = text.index("\n\n")

    def receive(offset, end):
        receipts.observe("read_file", {"file_path": "/knowledge/d001/parsed.md", "source": "persistent_MinerU_snapshot", "offset": offset, "text": text[offset:end]}, {})

    receive(0, stop - 2)
    assert receipts.page_sets()["consulted"].get("d001", []) == []
    receive(stop - 2, stop)
    assert receipts.page_sets()["consulted"]["d001"] == [1]
    assert 3 not in receipts.page_sets()["consulted"]["d001"]
    receipts.observe("knowledge_read", {"document_id": "d002", "items": [k.rows("d002")[0]]}, {})
    assert receipts.page_sets()["consulted"]["d002"] == [1]


def test_scope_validation_is_only_receipts_and_never_all_candidate_gate():
    from app.experiments.collusion_audit.report_checks import assertion_errors

    common = {"evidence_level": "grade", "visual_reviewed_candidates": [], "ocr_reviewed_candidates": [], "candidate_page_count": 3}
    kw = {"expected_grade": "grade", "rule_id": "M06", "displayed_pairs": set(), "ocr_pairs": set(), "candidate_page_count": 3}
    assert assertion_errors(common, **kw) == []
    assert assertion_errors({**common, "ocr_reviewed_candidates": [0]}, **kw)
    assert assertion_errors({**common, "ocr_reviewed_candidates": [0]}, **{**kw, "ocr_pairs": {0}}) == []
    assert assertion_errors({**common, "candidate_page_count": 4}, **kw)


def test_page_address_actual_tool_returns_sources_and_updates_only_current_receipts(tmp_path):
    import asyncio

    from app.experiments.collusion_audit.tools import AuditTools
    from app.experiments.tender_audit.observability import AuditTrace

    k = store(tmp_path)
    a = AuditTools("M06", tmp_path, tmp_path / "outputs", tmp_path / "cache", AuditTrace(tmp_path / "trace"), knowledge=k, rule_level_review=True, quality_controls=True)
    read = next(t for t in a.build() if t.name == "read_file")
    value = json.loads(asyncio.run(read.ainvoke({"file_path": "/knowledge/d001/pages/2-3.md", "offset": 0, "limit": 24000})))
    assert value["source"] == "persistent_MinerU_snapshot"
    assert value["requested_pages"] == [2, 3]
    assert len(value["source_chunks"]) == 2
    assert a.ocr_receipts.page_sets()["consulted"] == {"d001": [2, 3]}
    assert value["next_offset"] is None


def test_image_navigation_has_exact_sparse_pages_and_original_offsets(tmp_path):
    from app.experiments.collusion_audit.fact_index import build_fact_index, compact_facts
    from app.experiments.collusion_audit.full_results import pack_complete_result

    ev = [{"evidence_id": "E000001", "document_id": "d001", "page": 1}, {"evidence_id": "E000002", "document_id": "d002", "page": 3}, {"evidence_id": "E000003", "document_id": "d001", "page": 3}]
    findings = [{"kind": "perceptual_image_candidate", "dhash_distance": 3, "evidence_refs": ["E000001", "E000002"]}, {"kind": "perceptual_image_candidate", "dhash_distance": 0, "evidence_refs": ["E000003", "E000002"]}]
    for name, rows in [("evidence", ev), ("findings", findings)]:
        (tmp_path / (name + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in rows))
    (tmp_path / "sources.json").write_text("[]")
    (tmp_path / "result.json").write_text(json.dumps({"rule_id": "M06", "run_id": "run", "status": "ok", "collections": {name: {"file": name + ".jsonl", "count": n} for name, n in [("evidence", 3), ("findings", 2)]}}))
    idx = build_fact_index(pack_complete_result(tmp_path))
    facts = compact_facts(idx)["image_comparison"]
    assert facts["unique_candidate_pages"] == 3
    assert facts["candidate_pages_by_document"] == {"d001": [1, 3], "d002": [3]}
    assert facts["candidate_offsets_by_dhash"] == {"0": [1], "3": [0]}
    assert list(idx).index("image_comparison") < list(idx).index("findings")
