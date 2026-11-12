"""Generic synthetic contracts; no project/history answer literals."""

import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def vendor(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "vendor/bid_audit_tools"))


def test_html_grid_expands_spans_and_preserves_blank_cells():
    from bid_audit.structured_text import html_tables

    grid = html_tables('<table><tr><th rowspan="2">序号</th><th colspan="2">响应</th></tr><tr><td>要求</td><td>实际</td></tr><tr><td>1</td><td></td><td>不适用</td></tr></table>')[0]["rows"]
    assert grid[1] == ["序号", "要求", "实际"]
    assert grid[2] == ["1", "", "不适用"]


def test_row_width_change_uses_actual_deviation_anchor_not_old_width():
    from bid_audit.enhanced_tables import parse_grid

    grid = [
        ["序号", "标的名称", "参数性质", "采购文件规定的技术和服务要求", "投标文件响应的具体内容", "型号", "是否偏离", "备注"],
        ["1", "", "", "（一）精密设备", "", "", "", ""],
        ["2", "", "1) 精度：≤4mm；", "1) 精度：5mm；", "TYPE-A", "负偏离", "/"],
    ]
    rows, _ = parse_grid(grid)
    assert len(rows) == 1
    assert "≤4mm" in rows[0]["requirement"] and "5mm" in rows[0]["response"]
    assert "精密设备" in rows[0]["section"]
    assert rows[0]["declared_deviation"] == "负偏离"


def test_no_response_column_is_not_invented_from_requirement_only_table():
    from bid_audit.enhanced_tables import parse_grid

    rows, _ = parse_grid([["序号", "性质", "要求"], ["1", "▲", "观察范围：2-120mm"]])
    assert rows == []


def test_equal_requirements_in_different_equipment_sections_do_not_cross_align():
    from bid_audit.enhanced_tables import align_rows

    rows = [dict(bidder="A", round="1", lot="1", section="设备甲", requirement="观察范围：2-120mm", response="3-120mm"), dict(bidder="B", round="1", lot="1", section="设备乙", requirement="观察范围：2-120mm", response="3-120mm")]
    index, _ = align_rows(rows)
    assert all(len(rr) == 1 for rr in index.values())


def test_numeric_range_narrowing_and_same_direction_different_magnitude_are_distinct():
    from bid_audit.enhanced_tables import numeric_checks

    a = numeric_checks("观察范围：2-120mm", "观察范围：3-120mm")
    b = numeric_checks("观察范围：2-120mm", "观察范围：5-120mm")
    assert a[0]["relation"] == "response_interval_narrower_than_required"
    assert a[0]["signature"] != b[0]["signature"]


def test_multiple_dimensions_pair_by_label_and_unit_not_whole_row_single_number():
    from bid_audit.enhanced_tables import numeric_checks

    rows = numeric_checks("夹角：≥150°；通道：≥4mm；", "夹角：145°；通道：3mm；")
    assert len(rows) == 2 and all(r["unfavorable"] is True for r in rows)


def test_plain_point_value_difference_is_not_automatically_negative():
    from bid_audit.enhanced_tables import numeric_checks

    r = numeric_checks("近距离观察：2mm", "近距离观察：3mm")[0]
    assert r["relation"] == "different_point_value" and r["unfavorable"] is None


def test_repeated_edit_patterns_are_discovered_without_actor_dictionary():
    from bid_audit.enhanced_text import edit_patterns

    passages = [
        {
            "document_a": "a",
            "document_b": "b",
            "text_a": "交付团队将在现场检查设备并填写交付记录。交付团队的交付员负责填写异常记录。",
            "text_b": "维护团队将在现场检查设备并填写交付记录。维护团队的维护员负责填写异常记录。",
            "evidence": [{"document_id": "a"}, {"document_id": "b"}],
        }
    ]
    patterns = edit_patterns(passages)
    assert any(p["before"] == "交付" and p["after"] == "维护" and p["occurrence_count"] >= 2 for p in patterns)


def test_single_sided_repetition_is_not_called_shared_error():
    from bid_audit.enhanced_text import paired_anomalies

    pair = {"document_a": "a", "document_b": "b", "text_a": "本单位单位将负责全部交付与现场复核工作。", "text_b": "本机构将负责全部交付与现场复核工作。", "evidence": [{"document_id": "a"}, {"document_id": "b"}]}
    findings = paired_anomalies([pair])
    assert any(r["kind"] == "single_side_edit_artifact_for_review" for r in findings)
    assert not any(r["kind"] == "shared_error_hypothesis" for r in findings)


