"""HTML layout delimiters must not become observed punctuation anomalies."""

from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def vendor(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "vendor/bid_audit_tools"))


def run_source(monkeypatch, html, tender=None):
    from bid_audit import enhanced_text
    from bid_audit.m01_recall import plain

    docs = [{"id": d, "bidder": d, "round": "1", "lot": "1"} for d in ["a", "b"]]
    baseline = [{"id": "t", "round": "1", "lot": "1", "bidder": "t"}] if tender else []
    ctx = SimpleNamespace(documents=lambda *roles: docs if roles == ("bid",) else baseline, evidence=lambda doc, page, quote, **kw: {"document_id": doc["id"], "page": page, "quote": quote, **kw})

    def source(ctx, doc):
        raw = tender if doc["id"] == "t" else html
        yield {"text": plain(raw), "raw": raw, "page": 1, "physical_pages": [1], "chunk_ids": ["c-" + doc["id"]], "positions": [], "source_channel": "ragflow_mineru"}

    monkeypatch.setattr(enhanced_text, "blocks", source)
    return enhanced_text.m08(ctx)


def test_empty_html_cells_do_not_fabricate_shared_double_semicolons(monkeypatch):
    result = run_source(monkeypatch, "<table><tr><td>独立说明</td><td></td><td>正常响应</td></tr></table>")
    assert result["findings"] == [] and result["metrics"]["non_template_anomaly_occurrences"] == 0


def test_original_contiguous_punctuation_inside_cells_is_retained(monkeypatch):
    result = run_source(monkeypatch, "<table><tr><td>自编维护正文；；异常原文</td><td>其他内容</td></tr></table>")
    assert len(result["findings"]) == 1
    assert result["findings"][0]["punctuation"] == ";;"
    assert any("；；" in e["quote"] for e in result["findings"][0]["evidence"])


def test_real_template_punctuation_is_excluded_with_original_source_proof(monkeypatch):
    html = "<table><tr><td>公开原文；；内容保留</td></tr></table>"
    result = run_source(monkeypatch, html, tender=html)
    assert result["findings"] == [] and len(result["template_exclusions"]) == 2
    assert all("；；" in r["template_source"]["evidence"][0]["quote"] for r in result["template_exclusions"])


def test_inline_markup_preserves_real_adjacency_but_cell_boundaries_do_not(monkeypatch):
    real = run_source(monkeypatch, "<p>原文;<b>;</b>异常</p>")
    assert len(real["findings"]) == 1
    separate = run_source(monkeypatch, "<table><tr><td>原文；</td><td>；另一字段</td></tr></table>")
    assert separate["findings"] == []


def test_docx_cells_are_separate_original_units_without_joined_delimiters(tmp_path):
    from bid_audit.punctuation_sources import punctuation_units
    from docx import Document

    p = tmp_path / "source.docx"
    d = Document()
    row = d.add_table(rows=1, cols=2).rows[0]
    row.cells[0].text = "第一字段；"
    row.cells[1].text = "；第二字段"
    d.save(p)
    units = list(punctuation_units({"_path": p}, []))
    assert [u["text"] for u in units] == ["第一字段；", "；第二字段"]
    assert all(u["page"] is None and u["source_channel"] == "original_text" for u in units)


def test_fullwidth_contents_dot_leaders_keep_the_existing_exclusion(monkeypatch):
    result = run_source(monkeypatch, "<table><tr><td>章节名称．．．．．．．．．7</td></tr></table>")
    assert result["findings"] == []


def test_continued_contents_dot_leaders_are_excluded_without_a_directory_heading(monkeypatch):
    result = run_source(monkeypatch, "章节甲 .................................................................10\n章节乙 .................................................................14\n下一章节")
    assert result["findings"] == []
