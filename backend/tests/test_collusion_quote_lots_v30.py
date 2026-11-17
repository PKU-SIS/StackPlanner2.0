"""Government quote identity must accept balanced lot parentheses without guessing."""

from types import SimpleNamespace

import pytest
from bid_audit import price_rules
from bid_audit.core import digest, dump


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("第1采购包", {"1"}),
        ("第 2 采购包", {"2"}),
        ("第 (1) 采购包", {"1"}),
        ("第（1）采购包", {"1"}),
        ("第 （ ２ ） 采购包", {"2"}),
        ("包号：2\n第 (2) 采购包", {"2"}),
        ("包号：1\n第 (2) 采购包", {"1", "2"}),
        ("第(1采购包", set()),
        ("第1)采购包", set()),
        ("项目名称：设备采购", set()),
    ],
)
def test_quote_lot_numbers_preserves_identity_and_rejects_unbalanced_footer(text, expected):
    assert price_rules.quote_lot_numbers(text) == expected


def fake_quote(tmp_path, monkeypatch, footer, *, declared_lot=None):
    raw = "项目编号：P-2026\n投标人名称：测试设备有限公司\n"
    if declared_lot is not None:
        raw += "包号：" + declared_lot + "\n"
    raw += footer

    class Table:
        bbox = (0, 0, 100, 100)

        def extract(self):
            return [["投标总报价（元）", "交货或服务期"], ["12,000.00", "30天"]]

    class Page:
        def find_tables(self):
            return [Table()]

        def close(self):
            pass

    class PDF:
        pages = [Page()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(price_rules, "open_pdf", lambda _: PDF())
    monkeypatch.setattr(price_rules, "clean_page", lambda page: page)
    context = SimpleNamespace(cache=tmp_path, pages=lambda doc: [{"page": 1, "text": raw}])
    doc = {"id": "quote", "role": "quote_opening", "sha256": "controlled-source-sha", "_path": tmp_path / "quote.pdf"}
    return context, doc


@pytest.mark.parametrize("footer", ["第 (1) 采购包", "第（1）采购包", "第 1 采购包"])
def test_quote_table_reads_balanced_footer_and_preserves_amount(tmp_path, monkeypatch, footer):
    context, doc = fake_quote(tmp_path, monkeypatch, footer)
    result = price_rules.quote_table(context, doc)
    assert result["source_project_id"] == "P-2026"
    assert result["source_bidder"] == "测试设备有限公司"
    assert result["source_lot"] == "1"
    assert result["rows"][0]["cells"]["投标总报价(元)"] == "12,000.00"


def test_quote_table_rejects_conflicting_header_and_footer_lots(tmp_path, monkeypatch):
    context, doc = fake_quote(tmp_path, monkeypatch, "第 (2) 采购包", declared_lot="1")
    with pytest.raises(ValueError, match="ambiguous project/bidder/lot identity"):
        price_rules.quote_table(context, doc)


def test_old_quote_cache_cannot_hide_conflicting_source_identity(tmp_path, monkeypatch):
    context, doc = fake_quote(tmp_path, monkeypatch, "第 (2) 采购包", declared_lot="1")
    old_cache = tmp_path / (digest((doc["sha256"] + "quotes-v5").encode()) + ".quotes.json")
    dump(old_cache, {"source_lot": "1", "rows": []})
    with pytest.raises(ValueError, match="ambiguous project/bidder/lot identity"):
        price_rules.quote_table(context, doc)