def test_generic_candidate_handoff_retains_body_tail_and_paired_sources(tmp_path):
    from app.experiments.collusion_audit.general_delivery import materialize

    row = {"kind": "stable_cross_document_replacement", "text_a": "通用段落" * 80 + "交付员", "text_b": "通用段落" * 80 + "维护员", "evidence_refs": ["E1", "E2"], "occurrence_count": 4}
    evidence = [{"evidence_id": "E1", "document_id": "a", "quote": row["text_a"]}, {"evidence_id": "E2", "document_id": "b", "quote": row["text_b"]}]
    nav = materialize(tmp_path, "/current", {"summary": {"run_id": "M02_run"}}, [row], evidence)
    assert nav["candidate_previews"][0]["candidate"]["text_a"].endswith("交付员")
    assert json.loads((tmp_path / "candidates/C000001.json").read_text())["evidence"][1]["quote"].endswith("维护员")


def test_raw_pdf_syntax_windows_exclude_compressed_stream_payload():
    from bid_audit.enhanced_pdf import syntax_windows

    visible = b"/Type /Example /Meaning (a sufficiently long structural object dictionary repeated across PDF files) "
    payload = b"SECRET_STREAM_PAYLOAD_" * 12
    raw = b"1 0 obj\n<<" + visible + b">>\nstream\n" + payload + b"\nendstream\nendobj\n"
    windows = list(syntax_windows(raw, 80))
    assert windows and all(b"SECRET_STREAM_PAYLOAD" not in row["bytes"] for row in windows)
    assert all(row["length"] == 80 and row["object_id"] == 1 for row in windows)


