import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def vendor(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "vendor/bid_audit_tools"))


def test_reference_lookup_survives_a_leading_ordinal_character():
    """Adding one list marker must not hide the actual template from lookup."""
    from bid_audit.source_quality import _grams

    body = "探头接口须兼容现有控制平台并提供独立的使用说明及维护记录"
    assert _grams(body) <= _grams("3" + body)


def test_original_region_zoom_is_opt_in_and_preserves_pdf(tmp_path):
    from pypdf import PdfWriter

    from app.experiments.collusion_audit.vision import render_original

    source = tmp_path / "source.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=200)
    writer.write(source)
    before = source.read_bytes()
    doc = {"_path": source, "path": "source.pdf", "sha256": hashlib.sha256(before).hexdigest(), "round": "1"}
    adapter = SimpleNamespace(outputs=tmp_path / "out", general_quality=False, context=lambda: SimpleNamespace(by_id={"d": doc}))
    with pytest.raises(ValueError, match="general_quality"):
        render_original(adapter, "d", 1, "", region=[0, 0.5, 1, 1])
    adapter.general_quality = True
    metadata, encoded = render_original(adapter, "d", 1, "", region=[0, 0.5, 1, 1])
    assert metadata["region"] == [0, 0.5, 1, 1]
    assert metadata["display_dimensions"] == [300, 300]
    assert metadata["source_sha256"] == doc["sha256"] and encoded
    assert source.read_bytes() == before
    whole, _ = render_original(adapter, "d", 1, "")
    assert whole["display_dimensions"] == [150, 300] and "region" not in whole


@pytest.mark.parametrize("region", [[0, 0, 0, 1], [-0.1, 0, 1, 1], [0, 0, float("nan"), 1], [0, 0, 1]])
def test_original_region_rejects_invalid_bounds_before_reading(region, tmp_path):
    from app.experiments.collusion_audit.vision import render_original

    adapter = SimpleNamespace(outputs=tmp_path, general_quality=True, context=lambda: pytest.fail("invalid region must be rejected before source access"))
    with pytest.raises(ValueError, match="region"):
        render_original(adapter, "d", 1, "", region=region)


@pytest.mark.parametrize("enabled", [False, True])
def test_full_document_read_and_receipt_use_same_source_view(tmp_path, enabled):
    import asyncio

    from app.experiments.collusion_audit.knowledge import KnowledgeStore
    from app.experiments.collusion_audit.tools import AuditTools

    root = tmp_path / "knowledge"
    folder = root / "doc"
    folder.mkdir(parents=True)
    raw = {"id": "c", "content": "[错误章名 > 实际响应表]\n真实响应正文保持不变。", "positions": [[1, 0, 1, 0, 1]]}
    chunks = (json.dumps(raw, ensure_ascii=False) + "\n").encode()
    (folder / "chunks.jsonl").write_bytes(chunks)
    parsed = ("[chunk c | pages 1]\n" + raw["content"]).encode()
    (folder / "parsed.md").write_bytes(parsed)
    (root / "index.json").write_text(json.dumps({"ready": True, "documents": {"d001": {"dataset_id": "kb", "document_id": "remote", "snapshot": "doc", "source_sha256": "sha", "original_pages": 1}}}))
    knowledge = KnowledgeStore(root, general_quality=enabled)
    trace = SimpleNamespace(event=lambda *a, **k: None, record_tool=lambda *a, **k: None, append=lambda *a, **k: None)
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    adapter = AuditTools("M02", tmp_path, outputs, tmp_path / "cache", trace, knowledge=knowledge, quality_controls=True, rule_level_review=True)
    adapter.general_quality = enabled
    tool = next(t for t in adapter.build() if t.name == "read_file")
    result = json.loads(asyncio.run(tool.ainvoke({"file_path": "/knowledge/d001/parsed.md", "limit": 10000})))
    assert ("错误章名" not in result["text"]) is enabled
    assert "真实响应正文保持不变。" in result["text"]
    source = adapter.ocr_receipts.source_view("/knowledge/d001/parsed.md")
    assert source[1] == result["text"]
    assert adapter.ocr_receipts.seen["d001"] == {"c"}
    assert (folder / "parsed.md").read_bytes() == parsed
    assert (folder / "chunks.jsonl").read_bytes() == chunks


