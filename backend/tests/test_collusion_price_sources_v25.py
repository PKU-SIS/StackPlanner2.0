"""Synthetic price-source provenance and literal constraints, no project GT."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def vendor(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "vendor/bid_audit_tools"))


@pytest.mark.parametrize(
    "text,scope",
    [
        ("两套组成不同，单套采购限价为38万元。", "per_unit_or_item"),
        ("采购包预算总价为760000元。", "package_or_total"),
        ("每台报价不得超过人民币38万元。", "per_unit_or_item"),
        ("分项最高投标限价为760000元。", "unspecified"),
    ],
)
def test_scoped_limits_keep_original_amount_and_context(text, scope):
    from bid_audit.price_sources import price_constraints

    rows = list(price_constraints(text))
    assert rows and all(row["text"] in text for row in rows)
    assert any(row["scope_hint"] == scope for row in rows)
    assert any("38" in row["text"] or "760000" in row["text"] for row in rows)


def test_generic_cap_clause_does_not_invent_a_numeric_cap():
    from bid_audit.price_sources import price_constraints

    row = next(price_constraints("投标报价不得超过最高限价。"))
    assert not row["contains_arabic_number"]
    assert row["text"] == "投标报价不得超过最高限价。"


def test_multirow_title_header_keeps_the_actual_header_end():
    from bid_audit.price_sources import configuration_header

    assert configuration_header([["第一组设备配置"], ["产品名称", "型号", "数量", "单位"], ["处理器", "A-100", "1", "台"]]) == 1
    assert configuration_header([["说明", "评审标准"], ["产品技术说明", "详细参数"]]) is None


class Context:
    def __init__(self):
        self.docs = [{"id": "t", "role": "tender", "bidder": ""}, {"id": "b", "role": "bid", "bidder": "supplier"}]
        self.derived_sources = {}
        for doc in self.docs:
            title = "采购需求" if doc["id"] == "t" else "类似项目业绩 > 合同货物"
            content = title + "第一套配置如下：<table><tr><td colspan=3>设备配置</td></tr><tr><td>产品名称</td><td>数量</td><td>单位</td></tr><tr><td>处理器</td><td>1</td><td>台</td></tr></table>"
            self.derived_sources[doc["id"]] = {"chunks": [{"id": doc["id"] + "1", "content": content, "positions": [[2, 0, 1, 0, 1]]}]}

    def documents(self, *roles):
        return [doc for doc in self.docs if doc["role"] in roles]

    def pages(self, doc):
        return []

    def evidence(self, doc, page, quote, **extra):
        return {"document_id": doc["id"], "page": page, "quote": quote, **extra}


def test_tender_baseline_and_past_contract_stay_distinct_full_sources():
    from bid_audit.price_sources import price_context

    result = price_context(Context(), {"metrics": {}})
    tables = result["configuration_observations"]
    assert {row["document_role"] for row in tables} == {"bid", "tender"}
    assert next(row for row in tables if row["document_id"] == "t")["source_scope"] == "tender_requirement"
    past = next(row for row in tables if row["document_id"] == "b")
    assert past["source_scope"] == "contract_or_performance_attachment_unverified"
    assert past["rows"] == [["处理器", "1", "台"]]
    assert "处理器" in past["evidence"][0]["quote"]
    assert result["configuration_contexts"]


def test_budget_label_and_adjacent_scalar_cell_preserve_the_value():
    from bid_audit.price_sources import price_constraints

    text = "预算金额；人民币980000元；有效期；30日"
    row = next(price_constraints(text))
    assert "980000元" in row["text"] and row["text"] in text
    assert "30日" not in row["text"]