def test_pdf_marker_counts_are_not_claimed_as_incremental_save_count():
    from bid_audit.enhanced_pdf import marker_features

    r = marker_features(b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\nstartxref\n0\n%%EOF\n")
    assert r["eof_line_markers"] == 1
    assert r["verified_incremental_save_count"] is None


def test_general_source_code_has_no_project_answers_or_history_error_dictionary():
    root = Path(__file__).resolve().parents[2]
    files = ["structured_text.py", "enhanced_tables.py", "enhanced_text.py", "enhanced_pdf.py", "enhanced_rules.py", "price_sources.py", "supplemental_prices.py"]
    text = "\n".join((root / "vendor/bid_audit_tools/bid_audit" / name).read_text() for name in files)
    assert all(token not in text for token in ["九州通", "天医", "项目师", "采购单位单位", "5938500", "0877-26GZTP1ZC0146"])


def test_ocr_model_input_receipt_requires_full_original_chunk_not_file_persistence():
    from types import SimpleNamespace

    from langchain_core.messages import HumanMessage

    from app.experiments.collusion_audit.source_receipts import OCRReadReceipts

    row = {"id": "c", "content": "只来自原始扫描材料的完整OCR段落。", "positions": [[3, 0, 1, 0, 1]]}
    kb = SimpleNamespace(index={"documents": {"d001": {}}}, rows=lambda doc: [row])
    receipts = OCRReadReceipts(kb)
    receipts.observe_model_input([HumanMessage(content=json.dumps({"content": row["content"][:5]}, ensure_ascii=False))])
    assert not receipts.seen["d001"]
    receipts.observe_model_input([HumanMessage(content=json.dumps({"ocr_endpoints": [{"document_id": "d001", "ocr_chunks": [{"chunk_id": "c", "content": row["content"]}]}]}, ensure_ascii=False))])
    assert receipts.seen["d001"] == {"c"}


def test_font_resource_invalid_entry_does_not_hide_other_valid_fonts():
    from bid_audit.pdf_rules import resource_fonts
    from pypdf.generic import NullObject

    class Ref:
        def __init__(self, v):
            self.v = v

        def get_object(self):
            return self.v

    font = {"/BaseFont": "/AAAAAA+Example"}
    notices = []
    rows = list(resource_fonts({"/Font": {"/bad": Ref(NullObject()), "/good": Ref(font)}}, on_missing=lambda: notices.append(True)))
    assert rows == [("/good", font)] and notices


def test_malformed_overlapping_ocr_spans_preserve_cell_text_and_warn():
    from bid_audit.structured_text import html_tables

    table = html_tables('<table><tr><td>A</td><td rowspan="2">B</td></tr><tr><td colspan="2">真实要求</td><td>真实响应</td></tr></table>')[0]
    assert table["layout_warnings"]
    assert "真实要求" in table["rows"][1] and "真实响应" in table["rows"][1]


def test_split_column_orphan_joins_only_unique_same_page_requirement():
    from bid_audit.enhanced_tables import recover_orphan

    row = {"response": "2) 精度：5mm；", "section": "精密器材", "physical_pages": [3]}
    candidates = [{"requirement": "2) 精度：≤4mm；", "section": "精密器材", "physical_pages": [3], "item_no": "22", "evidence": []}]
    recovered = recover_orphan(row, candidates)
    assert recovered["requirement"] == candidates[0]["requirement"]
    ambiguous = candidates + [{**candidates[0], "requirement": "2) 精度：≤3mm；", "item_no": "23"}]
    assert recover_orphan(row, ambiguous) is None


def test_parameter_only_response_is_preserved_for_split_column_recovery():
    from bid_audit.enhanced_tables import parse_grid

    rows, _ = parse_grid([["", "", "2) 精度：5mm；", "TYPE-X", "负偏离", "P3"]])
    assert len(rows) == 1 and rows[0]["requirement"] == "" and rows[0]["response"] == "2) 精度：5mm；"


def test_same_negative_label_different_actual_magnitudes_remain_review_evidence():
    from bid_audit.enhanced_tables import same_direction_pairs

    rows = [{"document_id": d, "bidder": d, "round": "1", "lot": "1", "section": "器材", "requirement": "精度：≤4mm", "response": "精度：" + v + "mm", "declared_deviation": "负偏离", "evidence": []} for d, v in [("a", "5"), ("b", "7")]]
    result = same_direction_pairs(rows)
    assert len(result) == 1 and result[0]["same_actual_numeric_values"] is False
    assert result[0]["requires_agent_review"] is True


def test_actual_runner_enhanced_m04_returns_both_sides_from_ocr(tmp_path):
    from bid_audit.core import digest
    from bid_audit.runner import run_rule

    docs = []
    parsed = {}
    project = tmp_path / "project"
    project.mkdir()
    for doc_id in ["a", "b"]:
        p = project / (doc_id + ".txt")
        p.write_text("扫描材料")
        sha = digest(p.read_bytes())
        docs.append({"id": doc_id, "path": p.name, "role": "bid", "bidder": doc_id, "expected_sha256": sha})
        html = "<table><tr><td>要求</td><td>实际响应</td><td>是否偏离</td></tr><tr><td>1)精度：≤4mm；</td><td>1)精度：5mm；</td><td>负偏离</td></tr></table>"
        parsed[doc_id] = {"source_sha256": sha, "chunks": [{"id": doc_id, "content": html, "positions": [[1, 0, 1, 0, 1]]}]}
    (project / "manifest.json").write_text(json.dumps({"schema_version": "1.0", "project_id": "synthetic", "documents": docs}))
    result = run_rule("M04", project / "manifest.json", tmp_path / "out", permitted_root=project, parsed_texts=parsed)
    assert result["status"] != "error", result
    assert result["finding_count"] == 1
    root = Path(result["result_directory"])
    row = json.loads((root / "findings.jsonl").read_text().splitlines()[0])
    assert {r["document_id"] for r in row["comparisons"]} == {"a", "b"}
    refs = [json.loads(s) for s in (root / "evidence.jsonl").read_text().splitlines()]
    assert all(e.get("source_channel") == "ragflow_mineru" for e in refs)


def test_template_punctuation_exclusion_keeps_actual_template_source(tmp_path):
    from bid_audit.runner import run_rule

    project = tmp_path / "project"
    project.mkdir()
    text = "设备维护交付记录由双方代表在检查后签字归档；；全部清单应保存以供复查。"
    docs = []
    for name, role in [("a", "bid"), ("b", "bid"), ("t", "tender")]:
        p = project / (name + ".txt")
        p.write_text(text)
        docs.append({"id": name, "path": p.name, "role": role, **({"bidder": name} if role == "bid" else {})})
    (project / "manifest.json").write_text(json.dumps({"schema_version": "1.0", "project_id": "synthetic", "documents": docs}))
    result = run_rule("M08", project / "manifest.json", tmp_path / "out", parsed_texts={})
    assert result["finding_count"] == 0, result
    exclusions = [json.loads(x) for x in (Path(result["result_directory"]) / "template_exclusions.jsonl").read_text().splitlines()]
    assert exclusions and all(x.get("template_source", {}).get("evidence_refs") for x in exclusions)


def test_performance_blank_rows_are_observations_not_ineligible_company_verdicts():
    from bid_audit.enhanced_text import performance_table

    observation = performance_table([["序号", "项目名称", "客户单位", "合同金额"], ["1", "无", "", ""], ["2", "", "", ""]])
    assert observation["nonempty_data_row_count"] == 0
    assert observation["qualification_verdict"] is None
    assert performance_table([["日期", "说明"], ["2024", "说明正文"]]) is None


@pytest.mark.parametrize("rule", ["M01", "M04"])
def test_registered_large_result_delivery_preserves_m01_and_supplementary_evidence(tmp_path, monkeypatch, rule):
    import asyncio

    import bid_audit.runner
    from test_collusion_quality_v18 import packet

    from app.experiments.collusion_audit.tools import AuditTools
    from app.experiments.tender_audit.observability import AuditTrace

    run_id = rule + "_20261109T000000_0123456789"

    def fake_run(rule_id, manifest, output_root, **kwargs):
        folder = Path(output_root) / run_id
        folder.mkdir(parents=True)
        finding = {
            "kind": "shared_unfavorable_response" if rule == "M04" else "non_template_shared_passage",
            "text_a": "独立方案" * 12000,
            "text_b": "对应方案" * 12000,
            "document_a": "d001",
            "document_b": "d002",
            "non_template_shared_chars": 300,
            "evidence_refs": ["E1", "E2"],
        }
        ev = [{"evidence_id": "E1", "document_id": "d001", "page": 1, "quote": "完整原句甲"}, {"evidence_id": "E2", "document_id": "d002", "page": 2, "quote": "完整原句乙"}]
        packet(folder, rule, [finding], ev)
        summary = json.loads((folder / "result.json").read_text())
        summary["run_id"] = run_id
        if rule == "M04":
            row = {"kind": "co_declared_negative_for_review", "evidence_refs": ["E1", "E2"], "comparisons": [{"response": "5mm"}, {"response": "7mm"}]}
            (folder / "same_direction_review_pairs.jsonl").write_text(json.dumps(row) + "\n")
            summary["collections"]["same_direction_review_pairs"] = {"file": "same_direction_review_pairs.jsonl", "count": 1}
        (folder / "result.json").write_text(json.dumps(summary))
        return summary

    monkeypatch.setattr(bid_audit.runner, "run_rule", fake_run)
    adapter = AuditTools(rule, tmp_path, tmp_path / "out", tmp_path / "cache", AuditTrace(tmp_path / "trace"), rule_level_review=True, quality_controls=True)
    tool = next(t for t in adapter.build() if t.name == "audit_" + rule.lower())
    result = json.loads(asyncio.run(tool.ainvoke({})))
    assert result.get("status") != "error", result
    if rule == "M01":
        assert result["code_result_delivery"] == "indexed_large_M01"
    else:
        assert result["result_navigation"]["supplementary_collections"]["same_direction_review_pairs"]["shown"] == 1
        assert "result_navigation" not in result["code_facts"]
        assert adapter.fact_indexes[run_id]["result_navigation"]["supplementary_collections"]


def test_edit_handoff_keeps_anomalous_word_boundary_not_only_longest_passage():
    from bid_audit.enhanced_text import edit_patterns

    normal = {"document_a": "a", "document_b": "b", "text_a": "交付人员将在现场填写记录并组织培训。", "text_b": "维护人员将在现场填写记录并组织培训。", "non_template_shared_chars": 600, "evidence": []}
    anomaly = {"document_a": "a", "document_b": "b", "text_a": "交付组交付组将在现场填写完整检查记录。", "text_b": "维护组交付组将在现场填写完整检查记录。", "non_template_shared_chars": 80, "evidence": []}
    patterns = edit_patterns([normal, anomaly])
    target = next(p for p in patterns if p["before"] == "交付" and p["after"] == "维护")
    assert target["text_a"] == anomaly["text_a"]
