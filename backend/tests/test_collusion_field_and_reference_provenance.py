"""General provenance regressions; no project names or historical answers."""

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def vendor(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "vendor/bid_audit_tools"))


def test_template_lookup_survives_a_leading_list_marker():
    from bid_audit.source_quality import _grams

    text = "探头接口须兼容现有控制平台并提供独立的使用说明及维护记录"
    assert _grams(text) <= _grams("3" + text)


def test_m02_field_advice_uses_actual_primary_collection_without_excluding_prose():
    from bid_audit.source_quality import VERSION, enrich

    row = {
        "kind": "stable_cross_document_replacement",
        "document_a": "a",
        "document_b": "b",
        "before": "张甲",
        "after": "李乙",
        "occurrence_count": 7,
        "non_template_shared_chars": 123,
        "contexts": [{"context_a": "本投标人正式授权代表张甲全权处理有关投标事宜", "context_b": "本投标人正式授权代表李乙全权处理有关投标事宜"}],
    }
    address = {"kind": "stable_cross_document_replacement", "document_a": "a", "document_b": "b", "before": "旧区", "after": "新区", "contexts": [{"context_a": "联系人张甲地址旧区某路", "context_b": "联系人李乙地址新区某路"}]}
    original = {"findings": [row, address]}
    context = SimpleNamespace(by_id={"a": {"sha256": "s", "bidder": "甲公司"}, "b": {"sha256": "t", "bidder": "乙公司"}}, documents=lambda *roles: [])
    assert enrich(context, "M02", copy.deepcopy(original), {}) == original
    result = enrich(context, "M02", copy.deepcopy(original), {"a": {"source_view_version": VERSION, "source_sha256": "s", "chunks": []}})
    advice = result["replacement_interpretation_facts"]
    assert len(advice) == 2 and advice[0]["source_collection"] == "findings"
    assert advice[0]["person_role_field_context_observed"] is True
    assert advice[1]["person_role_field_context_observed"] is False
    assert advice[0]["identity_or_contact_field_context_observed"] is True
    assert advice[0]["occurrence_count_is_not_independent_evidence_count"] is True
    assert advice[0]["cannot_alone_establish_common_error_or_authorship"] is True
    assert advice[0]["public_source_for_surrounding_prose_verified"] is False
    assert result["metrics"]["unmatched_text_count_is_private_origin_proof"] is False
    assert result["findings"][0] == row and "risk_verdict" not in advice[0]


def test_person_field_context_survives_bounded_source_navigation():
    from app.experiments.collusion_audit.full_results import ENCODING
    from app.experiments.collusion_audit.review_quality import fact_navigation

    corporate = [
        {"source_collection_offset": i, "before": "旧区", "after": "新区", "bidder_identity_fragment_substitution": False, "identity_or_contact_field_context_observed": True, "person_role_field_context_observed": False} for i in range(8)
    ]
    person = {
        "source_collection_offset": 8,
        "person_role_field_context_observed": True,
        "before": "张甲",
        "after": "李乙",
        "bidder_identity_fragment_substitution": False,
        "identity_or_contact_field_context_observed": True,
        "cannot_alone_establish_common_error_or_authorship": True,
    }
    rows = corporate + [person]
    name = "replacement_interpretation_facts"
    packet = {"encoding": ENCODING, "complete": True, "summary": {"collections": {name: {"count": len(rows)}}}, "sources": [], "collections": {name: {"record_count": len(rows), "groups": [{"common": {}, "rows": rows}]}}}
    result = fact_navigation(packet, "/current")
    assert person in result[name]["records"]
    assert result[name]["count"] == 9 and result[name]["shown"] <= 3


def test_lexical_residual_is_not_labeled_verified_non_template_origin():
    from bid_audit.source_quality import VERSION, enrich

    context = SimpleNamespace(by_id={"a": {"sha256": "s"}}, documents=lambda *roles: [])
    row = {"kind": "non_template_shared_passage", "text_a": "公开表样中填入各自姓名的原句", "text_b": "公开表样中填入另一姓名的原句", "non_template_shared_chars": 123}
    raw = {"findings": [row]}
    assert enrich(context, "M01", copy.deepcopy(raw), {}) == raw
    result = enrich(context, "M01", copy.deepcopy(raw), {"a": {"source_view_version": VERSION, "source_sha256": "s", "chunks": []}})
    record = result["findings"][0]
    assert record["kind"] != "non_template_shared_passage"
    assert record["original_detector_kind"] == "non_template_shared_passage"
    assert record["source_origin_status"] == "not_verified_by_lexical_residual"
    assert record["non_template_shared_chars"] == 123 and record["text_a"] == row["text_a"] and record["text_b"] == row["text_b"]
