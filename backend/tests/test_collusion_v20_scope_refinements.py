"""Scope regressions from actual delivery, with synthetic unrelated projects."""

from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def vendor(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "vendor/bid_audit_tools"))


def block(page):
    return {"page": page, "physical_pages": [page], "chunk_ids": [str(page)], "positions": [[page, 0, 1, 0, 1]], "source_channel": "ragflow_mineru"}


def context():
    return SimpleNamespace(evidence=lambda doc, page, quote, **kwargs: {"document_id": doc["id"], "page": page, "quote": quote, **kwargs})


def test_requirement_catalog_never_imports_headings_from_unrelated_product_appendix():
    from bid_audit.enhanced_tables import collect_catalog

    response = {"rows": [["序号", "采购文件规定的技术要求", "投标文件响应的具体内容", "型号", "是否偏离"], ["10", "（一）器材甲", "（一）器材甲", "", "无偏离"], ["11", "1)精度：≤4mm", "1)精度：5mm", "TYPE-A", "负偏离"]]}
    brochure = {"rows": [["10", "（一）附录器材"], ["11", "1)尺寸：6mm"]]}
    rows = collect_catalog(context(), {"id": "a"}, [(block(1), [response]), (block(50), [brochure])])
    assert rows and all(r["physical_pages"] == [1] for r in rows)
    assert all(r["section"] == "器材甲" for r in rows)


def test_business_response_section_is_not_overwritten_by_technical_row_ordinals():
    from bid_audit.enhanced_tables import collect_catalog

    technical = {"rows": [["序号", "采购文件规定的技术要求", "投标文件响应的具体内容", "是否偏离"], ["1", "（一）器材甲", "（一）器材甲", "无偏离"], ["2", "1)精度：≤4mm", "1)精度：5mm", "负偏离"]]}
    business = {"rows": [["序号", "采购文件规定的商务条件", "投标文件响应的具体内容", "是否偏离"], ["2", "付款方式：交付后付款", "付款方式：交付后付款", "无偏离"]]}
    rows = collect_catalog(context(), {"id": "a"}, [(block(1), [technical]), (block(2), [business])])
    assert next(r for r in rows if "付款方式" in r["requirement"])["section"] == "商务条件"


def test_incommensurate_dimension_pair_is_flagged_for_source_check_not_word_change():
    from bid_audit.enhanced_tables import alignment_flags

    flags = alignment_flags("分辨率：1600×900p", "屏幕：24英寸")
    assert "incommensurate_numeric_dimensions" in flags


def test_bid_stated_requirement_is_not_automatically_proven_public_template(monkeypatch):
    from bid_audit import enhanced_tables, enhanced_text

    rows = [
        {"document_id": d, "bidder": d, "round": "1", "lot": "1", "section": "器材", "requirement": "供应商完成交付后应当提供完整的维护记录及设备归档报告", "response": "我方完成交付后应当提供完整的维护记录及设备归档报告", "evidence": []}
        for d in ["a", "b"]
    ]
    monkeypatch.setattr(enhanced_tables, "response_rows", lambda ctx: rows)
    monkeypatch.setattr(enhanced_text, "template_texts", lambda ctx: defaultdict(list))
    groups = enhanced_text.requirement_edit_patterns(SimpleNamespace())
    assert groups and all(r["public_source_match"] is False for r in groups)
    assert all(r["requirement_reference_verified"] is False for r in groups)


def test_response_continuation_survives_one_page_without_matching_status_cells():
    from bid_audit.enhanced_tables import response_views

    header = {"rows": [["要求", "投标文件响应的具体内容", "是否偏离"]]}
    continued = {"rows": [["要求正文", "实际响应", "负偏离"]]}
    selected = response_views([(block(1), [header]), (block(3), [continued])])
    assert {b["page"] for b, _ in selected} == {1, 3}