def test_actual_response_arithmetic_is_attached_to_the_comparison():
    from decimal import Decimal

    from bid_audit.source_quality import VERSION, enrich

    original = {"same_direction_review_pairs": [{"comparisons": [{"document_id": "a", "requirement": "最大转速≥4RPM", "response": "最大转速12°/秒"}, {"document_id": "b", "requirement": "最大转速≥4RPM", "response": "最大转速1RPM"}]}]}
    context = SimpleNamespace(by_id={"t": {"sha256": "s"}})
    assert enrich(context, "M04", copy.deepcopy(original), {}) == original
    result = enrich(context, "M04", copy.deepcopy(original), {"t": {"source_sha256": "s", "source_view_version": VERSION, "chunks": []}})
    first, second = result["same_direction_review_pairs"][0]["comparisons"]
    fact = first["computed_response_quantities"][0]
    assert fact["source_text"] == "12°/秒" and Decimal(fact["normalized_value"]) == 2
    assert fact["normalized_unit"] == "rpm" and "360" in fact["calculation"] and "60" in fact["calculation"]
    assert second["computed_response_quantities"][0]["normalized_value"] == "1"
    assert first["response"] == original["same_direction_review_pairs"][0]["comparisons"][0]["response"]
    assert first["computed_requirement_quantities"][0]["operator"] == "≥"


def test_zero_punctuation_matches_does_not_claim_complete_delimiter_review():
    from bid_audit.source_quality import VERSION, enrich

    context = SimpleNamespace(by_id={"a": {"sha256": "s"}}, documents=lambda *roles: [])
    data = {"metrics": {"shared_contexts": 0}, "findings": []}
    assert enrich(context, "M08", copy.deepcopy(data), {}) == data
    result = enrich(context, "M08", copy.deepcopy(data), {"a": {"source_view_version": VERSION, "source_sha256": "s", "chunks": []}})
    assert result["metrics"]["all_delimiter_balance_checked"] is False
    assert result["metrics"]["shared_contexts"] == 0


def test_stream_block_equality_is_not_unicode_prose_or_author_identification():
    from bid_audit.source_quality import VERSION, enrich

    context = SimpleNamespace(by_id={"a": {"sha256": "s"}}, documents=lambda *roles: [])
    data = {"metrics": {"shared_blocks": 0}, "findings": []}
    assert enrich(context, "M12", copy.deepcopy(data), {}) == data
    result = enrich(context, "M12", copy.deepcopy(data), {"a": {"source_view_version": VERSION, "source_sha256": "s", "chunks": []}})
    assert result["metrics"]["stream_block_match_is_unicode_text_comparison"] is False
    assert result["metrics"]["object_number_match_is_rarity_estimate"] is False
    assert result["metrics"]["shared_blocks"] == 0


def test_main_quote_applicability_does_not_discard_supplemental_price_lists():
    from bid_audit.source_quality import VERSION, enrich

    context = SimpleNamespace(by_id={"a": {"sha256": "s"}}, documents=lambda *roles: [])
    data = {"metrics": {"main_quote_rows": 1}, "findings": []}
    assert enrich(context, "M15", copy.deepcopy(data), {}) == data
    result = enrich(context, "M15", copy.deepcopy(data), {"a": {"source_view_version": VERSION, "source_sha256": "s", "chunks": []}})
    assert result["metrics"]["one_main_row_excludes_supplemental_lists"] is False
    assert result["metrics"]["brand_difference_alone_verifies_incomparability"] is False
    assert result["metrics"]["main_quote_rows"] == 1


@pytest.mark.parametrize(
    "raw,expected",
    [("D:20240102151148+07'11'", "2024-01-02T08:00:48+00:00"), ("D:20240102172836+09'28'", "2024-01-02T08:00:36+00:00"), ("D:20240102080000Z", "2024-01-02T08:00:00+00:00"), ("D:20240102160000", None), ("D:2024", None), ("bad", None)],
)
def test_pdf_dates_preserve_raw_and_do_not_guess_zone(raw, expected):
    from bid_audit.source_quality import pdf_date_fact

    value = pdf_date_fact(raw)
    assert value["source_text"] == raw
    assert value["utc_iso"] == expected
    assert value["timezone_inferred"] is False


def test_binary_metadata_fact_collection_stays_next_to_original_value():
    from bid_audit.source_quality import VERSION, enrich

    context = SimpleNamespace(by_id={"a": {"sha256": "s"}})
    data = {"records": [{"bidder": "甲", "metadata": {"/CreationDate": "D:20240102151148+07'11'"}, "evidence": {"document_id": "a"}}]}
    parsed = {"a": {"source_view_version": VERSION, "source_sha256": "s", "chunks": []}}
    result = enrich(context, "M10", copy.deepcopy(data), parsed)
    value = result["records"][0]["computed_pdf_dates"]["/CreationDate"]
    assert value["utc_iso"] == "2024-01-02T08:00:48+00:00"
    assert result["pdf_timestamp_observations"][0]["metadata_field"] == "/CreationDate"
    assert result["records"][0]["metadata"] == data["records"][0]["metadata"]


