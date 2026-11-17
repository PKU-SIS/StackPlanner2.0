"""Generic source-role/template regressions, without project answers."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def vendor(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "vendor/bid_audit_tools"))


def block(text, page=1):
    return {"text": text, "page": page, "physical_pages": [page], "source_channel": "synthetic_original"}


def test_template_slots_resolve_to_the_right_form_not_the_bid_letter():
    from bid_audit.reference_context import ReferenceCatalog

    quote = "对于山区设备采购项目（项目编号：AB-2099），我方郑重承诺如下："
    refs = {"t": [block("你方组织的采购项目，我方愿参与投标，并接受全部条款。", 3), block("承诺函\n对于____项目（项目编号：____），我方郑重承诺如下：\n如中标我方严格履行采购文件要求。", 17)]}
    best = ReferenceCatalog(refs).lookup(quote)[0]
    assert best["reference_source"]["page"] == 17
    assert best["query_fully_explained_by_literal_template"]
    assert best["match_kind"] == "explicit_template_slots"
    assert best["matched_template_quote"] in refs["t"][1]["text"]
    assert "山区设备" not in best["matched_template_quote"]


def test_ratio_undertaking_is_not_compared_only_with_an_adjacent_declaration():
    from bid_audit.reference_context import ReferenceCatalog

    quote = "本单位提供的符合本国产品标准的产品成本之和占全部产品成本之和的比例达到80%以上"
    refs = {"t": [block("本单位郑重声明：以下产品属于本国产品。", 8), block("承诺函：本单位提供的符合本国产品标准的产品成本之和占全部产品成本之和的比例达到80%以上。", 9)]}
    best = ReferenceCatalog(refs).lookup(quote)[0]
    assert best["reference_source"]["page"] == 9
    assert best["query_fully_explained_by_literal_template"]


def test_product_registration_and_enterprise_license_are_not_the_same_obligation():
    from bid_audit.reference_context import reference_compatibility

    a = "所投产品属于第二类第三类医疗器械，应提供有效的医疗器械注册证明扫描件"
    b = "投标人为生产企业，所投产品属于第二类第三类医疗器械，应提供有效的医疗器械生产许可证扫描件"
    facts = reference_compatibility(a, b)
    assert facts["status"] == "different_explicit_purpose"
    assert not facts["same_clause_verified"]
    assert "risk_verdict" not in facts


def test_contract_scope_keeps_project_parties_date_and_never_declares_it_safe():
    from bid_audit.reference_context import material_context

    quote = "按照竞争性谈判文件规定条款和成交供应商承诺，甲乙双方签订本合同"
    blocks = [block("业绩合同\n合同编号：ZZ-2021\n采购项目：某县急救设备采购\n甲方：某县医院\n乙方：某制造商\n签订日期：2021年7月9日", 22), block(quote, 22)]
    result = material_context(blocks, block(quote, 22))
    assert result["contract_or_performance_material_observed"]
    text = "\n".join(r["text"] for r in result["source_context"])
    for marker in ["ZZ-2021", "某县医院", "某制造商", "2021年7月9日"]:
        assert marker in text
    assert result["current_procurement_scope_verified"] is False
    assert result["automatically_excluded_from_cross_bid_review"] is False
    assert blocks[1]["text"] == quote


def test_shared_business_change_remains_a_candidate():
    from bid_audit.source_quality import common_baseline_edits

    a = "资格声明：信用查询时点以评审当日的查询结果为准，供应商应当提供相关证明材料。"
    b = "资格声明：信用查询时点以投标截止当日的查询结果为准，供应商应当提供相关证明材料。"
    observations = []
    rows = common_baseline_edits({"a": [block(a)], "b": [block(a)]}, {"t": [block(b)]}, source_observations=observations)
    assert rows and rows[0]["shared_reference_edits"]
    assert rows[0]["reference_compatibility"]["status"] != "different_explicit_purpose"
    assert "risk_verdict" not in rows[0]


def test_filled_template_does_not_become_a_shared_private_rewrite():
    from bid_audit.source_quality import common_baseline_edits

    quote = "对于山地气象设备建设项目（项目编号：ZZ-32），我方郑重承诺如下："
    observations = []
    rows = common_baseline_edits({"a": [block(quote)], "b": [block(quote)]}, {"t": [block("对于____项目（项目编号：____），我方郑重承诺如下：", 18)]}, source_observations=observations)
    assert not rows
    assert any(o["reference_matches"][0]["reference_source"]["page"] == 18 for o in observations)


def test_template_match_does_not_hide_a_changed_substantive_condition():
    from bid_audit.reference_context import ReferenceCatalog

    ref = "对于____项目（项目编号：____），我方郑重承诺如下：查询时间以投标截止当日为准。"
    quote = "对于某设备项目（项目编号：ZZ-1），我方郑重承诺如下：查询时间以评审当日为准。"
    matches = ReferenceCatalog({"t": [block(ref)]}).lookup(quote)
    assert not any(x["query_fully_explained_by_literal_template"] for x in matches)


def test_review_prompt_requires_material_scope_and_exact_form_verification():
    from app.experiments.collusion_audit.review_quality import guidance

    for rule in ["M01", "M02", "M03", "M07"]:
        text = guidance(rule)
        assert "历史业绩合同" in text and "同一种格式" in text
        assert "reference_source_observations" in text
        assert "不能" in text


def test_source_preview_prioritizes_and_diversifies_explicit_form_locations():
    from app.experiments.collusion_audit.full_results import ENCODING
    from app.experiments.collusion_audit.review_quality import fact_navigation

    ordinary = {"document_ids": ["a", "b"], "reference_matches": [{"reference_document_id": "t", "reference_source": {"physical_pages": [1]}, "match_kind": "similar_source_only", "query_fully_explained_by_literal_template": False}]}
    slot = {"document_ids": ["a", "b"], "reference_matches": [{"reference_document_id": "t", "reference_source": {"physical_pages": [9]}, "match_kind": "explicit_template_slots", "query_fully_explained_by_literal_template": True}]}
    ratio = {"document_ids": ["a", "b"], "reference_matches": [{"reference_document_id": "t", "reference_source": {"physical_pages": [10]}, "match_kind": "literal_containment", "query_fully_explained_by_literal_template": True}]}
    rows = [ordinary] * 4 + [slot] * 4 + [ratio]
    name = "reference_source_observations"
    packet = {"encoding": ENCODING, "complete": True, "summary": {"collections": {name: {"count": len(rows)}}}, "sources": [], "collections": {name: {"record_count": len(rows), "groups": [{"common": {}, "rows": rows}]}}}
    preview = fact_navigation(packet, "/current")[name]
    assert preview["records"][0] == slot
    assert ratio in preview["records"]
    assert preview["count"] == len(rows) and preview["preview_is_not_complete_review"]


def test_contract_preview_keeps_identified_contracts_before_generic_instructions():
    from app.experiments.collusion_audit.full_results import ENCODING
    from app.experiments.collusion_audit.review_quality import fact_navigation

    generic = {"document_id": "a", "scope_markers": ["项目编号：NOW-1"], "source_context": [{"text": "我方同意中标后签订采购合同"}]}
    contract = {"document_id": "b", "scope_markers": ["合同编号：OLD-2022-13", "采购人（甲方）：某县医院"], "source_context": [{"text": "政府采购合同\n合同编号：OLD-2022-13\n采购人（甲方）：某县医院"}]}
    rows = [generic] * 4 + [contract]
    name = "material_scope_observations"
    packet = {"encoding": ENCODING, "complete": True, "summary": {"collections": {name: {"count": len(rows)}}}, "sources": [], "collections": {name: {"record_count": len(rows), "groups": [{"common": {}, "rows": rows}]}}}
    assert fact_navigation(packet, "/current")[name]["records"][0] == contract
