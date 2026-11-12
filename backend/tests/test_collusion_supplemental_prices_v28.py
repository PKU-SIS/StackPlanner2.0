"""Whole-bid supplemental quote sources; no unit-price or identity invention."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def vendor(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "vendor/bid_audit_tools"))


def table(price1="70001.00", price2="70001.00", header="报价(元)"):
    return (
        "<table><tr><td>序号</td><td>设备名称</td><td>单位</td><td>配置清单</td><td>"
        + header
        + "</td></tr><tr><td>1</td><td>设备甲</td><td>套</td><td>配置清单一</td><td>"
        + price1
        + "</td></tr><tr><td>2</td><td>设备甲</td><td>套</td><td>配置清单二</td><td>"
        + price2
        + "</td></tr><tr><td>合计</td><td></td><td></td><td></td><td>140002.00</td></tr></table>"
    )


class Sources:
    def __init__(self, a, b):
        self.docs = [{"id": "a", "role": "bid", "bidder": "A", "round": "1", "lot": "1"}, {"id": "b", "role": "bid", "bidder": "B", "round": "1", "lot": "1"}]
        self.derived_sources = {doc["id"]: {"chunks": [{"id": doc["id"] + str(i), "content": t, "positions": [[i + 1, 0, 1, 0, 1]]} for i, t in enumerate(texts)]} for doc, texts in zip(self.docs, [a, b])}

    def documents(self, *roles):
        return [d for d in self.docs if d["role"] in roles]

    def pages(self, doc):
        return []

    def evidence(self, doc, page, quote, **extra):
        return {"document_id": doc["id"], "bidder": doc["bidder"], "page": page, "quote": quote, **extra}


def collect(a, b):
    from bid_audit.supplemental_prices import supplement_m15

    primary = [{"bidder": x, "currency": "CNY", "round": "1", "lot": "1", "source_project_id": "P"} for x in ["A", "B"]]
    return supplement_m15(Sources(a, b), {"records": primary, "metrics": {}, "findings": [], "status": "not_applicable", "not_applicable_pairs": [], "limitations": []})


def test_collect_two_priced_configuration_sources_without_inventing_unit_prices():
    d = collect([table()], [table("70004.00", "70004.00")])
    assert len(d["supplemental_quote_observations"]) == 4
    pair = d["supplemental_quote_comparisons"][0]
    assert (pair["n"], pair["m"]) == (2, 0)
    assert pair["unit_pricing_verified"] is False
    assert d["metrics"]["primary_quote_status"] == "not_applicable"
    assert d["status"] == "partial"


def test_within_bid_equality_is_not_cross_bid_agreement():
    d = collect([table()], [])
    assert not d["supplemental_quote_comparisons"]


def test_duplicate_original_copies_retain_sources_without_extra_dimensions():
    d = collect([table(), table()], [table("70004.00", "70004.00")])
    assert len(d["supplemental_quote_observations"]) == 4
    assert d["supplemental_quote_comparisons"][0]["n"] == 2
    a = next(r for r in d["supplemental_quote_observations"] if r["bidder"] == "A")
    assert len(a["evidence"]) == 2


def test_blank_zero_and_ocr_truncation_not_filled_from_total():
    d = collect([table("", "1,336,")], [table("0", "0")])
    assert not d["supplemental_quote_observations"]
    assert d["supplemental_quote_ambiguities"]


def test_conflicting_duplicate_price_does_not_choose_one_silently():
    d = collect([table(), table("70002.00", "70001.00")], [table()])
    assert d["supplemental_quote_conflicts"]
    assert not d["supplemental_quote_comparisons"]


def test_contract_prices_stay_available_but_not_current_quote_alignment():
    d = collect(["类似项目业绩 > 合同货物" + table()], [table()])
    assert len(d["supplemental_quote_observations"]) == 4
    assert not d["supplemental_quote_comparisons"]


def test_yuan_conversion_is_exact_and_explicit_unit_header_can_be_verified():
    d = collect([table("1.2345", "1.2345", "单价(万元)")], [table("12345.00", "12345.00", "单价(元)")])
    pair = d["supplemental_quote_comparisons"][0]
    assert (pair["n"], pair["m"]) == (2, 2)
    assert pair["unit_pricing_verified"] is True


def test_different_currencies_are_not_compared_without_conversion_evidence():
    d = collect([table(header="单价(美元)")], [table(header="单价(元)")])
    assert not d["supplemental_quote_comparisons"]


def test_manufacturer_name_column_is_not_a_second_product_header():
    t = table().replace("设备名称</td><td>单位", "设备名称</td><td>制造商名称</td><td>单位").replace("设备甲</td><td>套", "设备甲</td><td>制造商甲</td><td>套")
    d = collect([t], [t])
    assert len(d["supplemental_quote_observations"]) == 4
    assert d["supplemental_quote_comparisons"][0]["n"] == 2


def test_repeated_side_by_side_headers_remain_ambiguous_sources():
    text = "<table><tr><td>设备名称</td><td>报价(元)</td><td>设备名称</td><td>报价(元)</td></tr><tr><td>设备甲</td><td></td><td>设备甲</td><td>70001</td></tr></table>"
    d = collect([text], [text])
    assert not d["supplemental_quote_observations"]
    assert d["supplemental_quote_ambiguities"]


def test_supplemental_actual_bid_sources_are_indexed_as_comparison_members(monkeypatch):
    from app.experiments.collusion_audit.fact_index import build_fact_index

    row = {"kind": "supplemental_price_vector_for_review", "n": 2, "m": 0, "evidence_refs": ["E1", "E2"]}
    decoded = {
        "summary": {"rule_id": "M15", "metrics": {}},
        "collections": {"findings": [row], "supplemental_quote_comparisons": [row], "evidence": [{"evidence_id": "E1", "document_id": "a", "quote": "报价原文甲"}, {"evidence_id": "E2", "document_id": "b", "quote": "报价原文乙"}]},
        "sources": [{"id": "a", "role": "bid", "path": "a.pdf"}, {"id": "b", "role": "bid", "path": "b.pdf"}, {"id": "q", "role": "quote_detail", "path": "q.pdf"}, {"id": "t", "role": "tender", "path": "t.pdf"}],
    }
    monkeypatch.setattr("app.experiments.collusion_audit.fact_index.unpack_complete_result", lambda packet: decoded)
    index = build_fact_index({})
    assert set(index["comparison_scope"]["document_ids"]) == {"a", "b", "q"}
    assert index["comparison_scope"]["not_compared_document_ids"] == ["t"]
    assert index["findings"][0]["n"] == 2 and index["findings"][0]["m"] == 0


def test_currency_in_value_cell_is_not_silently_inherited_from_primary_cny():
    d = collect([table("70001.00美元", "70001.00美元", "单价")], [table("70001.00元", "70001.00元", "单价")])
    assert not d["supplemental_quote_comparisons"]
    assert {r["currency"] for r in d["supplemental_quote_observations"]} == {"USD", "CNY"}
