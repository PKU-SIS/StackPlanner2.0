"""Generic response-to-reference locations; no project verdicts or page answers."""

import copy
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def vendor(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "vendor/bid_audit_tools"))


def docs():
    return [{"id": "a", "bidder": "甲公司", "round": "1", "lot": "1"}, {"id": "b", "bidder": "乙公司", "round": "1", "lot": "1"}]


def test_source_location_survives_a_different_response_heading():
    from bid_audit.response_reference import locate_response_sources

    source = "标的提供时间：合同签订生效且具备安装条件后，接书面通知之日起45个自然日内完成交货、安装及调试"
    response = "交货时间：合同签订生效且具备安装条件后，接书面通知之日起45个自然日内完成交货、安装及调试"
    data = {"findings": [{"document_a": "a", "document_b": "b", "requirement": response, "response_a": response, "response_b": response}]}
    before = copy.deepcopy(data)
    result = locate_response_sources(data, docs(), [{"id": "t", "round": "1", "lot": "1", "blocks": [{"text": source, "page": 7, "physical_pages": [7]}]}])
    row = result["observations"][0]
    assert row["source_row_offset"] == 0
    match = row["located_texts"][0]["reference_matches"][0]
    assert match["reference_source"]["page"] == 7 and match["matched_template_quote"] == source
    assert data == before and row["automatically_excludes_shared_response_risk"] is False


def test_different_values_are_preserved_and_never_verified_by_similarity():
    from bid_audit.response_reference import locate_response_sources

    source = "签订合同后，采购人应支付合同总价的35%作为预付款，其余款项验收后支付"
    changed = source.replace("35%", "65%")
    data = {"requirement_copy_pairs": [{"document_a": "a", "document_b": "b", "requirement": changed, "response_a": changed, "response_b": changed, "requirement_reference_verified": False}]}
    before = copy.deepcopy(data)
    r = locate_response_sources(data, docs(), [{"id": "t", "blocks": [{"text": source, "page": 11, "physical_pages": [11]}]}])
    assert r["observations"] and data == before
    item = r["observations"][0]["located_texts"][0]
    assert "65%" in item["original_query"] and "35%" in item["reference_matches"][0]["matched_template_quote"]
    assert r["observations"][0]["automatically_excludes_shared_response_risk"] is False


def test_reference_from_another_round_is_not_used():
    from bid_audit.response_reference import locate_response_sources

    text = "合同签订后十个工作日内完成交付，并提供完整安装和操作培训服务"
    d = {"findings": [{"document_a": "a", "document_b": "b", "requirement": text}]}
    r = locate_response_sources(d, docs(), [{"id": "other", "round": "2", "lot": "1", "blocks": [{"text": text, "page": 3, "physical_pages": [3]}]}])
    assert r["observations"] == [] and r["summary"]["no_match_is_absence_proof"] is False


def test_located_sources_survive_navigation_next_to_initial_unverified_counts():
    from app.experiments.collusion_audit.full_results import ENCODING
    from app.experiments.collusion_audit.review_quality import fact_navigation

    row = {
        "source_collection": "findings",
        "source_row_offset": 4,
        "located_texts": [{"fields": ["response_a"], "original_query": "预付款条款原文", "reference_matches": [{"reference_source": {"physical_pages": [7], "text": "实际招标预付款条款原文"}}]}],
    }
    name = "response_public_source_observations"
    packet = {"encoding": ENCODING, "complete": True, "summary": {"collections": {name: {"count": 1}}}, "sources": [], "collections": {name: {"record_count": 1, "groups": [{"common": {}, "rows": [row]}]}}}
    assert fact_navigation(packet, "/current")[name]["records"][0] == row


def test_response_preview_prioritizes_different_source_queries():
    from app.experiments.collusion_audit.full_results import ENCODING
    from app.experiments.collusion_audit.review_quality import fact_navigation

    rows = [{"source_row_offset": i, "located_texts": [{"original_query": q, "reference_matches": []}]} for i, q in enumerate(["同一条款", "同一条款", "第二条款", "第三条款"])]
    name = "response_public_source_observations"
    packet = {"encoding": ENCODING, "complete": True, "summary": {"collections": {name: {"count": 4}}}, "sources": [], "collections": {name: {"record_count": 4, "groups": [{"common": {}, "rows": rows}]}}}
    nav = fact_navigation(packet, "/current")[name]
    assert [r["source_row_offset"] for r in nav["records"]] == [0, 2, 3]
    assert nav["count"] == 4 and nav["preview_is_not_complete_review"]