def test_worker_source_marker_matches_current_enrichment_version(tmp_path):
    from bid_audit.source_quality import VERSION

    from app.experiments.collusion_audit.m01_corpus import parsed_sources

    context = SimpleNamespace(docs=[{"id": "d", "sha256": "s", "role": "bid"}])
    knowledge = SimpleNamespace(general_quality=True, index={"documents": {"d": {}}}, document=lambda doc: {"source_sha256": "s"}, rows=lambda doc: [{"id": "c", "content": "完整原文", "positions": [[1, 0, 1, 0, 1]]}])
    value = parsed_sources(context, knowledge)
    assert value["d"]["source_view_version"] == VERSION


@pytest.mark.parametrize("text,operator", [("小于42cm", "小于"), ("大于3rpm", "大于"), ("至多600mm", "至多"), ("至少12°/秒", "至少")])
def test_quantity_boundaries_are_not_rewritten_as_equal_values(text, operator):
    from bid_audit.source_quality import quantities

    value = quantities(text)[0]
    assert value["operator"] == operator
    assert value["source_text"] == text


def test_rule_guidance_distinguishes_source_facts_from_inferences():
    from app.experiments.collusion_audit.review_quality import guidance

    assert "如实申报负偏离不等于共同编辑错误" in guidance("M07")
    assert "像素哈希不同不能排除" in guidance("M06")
    assert "风格不同不证明独立编写" in guidance("M03")
    assert "未嵌入不等于解析失败" in guidance("M11")
    assert "作者编号的分配机制" in guidance("M10")


def test_shape_categories_are_separate_from_detail_wording_and_risk_verdict():
    from bid_audit.source_quality import VERSION, enrich

    context = SimpleNamespace(by_id={"a": {"sha256": "s"}})
    original = {"same_direction_review_pairs": [{"requirement": "机架采用封闭环形结构", "comparisons": [{"document_id": "a", "response": "采用U形机架，配安全连锁装置"}, {"document_id": "b", "response": "U臂形机架"}]}]}
    parsed = {"a": {"source_view_version": VERSION, "source_sha256": "s", "chunks": []}}
    result = enrich(context, "M04", copy.deepcopy(original), parsed)
    row = result["categorical_response_observations"][0]
    assert row["shared_explicit_structure_labels"] == [{"shape": "U", "object": "机架"}]
    assert row["comparisons"][0]["response"] == original["same_direction_review_pairs"][0]["comparisons"][0]["response"]
    assert row["same_whole_response_verified"] is False
    assert row["risk_verdict_computed"] is False
    result = enrich(context, "M04", {"same_direction_review_pairs": [{"comparisons": [{"response": "双U形机架"}, {"response": "U形机架"}]}]}, parsed)
    assert result["categorical_response_observations"][0]["shared_explicit_structure_labels"] == []


def test_unfinished_candidates_cannot_be_promoted_to_confirmed_differences():
    from app.experiments.collusion_audit.review_quality import guidance

    assert "未取得实际值不等于已确认双方不同" in guidance("M04")


def test_structure_category_card_survives_full_result_navigation_and_handoff(monkeypatch):
    import app.experiments.collusion_audit.fact_context as module
    from app.experiments.collusion_audit.full_results import ENCODING
    from app.experiments.collusion_audit.review_quality import fact_navigation

    name = "categorical_response_observations"
    row = {"shared_explicit_structure_labels": [{"shape": "U", "object": "机架"}], "comparisons": [{"response": "U形机架"}, {"response": "U臂形机架"}]}
    packet = {"encoding": ENCODING, "complete": True, "summary": {"collections": {name: {"count": 1}}}, "sources": [], "collections": {name: {"record_count": 1, "groups": [{"common": {}, "rows": [row]}]}}}
    navigation = fact_navigation(packet, "/current")
    assert navigation[name]["records"] == [row]
    adapter = SimpleNamespace(general_quality=True, fact_indexes={"calc": {"audit_run_id": "calc", "source_quality_navigation": navigation}})
    monkeypatch.setattr(module, "compact_facts", lambda index: {})
    request = SimpleNamespace(messages=[], override=lambda **v: SimpleNamespace(**v))
    projected = module.CurrentCodeFactsMiddleware(adapter).project(request)
    text = projected.messages[-1].content
    assert "actual_structure_category_observations" in text
    assert "U形机架" in text and "U臂形机架" in text
