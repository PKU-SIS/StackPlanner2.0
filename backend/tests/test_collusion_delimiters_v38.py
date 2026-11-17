"""Delimiter structures are source-addressed candidates, never verdicts."""

from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def vendor(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "vendor/bid_audit_tools"))


@pytest.mark.parametrize("text", ["说明（甲[乙]丙）结束", "编号1) 第一项\n2）第二项", "网站(example.invalid)查询，凭据（有效）保留。"])
def test_balanced_and_list_labels_are_not_structural_errors(text):
    from bid_audit.delimiters import delimiter_events

    assert delimiter_events(text) == []


@pytest.mark.parametrize("text,kind", [("材料（甲）查证后归档）", "unexpected_close"), ("说明（甲[乙）丙]", "crossed_delimiter"), ("说明（尚未完结", "unclosed_open")])
def test_nonconsecutive_and_crossed_or_open_delimiters(text, kind):
    from bid_audit.delimiters import delimiter_events

    events = delimiter_events(text)
    assert any(r["kind"] == kind for r in events)
    assert all(text[r["offset"]] == r["character"] for r in events)


def context(texts):
    docs = [{"id": id, "role": "tender" if id.startswith("t") else "bid", "bidder": id, "round": "2" if id.endswith("2") else "1", "lot": "1", "_path": Path("not-a-real-file.txt")} for id in texts]
    return SimpleNamespace(
        documents=lambda *roles: [d for d in docs if d["role"] in roles], pages=lambda doc: [{"page": 3, "text": texts[doc["id"]]}], evidence=lambda doc, page, quote, **kw: {"document_id": doc["id"], "page": page, "quote": quote, **kw}
    )


BROKEN = "由受托单位核查记录（在平台网站(example.invalid)及资料网站 http://archive.invalid/)核查为准，申请者提交证明材料）。"
FIXED = "由受托单位核查记录（在平台网站(example.invalid)及资料网站(http://archive.invalid/)核查为准，申请者提交证明材料）。"


def test_shared_error_matches_same_location_across_width_and_linewraps():
    from bid_audit.delimiters import audit_delimiters

    result = audit_delimiters(context({"a": BROKEN, "b": BROKEN.replace("材料）", "材\n料)"), "t": FIXED}))
    assert len(result["findings"]) == 1
    row = result["findings"][0]
    assert row["kind"] == "shared_delimiter_structure_candidate"
    assert set(row["document_ids"]) == {"a", "b"}
    assert row["reference_comparisons"] and not row["reference_comparisons"][0]["same_local_anomaly"]
    assert len(row["evidence"]) == 2 and all(e["physical_pages"] == [3] for e in row["evidence"])
    assert row["requires_original_verification"] and "risk_verdict" not in row


def test_exact_inherited_anomaly_is_disclosed_not_promoted():
    from bid_audit.delimiters import audit_delimiters

    result = audit_delimiters(context({"a": BROKEN, "b": BROKEN, "t": BROKEN}))
    assert result["findings"] == []
    assert result["template_exclusions"]
    assert result["observations"]


def test_anomaly_elsewhere_in_same_tender_clause_does_not_exclude_bidder_error():
    from bid_audit.delimiters import audit_delimiters

    result = audit_delimiters(context({"a": BROKEN, "b": BROKEN, "t": "附注（" + FIXED}))
    assert result["findings"]
    assert all(not r["same_local_anomaly"] for r in result["findings"][0]["reference_comparisons"])


@pytest.mark.parametrize("texts", [{"a": BROKEN, "b": "安装后请在机架背面复核电路安全）"}, {"a": BROKEN, "b2": BROKEN}, {"a": BROKEN, "b": FIXED}])
def test_different_location_round_or_one_sided_error_is_not_shared(texts):
    from bid_audit.delimiters import audit_delimiters

    assert audit_delimiters(context(texts))["findings"] == []


def test_unclosed_unit_is_explicitly_not_a_proven_paper_error():
    from bid_audit.delimiters import audit_delimiters

    result = audit_delimiters(context({"a": "附注（后续条件详见下一页", "b": "附注（后续条件详见下一页"}))
    assert result["findings"][0]["source_boundary_may_split_delimiter"]
    assert result["findings"][0]["requires_original_verification"]


def test_quality_mode_connects_findings_to_primary_collection_and_navigation():
    from bid_audit.source_quality import VERSION, enrich

    from app.experiments.collusion_audit.review_quality import COLLECTIONS

    ctx = context({"a": BROKEN, "b": BROKEN, "t": FIXED})
    ctx.by_id = {d["id"]: {**d, "sha256": d["id"]} for d in ctx.documents("bid", "tender")}
    parsed = {"a": {"source_view_version": VERSION, "source_sha256": "a", "chunks": []}}
    original = {"metrics": {}, "findings": []}
    assert enrich(ctx, "M08", original.copy(), {}) == original
    result = enrich(ctx, "M08", {"metrics": {}, "findings": []}, parsed)
    assert result["findings"] == result["delimiter_structure_findings"]
    assert result["metrics"]["shared_delimiter_structure_candidates"] == 1
    assert not result["metrics"]["all_delimiter_balance_checked"]
    assert "delimiter_structure_findings" in COLLECTIONS


def test_prompts_cover_structures_and_original_verification_without_answers():
    from app.experiments.collusion_audit.review_quality import guidance

    for rule in ["M07", "M08"]:
        s = guidance(rule)
        assert "delimiter_structure_findings" in s and "原图" in s and "跨页" in s
        for name in ["九州通", "恒汇", "玮琳", "评审当天"]:
            assert name not in s


def test_reference_discloses_other_unit_anomalies_without_globally_excluding_candidate():
    from bid_audit.delimiters import audit_delimiters

    result = audit_delimiters(context({"a": BROKEN, "b": BROKEN, "t": "附注（" + FIXED}))
    refs = result["findings"][0]["reference_comparisons"]
    assert refs and not refs[0]["same_local_anomaly"]
    assert any(e["kind"] == "unclosed_open" for e in refs[0]["reference_unit_delimiter_events"])
    assert refs[0]["reference_events_are_source_unit_observations_not_paper_verdicts"]


def test_guidance_limits_baseline_claims_and_prevents_repeating_invalid_crop():
    from app.experiments.collusion_audit.review_quality import guidance

    for rule in ["M07", "M08"]:
        s = guidance(rule)
        assert "局部配对正常不等于整段配对正确" in s
        assert "原生JSON数组" in s and "不得原样重试" in s
