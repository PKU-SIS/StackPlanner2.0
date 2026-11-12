"""Trusted OCR input and source-sensitive cache, using synthetic originals."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def vendor(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "vendor/bid_audit_tools"))


def test_ocr_corpus_only_contains_clean_original_fields_and_matching_hash():
    from app.experiments.collusion_audit.m01_corpus import parsed_sources

    doc = {"id": "b", "role": "bid", "sha256": "sha"}
    ctx = SimpleNamespace(docs=[doc, {"id": "q", "role": "quote_detail", "sha256": "qsha"}])
    kb = SimpleNamespace(
        index={"documents": {"b": {}, "q": {}}},
        document=lambda d: {"source_sha256": "sha"},
        rows=lambda d: [{"id": "c", "content": "仅原文", "positions": [[2, 0, 1, 0, 1]], "questions": ["HISTORICAL_ANSWER"], "important_keywords": ["SECRET"]}],
    )
    result = parsed_sources(ctx, kb)
    assert set(result) == {"b"}
    assert result["b"]["chunks"] == [{"id": "c", "content": "仅原文", "positions": [[2, 0, 1, 0, 1]]}]
    assert "SECRET" not in json.dumps(result) and "HISTORICAL_ANSWER" not in json.dumps(result)
    kb.document = lambda d: {"source_sha256": "wrong"}
    with pytest.raises(ValueError, match="hash"):
        parsed_sources(ctx, kb)


def test_calculation_cache_identity_includes_actual_ocr_content(tmp_path):
    from bid_audit.core import digest

    from app.experiments.collusion_audit.cache import calculate_cached

    project = tmp_path / "project"
    project.mkdir()
    docs = []
    for i in range(2):
        p = project / f"{i}.txt"
        p.write_text("扫描图")
        docs.append({"id": f"d{i}", "path": p.name, "role": "bid", "bidder": f"C{i}", "round": "1", "lot": "1", "expected_sha256": digest(p.read_bytes())})
    (project / "manifest.json").write_text(json.dumps({"schema_version": "1.0", "project_id": "synthetic", "documents": docs}))

    def corpus(word):
        return {d["id"]: {"source_sha256": d["expected_sha256"], "chunks": [{"id": d["id"], "content": word, "positions": [[1, 0, 1, 0, 1]]}]} for d in docs}

    def run(folder, word):
        return calculate_cached("M01", project, tmp_path / folder, tmp_path / "parse", tmp_path / "cache", parsed_texts=corpus(word))

    original = "设备使用前应当对全部关键连接逐项完成确认并由双方代表在交付记录上签署确认意见。"
    a = run("a", original)
    b = run("b", original)
    c = run("c", original + "新增的事实来源内容。")
    assert not a["cache"]["hit"] and b["cache"]["hit"] and not c["cache"]["hit"]
    assert a["cache"]["key"] == b["cache"]["key"] != c["cache"]["key"]


def test_central_m01_receives_actual_paired_excerpt_and_difference_without_historical_prompt(tmp_path):
    from test_collusion_quality_v18 import packet

    from app.experiments.collusion_audit.fact_index import build_fact_index, compact_facts

    finding = {
        "kind": "non_template_shared_passage",
        "text_a": "甲方团队提供独立巡检",
        "text_b": "乙方团队提供独立巡检",
        "document_a": "d001",
        "document_b": "d002",
        "match_mode": "near_copy",
        "non_template_shared_chars": 30,
        "differences": [{"operation": "replace", "before": "甲方", "after": "乙方"}],
        "evidence_refs": ["E000001", "E000002"],
    }
    ev = [{"evidence_id": "E000001", "document_id": "d001", "page": 8, "quote": "甲方团队提供独立巡检"}, {"evidence_id": "E000002", "document_id": "d002", "page": 14, "quote": "乙方团队提供独立巡检"}]
    facts = compact_facts(build_fact_index(packet(tmp_path, "M01", [finding], ev)))
    text = json.dumps(facts, ensure_ascii=False)
    assert "甲方团队提供独立巡检" in text and "乙方团队提供独立巡检" in text
    assert "replace" in text and "E000001" in text


def test_search_terms_and_literal_mode_are_explicit_and_newlines_do_not_cause_false_absence():
    from app.experiments.collusion_audit.source_search import search_text

    s = "设备安装之后，\n双方应共同执行检查。另有预算金额与最高限价条款。"
    assert search_text(s, "设备安装之后，双方应共同执行检查。", "literal")
    assert search_text(s, "预算金额 最高限价", "terms")
    assert not search_text(s, "预算金额 不存在的词", "terms")
    assert not search_text(s, "预算金额 最高限价", "literal")


def test_real_project_search_supports_terms_mode(tmp_path):
    import asyncio

    from app.experiments.collusion_audit.tools import AuditTools
    from app.experiments.tender_audit.observability import AuditTrace

    project = tmp_path / "project"
    project.mkdir()
    (project / "a.txt").write_text("项目预算金额为一百万元，另有独立的最高限价约定。")
    (project / "manifest.json").write_text(json.dumps({"schema_version": "1.0", "project_id": "fake", "documents": [{"id": "a", "path": "a.txt", "role": "bid", "bidder": "A"}]}))
    adapter = AuditTools("M01", project, tmp_path / "outputs", tmp_path / "cache", AuditTrace(tmp_path / "trace"))
    search = next(t for t in adapter.build() if t.name == "project_search")
    result = json.loads(asyncio.run(search.ainvoke({"query": "预算金额 最高限价", "mode": "terms"})))
    assert result["total"] == 1 and result["items"][0]["matching_mode"] == "terms"


def test_large_m01_delivery_is_explicit_and_all_candidates_remain_addressable(tmp_path):
    from app.experiments.collusion_audit.m01_delivery import materialize_candidates

    findings = [
        {"kind": "non_template_shared_passage", "text_a": "独立方案" + str(i), "text_b": "对应方案" + str(i), "document_a": "d1", "document_b": "d2", "non_template_shared_chars": 45, "evidence_refs": ["E1", "E2"]} for i in range(53)
    ]
    packet = {"summary": {"run_id": "M01_run", "collections": {"findings": {"count": 53}}}, "collections": {}}
    result = materialize_candidates(tmp_path, "/mnt/user-data/outputs/bid_audit/M01_run", packet, findings, [])
    assert result["candidate_count"] == 53
    assert result["preview_count"] < result["candidate_count"]
    assert result["complete_code_result_in_this_message"] is False
    index = json.loads((tmp_path / "m01_candidates/index.json").read_text())
    assert len(index["candidates"]) == 53
    assert json.loads((tmp_path / "m01_candidates/C000053.json").read_text())["candidate"]["text_a"] == "独立方案52"
    assert result["preview_is_not_full_review"] is True


def test_any_indexed_candidate_can_be_read_and_foreign_run_is_rejected(tmp_path):
    import asyncio

    from app.experiments.collusion_audit.m01_delivery import build_candidate_tool, materialize_candidates
    from app.experiments.collusion_audit.tools import AuditTools
    from app.experiments.tender_audit.observability import AuditTrace

    rid = "M01_20261108T010203_0123456789"
    adapter = AuditTools("M01", tmp_path, tmp_path / "out", tmp_path / "cache", AuditTrace(tmp_path / "trace"), rule_level_review=True)
    adapter.runs.append({"run_id": rid})
    root = adapter.result_root(rid)
    root.mkdir(parents=True)
    fs = [{"text_a": "完整独立正文" + str(i), "text_b": "完整对应正文" + str(i)} for i in range(11)]
    materialize_candidates(root, "/mnt/user-data/outputs/bid_audit/" + rid, {"summary": {"run_id": rid}}, fs, [])
    tool = build_candidate_tool(adapter)
    result = json.loads(asyncio.run(tool.ainvoke({"run_id": rid, "candidate_ids": ["C000011"]})))
    assert result["candidates"][0]["candidate"]["text_a"] == "完整独立正文10"
    bad = json.loads(asyncio.run(tool.ainvoke({"run_id": "M01_20261107T010203_0123456789", "candidate_ids": ["C000011"]})))
    assert bad["status"] == "error"


def test_priority_is_a_generic_hint_not_automatic_public_source_exclusion():
    from app.experiments.collusion_audit.m01_delivery import review_features

    a = {"text_a": "安排安装调试和现场培训，详细维护方案。", "text_b": "安排安装调试和现场培训，详细维护方案。", "non_template_shared_chars": 100, "differences": []}
    b = {"text_a": "公共信用报告及信用中国报告说明", "text_b": "公共信用报告及信用中国报告说明", "non_template_shared_chars": 100, "differences": []}
    assert review_features(a)[1] > review_features(b)[1]
    assert review_features(b)[0] == "possible_public_material"


def test_m01_context_does_not_receive_inapplicable_image_pair_receipt_fields():
    from app.experiments.collusion_audit.fact_context import CurrentCodeFactsMiddleware

    receipts = SimpleNamespace(facts=lambda _: {"ocr_pages_with_complete_chunks": {"d001": [4]}, "candidate_offsets_with_ocr_at_both_endpoints": []})
    adapter = SimpleNamespace(rule_id="M01", fact_indexes={"run": {"rule_id": "M01", "audit_run_id": "run", "findings": []}}, ocr_receipts=receipts)
    request = SimpleNamespace(messages=[], override=lambda **kw: SimpleNamespace(**kw))
    result = CurrentCodeFactsMiddleware(adapter).project(request)
    assert "ocr_pages_with_complete_chunks" in result.messages[-1].content
    assert "candidate_offsets_with_ocr_at_both_endpoints" not in result.messages[-1].content
    assert "actually_submitted_image_pair_offsets" not in result.messages[-1].content


def test_high_priority_passages_survive_full_body_handoff_without_tail_loss(tmp_path):
    from app.experiments.collusion_audit.fact_index import compact_facts
    from app.experiments.collusion_audit.m01_delivery import evidence_preview

    prefix = "安装调试和现场培训的方案说明" * 25
    a, b = prefix + "交付工程人员逐项复核设备记录", prefix + "交付项目人员逐项复核设备记录"
    row = {"text_a": a, "text_b": b, "non_template_shared_chars": 300, "differences": [], "evidence_refs": ["E1", "E2"]}
    evidence = {"E1": {"document_id": "a", "quote": a, "evidence_id": "E1"}, "E2": {"document_id": "b", "quote": b, "evidence_id": "E2"}}
    packet = evidence_preview(row, evidence)
    assert packet["text_a"] == a and packet["text_b"] == b
    assert packet["sources"][0]["quote"] == a
    nav = {**row, "offset": 0, "review_priority": 1, "paired_sources": list(evidence.values())}
    compact = compact_facts({"rule_id": "M01", "findings": [nav]})
    assert compact["m01_evidence_previews"][0]["text_a"] == a
    assert compact["m01_evidence_previews"][0]["paired_sources"][1]["quote"] == b
    assert compact["m01_preview_scope"]["shown_is_candidate_subset_not_clipped_text"] is True
