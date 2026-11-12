"""Geometry and evidence navigation contracts; synthetic documents only."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def vendor(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "vendor/bid_audit_tools"))


def test_original_pdf_column_geometry_survives_changed_subcolumn_count(tmp_path, monkeypatch):
    from bid_audit.geometric_tables import extract_responses

    class Row:
        def __init__(self, bbox, cells):
            self.bbox, self.cells = bbox, cells

    class Page:
        def __init__(self, page):
            self.number = page
            self.bbox = (0, 0, 100, 100) if page == 1 else (100, 0, 300, 100)

        def filter(self, callback):
            return self

        def find_tables(self):
            x0, _, x1, _ = self.bbox
            w = x1 - x0
            header = ["序号", "采购文件规定的技术要求", "投标文件响应的具体内容", "是否偏离"]
            cells = [(x0 + a * w, 0, x0 + b * w, 10) for a, b in [(0, 0.1), (0.1, 0.5), (0.5, 0.9), (0.9, 1)]]
            body = [(a, 10, b, 40) for a, _, b, _ in cells]
            rows = [Row((x0, 0, x1, 10), cells), Row((x0, 10, x1, 40), body)] if self.number == 1 else [Row((x0, 10, x1, 40), body * 6)]
            return [SimpleNamespace(bbox=self.bbox, rows=rows, extract=lambda: [header, ["1", "req", "resp", "负偏离"]] if self.number == 1 else [["fragment"] * 23])]

        def crop(self, bbox):
            x0, _, x1, _ = self.bbox
            center = ((bbox[0] + bbox[2]) / 2 - x0) / (x1 - x0)
            text = "1" if center < 0.1 else "1)精度：≤4mm；" if center < 0.5 else "1)精度：" + str(4 + self.number) + "mm；" if center < 0.9 else "负偏离"
            return SimpleNamespace(extract_text=lambda **kwargs: text)

        def close(self):
            pass

    class PDF:
        pages = [Page(1), Page(2)]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("pdfplumber.open", lambda path: PDF())
    cache = tmp_path / "cache"
    cache.mkdir()
    ctx = SimpleNamespace(cache=cache, warnings=[], evidence=lambda d, p, q, **kw: {"document_id": d["id"], "page": p, "quote": q, **kw})
    doc = {"id": "a", "sha256": "sha", "_path": tmp_path / "a.pdf", "bidder": "A", "round": "1", "lot": "1"}
    rows = extract_responses(ctx, doc, [1, 2])
    assert len(rows) == 2
    assert "5mm" in rows[0]["response"] and "6mm" in rows[1]["response"]
    assert rows[1]["evidence"][0]["page"] == 2
    assert rows[1]["evidence"][0]["source_channel"] == "original_geometry"


def test_compact_index_contains_exact_edit_fields_and_global_counts(tmp_path):
    from app.experiments.collusion_audit.general_delivery import materialize

    findings = [{"kind": "stable_cross_document_replacement", "before": "交付", "after": "维护", "occurrence_count": 3, "document_a": "a", "document_b": "b", "text_a": "完整甲", "text_b": "完整乙", "evidence_refs": []} for _ in range(2)]
    nav = materialize(tmp_path, "/current", {"summary": {"run_id": "M02_test"}}, findings, [])
    index = json.loads((tmp_path / "candidates/index.json").read_text())
    assert index["candidates"][0]["before"] == "交付"
    assert index["candidates"][0]["document_pair"] == ["a", "b"]
    assert nav["summary_statistics"]["by_before"]["交付"] == 2
    assert nav["summary_statistics"]["by_document_pair"]["a|b"] == 2
    assert nav["summary_statistics"]["counts_are_risk_verdicts"] is False


def test_row_interval_uses_cell_intersection_not_merged_rowspan_union():
    from bid_audit.geometric_tables import row_interval

    row = SimpleNamespace(bbox=(0, 0, 100, 100), cells=[(0, 0, 10, 100), (10, 20, 50, 30), (50, 20, 90, 30), (90, 0, 100, 100)])
    assert row_interval(row) == (20, 30)


def test_complete_original_readout_replaces_truncated_ocr_but_keeps_both_sources():
    from bid_audit.enhanced_tables import merge_source_views

    base = {"document_id": "a", "bidder": "A", "round": "1", "lot": "1", "section": "器材", "table_family": "technical", "item_no": "11", "requirement": "1) 自动调节：根据外部信号调整亮度，也可手动校正。", "physical_pages": [3]}
    ocr = {**base, "response": "自动调节）", "evidence": [{"document_id": "a", "page": 3, "source_channel": "ragflow_mineru"}]}
    original = {**base, "response": base["requirement"], "evidence": [{"document_id": "a", "page": 3, "source_channel": "original_geometry"}]}
    result = merge_source_views([ocr, original])
    assert len(result) == 1 and result[0]["response"] == base["requirement"]
    assert len(result[0]["extraction_variants"]) == 2


def test_equivalent_decimal_measurements_produce_same_numeric_signature():
    from bid_audit.enhanced_tables import numeric_checks

    assert numeric_checks("精度：≤4mm", "精度：5.0mm")[0]["signature"] == numeric_checks("精度：≤4.0mm", "精度：5mm")[0]["signature"]


def test_copy_of_requirement_pair_is_preserved_as_source_observation(monkeypatch):
    from bid_audit import enhanced_tables

    q = "1) 控制器支持正常图像与特殊图像的双路同步显示；"
    rr = [{"document_id": d, "bidder": d, "round": "1", "lot": "1", "section": "器材", "requirement": q, "response": q, "evidence": []} for d in ["a", "b"]]
    monkeypatch.setattr(enhanced_tables, "response_rows", lambda ctx: rr)
    ctx = SimpleNamespace(params={}, _response_coverage_v20=[], documents=lambda *roles: [])
    result = enhanced_tables.m03(ctx)
    assert result["findings"] == [] and len(result["requirement_copy_pairs"]) == 1
    assert result["requirement_copy_pairs"][0]["document_a"] == "a"


def test_fact_index_keeps_word_edit_fields_for_deterministic_navigation(tmp_path):
    from test_collusion_quality_v18 import packet

    from app.experiments.collusion_audit.fact_index import build_fact_index

    f = {"kind": "stable_cross_document_replacement", "before": "交付", "after": "维护", "occurrence_count": 3, "evidence_refs": []}
    index = build_fact_index(packet(tmp_path, "M02", [f], []))
    assert index["findings"][0]["before"] == "交付"
    assert index["findings"][0]["occurrence_count"] == 3


def test_requirement_copy_observations_are_discoverable_in_large_result(tmp_path):
    from app.experiments.collusion_audit.general_delivery import materialize

    rows = [{"section": "device", "requirement": "spec " + str(i), "response_a": "left", "response_b": "right", "document_a": "a", "document_b": "b"} for i in range(15)]
    nav = materialize(tmp_path, "/current", {"summary": {"run_id": "M03_test"}}, [], [], collections={"requirement_copy_pairs": rows})
    extra = nav["supplementary_collections"]["requirement_copy_pairs"]
    assert extra["source_observation_index_complete"] and len(extra["source_observation_index"]) == 15
    assert extra["source_observation_index"][14]["requirement"] == "spec 14"


@pytest.mark.parametrize("part", ["resources", "fonts", "xobjects"])
def test_null_optional_font_resource_preserves_gap_and_available_fonts(part):
    from bid_audit.pdf_rules import resource_fonts
    from pypdf.generic import NullObject

    calls = []
    font = {"/BaseFont": "/ABCDEF+Serif"}
    resources = {"/Font": {"/F1": font}}
    if part == "resources":
        resources = NullObject()
    elif part == "fonts":
        resources["/Font"] = NullObject()
    else:
        resources["/XObject"] = NullObject()
    result = list(resource_fonts(resources, on_missing=lambda: calls.append(True)))
    assert calls
    assert len(result) == (1 if part == "xobjects" else 0)


def test_registered_collection_overview_provides_whole_set_edit_statistics(tmp_path):
    import asyncio

    from app.experiments.collusion_audit.tools import AuditTools
    from app.experiments.tender_audit.observability import AuditTrace

    run = "M02_20261109T000000_0123456789"
    out = tmp_path / "outputs"
    folder = out / "bid_audit" / run
    folder.mkdir(parents=True)
    findings = [{"kind": "edit", "before": "left", "after": "right", "document_a": "a", "document_b": "b", "occurrence_count": 3} for _ in range(2)]
    (folder / "result.json").write_text(json.dumps({"collections": {"findings": {"file": "findings.jsonl", "count": 2}}}))
    (folder / "findings.jsonl").write_text("".join(json.dumps(r) + "\n" for r in findings))
    toolset = AuditTools("M02", tmp_path, out, tmp_path / "cache", AuditTrace(tmp_path / "trace"), rule_level_review=True, quality_controls=True)
    toolset.runs.append({"run_id": run})
    tool = next(t for t in toolset.build() if t.name == "audit_collection_overview")
    result = json.loads(asyncio.run(tool.ainvoke({"run_id": run})))
    assert result["summary_statistics"]["by_before"] == {"left": 2}
    assert result["summary_statistics"]["by_document_pair"] == {"a|b": 2}
    assert result["summary_statistics"]["counts_are_risk_verdicts"] is False


def test_source_access_keeps_tender_original_reachable_without_mineru(tmp_path):
    from test_collusion_quality_v18 import packet

    from app.experiments.collusion_audit.fact_index import build_fact_index, compact_facts

    p = packet(tmp_path, "M02", [], [], metrics={"input_channel_coverage": [{"document_id": "d003", "role": "tender", "original_text_characters": 120, "mineru_chunk_count": 0}]})
    from app.experiments.collusion_audit.full_results import pack_complete_result, unpack_complete_result

    source = unpack_complete_result(p)["sources"]
    source[2].update(role="tender", path="tender.docx")
    (tmp_path / "sources.json").write_text(json.dumps(source))
    index = build_fact_index(pack_complete_result(tmp_path))
    access = compact_facts(index)["source_access"]
    doc = next(d for d in access["documents"] if d["document_id"] == "d003")
    assert doc["original_read_input"] == "d003"
    assert doc["mineru_chunk_count"] == 0 and doc["original_text_characters"] == 120
    assert "project_read" in doc["original_source_tools_available"]
    assert "d003" in index["comparison_scope"]["not_compared_document_ids"]


def test_docx_original_tools_work_without_knowledge_store(tmp_path):
    import asyncio

    from docx import Document

    from app.experiments.collusion_audit.tools import AuditTools
    from app.experiments.tender_audit.observability import AuditTrace

    doc = Document()
    doc.add_paragraph("通用安装条件原文。")
    doc.add_paragraph("现场条件由采购人确认。")
    doc.save(tmp_path / "original.docx")
    manifest = {"schema_version": "1.0", "project_id": "synthetic", "documents": [{"id": "tender", "role": "tender", "path": "original.docx"}]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    tools = AuditTools("M02", tmp_path, tmp_path / "outputs", tmp_path / "cache", AuditTrace(tmp_path / "trace"), rule_level_review=True, quality_controls=True).build()
    read = next(t for t in tools if t.name == "read_file")
    result = json.loads(asyncio.run(read.ainvoke({"file_path": "tender"})))
    assert "通用安装条件原文" in result["text"]
    search = next(t for t in tools if t.name == "project_search")
    found = json.loads(asyncio.run(search.ainvoke({"query": "现场条件", "document_id": "tender"})))
    assert found["total"] > 0
