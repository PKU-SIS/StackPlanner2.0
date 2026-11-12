"""Navigation and assertions are raw facts, never hidden model judgements."""

import json

import pytest

from app.experiments.collusion_audit.full_results import pack_complete_result


def packet(tmp_path, rule, findings, evidence, *, metrics=None, records=None):
    cols = {"findings": findings, "evidence": evidence}
    if records is not None:
        cols["records"] = records
    for key, rows in cols.items():
        (tmp_path / (key + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in rows))
    sources = [{"id": "d001", "role": "bid", "path": "bid.pdf", "bidder": "A"}, {"id": "d002", "role": "bid", "path": "other.pdf", "bidder": "B"}, {"id": "d003", "role": "quote_detail", "path": "quote.pdf"}]
    (tmp_path / "sources.json").write_text(json.dumps(sources))
    (tmp_path / "result.json").write_text(json.dumps({"rule_id": rule, "run_id": "run", "status": "ok", "metrics": metrics or {}, "collections": {k: {"file": k + ".jsonl", "count": len(v)} for k, v in cols.items()}}))
    return pack_complete_result(tmp_path)


def test_font_index_distinguishes_zero_match_from_not_computed_and_unexamined_documents(tmp_path):
    from app.experiments.collusion_audit.fact_index import build_fact_index

    p = packet(
        tmp_path,
        "M11",
        [{"kind": "shared_font_name_or_prefix", "field": "subset_prefix", "value": "ABCDEF", "evidence_refs": ["E000001", "E000002"]}],
        [{"evidence_id": "E000001", "document_id": "d001", "page": 1}, {"evidence_id": "E000002", "document_id": "d002", "page": 3}],
        metrics={"font_resource_occurrences": 7, "embedded_font_occurrences": 4},
    )
    d = build_fact_index(p)
    assert d["font_comparison"]["byte_comparison_performed"] is True
    assert d["font_comparison"]["embedded_font_occurrences"] == 4
    assert d["font_comparison"]["identical_embedded_byte_groups"] == 0
    assert d["comparison_scope"]["document_ids"] == ["d001", "d002"]
    assert d["comparison_scope"]["not_compared_document_ids"] == ["d003"]
    assert d["findings"][0]["evidence_by_document"]["d002"]["physical_page_ranges"] == [[3, 3]]
    assert "risk_verdict" not in d


def test_image_index_counts_pairs_and_distances_exactly_and_binds_both_endpoints(tmp_path):
    from app.experiments.collusion_audit.fact_index import build_fact_index, pair_sources

    ev = [{"evidence_id": "E000001", "document_id": "d001", "page": 4, "image_name": "/A"}, {"evidence_id": "E000002", "document_id": "d002", "page": 7, "image_name": "/B"}]
    p = packet(tmp_path, "M06", [{"kind": "perceptual_image_candidate", "dhash_distance": 0, "evidence_refs": ["E000001", "E000002"]}], ev)
    d = build_fact_index(p)
    assert d["image_comparison"]["distance_counts"] == {"0": 1}
    assert pair_sources(p, 0) == ev
    with pytest.raises(ValueError):
        pair_sources(p, 1)


def test_large_reference_lists_do_not_overwhelm_index_and_fields_stay_in_full_packet(tmp_path):
    from app.experiments.collusion_audit.fact_index import build_fact_index

    ev = [{"evidence_id": f"E{i:06d}", "document_id": "d001", "page": i, "quote": "original untouched"} for i in range(1, 1001)]
    p = packet(tmp_path, "M11", [{"kind": "shared_font_name_or_prefix", "value": "ABCDEF+Font", "evidence_refs": [r["evidence_id"] for r in ev]}], ev)
    d = build_fact_index(p)
    assert d["findings"][0]["evidence_ref_count"] == 1000
    assert len(json.dumps(d)) < 8000
    assert "original untouched" in json.dumps(p)


def test_visual_assertion_requires_actual_paired_receipts_and_exact_rule_grade():
    from app.experiments.collusion_audit.report_checks import assertion_errors

    assert assertion_errors({"evidence_level": "PS-B", "visual_reviewed_candidates": [0]}, expected_grade="辅助线索", rule_id="M06", displayed_pairs=set())
    assert assertion_errors({"evidence_level": "辅助线索", "visual_reviewed_candidates": [0]}, expected_grade="辅助线索", rule_id="M06", displayed_pairs={0}) == []
    assert assertion_errors({"evidence_level": "辅助线索", "visual_reviewed_candidates": []}, expected_grade="辅助线索", rule_id="M06", displayed_pairs=set()) == []


def test_scope_checks_never_require_every_candidate_to_be_judged():
    from app.experiments.collusion_audit.report_checks import assertion_errors

    assert assertion_errors({"evidence_level": "辅助线索", "visual_reviewed_candidates": [5]}, expected_grade="辅助线索", rule_id="M06", displayed_pairs={5, 8, 9}) == []


def test_report_grade_is_checked_even_in_markdown_and_visual_count_must_match():
    from app.experiments.collusion_audit.report_checks import report_text_errors

    assert report_text_errors("证据等级：PS-B（辅助线索）", {}, expected_grade="辅助线索", rule_id="M11")
    assert report_text_errors("**证据等级**：PS-B（辅助线索）", {}, expected_grade="辅助线索", rule_id="M11")
    assert report_text_errors("视觉判读候选数：5", {"visual_reviewed_candidates": [1]}, expected_grade="辅助线索", rule_id="M06")
    assert report_text_errors("视觉判读候选数：1", {"visual_reviewed_candidates": [1]}, expected_grade="辅助线索", rule_id="M06") == []


def test_central_fact_context_contains_actual_values_without_mutating_state(tmp_path):
    from types import SimpleNamespace

    from langchain_core.messages import HumanMessage

    from app.experiments.collusion_audit.fact_context import CurrentCodeFactsMiddleware
    from app.experiments.collusion_audit.fact_index import build_fact_index

    p = packet(tmp_path, "M13", [], [], records=[{"bidder": "A", "brand": "RAW_BRAND", "total_yuan": "1234.56"}])
    adapter = SimpleNamespace(fact_indexes={"run": build_fact_index(p)})
    original = [HumanMessage(content="full original user rule")]

    class Request:
        messages = original

        def override(self, **kw):
            return SimpleNamespace(**kw)

    response = CurrentCodeFactsMiddleware(adapter).project(Request())
    assert "RAW_BRAND" in response.messages[-1].content
    assert "1234.56" in response.messages[-1].content
    assert len(original) == 1


def test_pair_tool_records_both_sources_and_only_actual_model_submission_counts(tmp_path, monkeypatch):
    import asyncio
    import base64
    import hashlib

    from langchain_core.messages import HumanMessage

    from app.experiments.collusion_audit.fact_index import build_fact_index
    from app.experiments.collusion_audit.tools import AuditTools
    from app.experiments.collusion_audit.vision import build_pair_view_tool
    from app.experiments.tender_audit.observability import AuditTrace

    runid = "M06_20261108T000000_0123456789"
    outputs = tmp_path / "outputs"
    root = outputs / "bid_audit" / runid
    root.mkdir(parents=True)
    ev = [{"evidence_id": "E000001", "document_id": "d001", "page": 4, "image_name": "/A"}, {"evidence_id": "E000002", "document_id": "d002", "page": 7, "image_name": "/B"}]
    p = packet(root, "M06", [{"kind": "perceptual_image_candidate", "dhash_distance": 0, "evidence_refs": ["E000001", "E000002"]}], ev)
    summary = json.loads((root / "result.json").read_text())
    summary["run_id"] = runid
    (root / "result.json").write_text(json.dumps(summary))
    p = pack_complete_result(root)
    adapter = AuditTools("M06", tmp_path, outputs, tmp_path / "cache", AuditTrace(tmp_path / "trace"), rule_level_review=True, quality_controls=True)
    adapter.runs.append({"run_id": runid})
    adapter.fact_indexes[runid] = build_fact_index(p)

    def render(a, document_id, page, image_name):
        raw = document_id.encode()
        return {"document_id": document_id, "physical_page": page, "image_name": image_name, "image_path": "/mnt/user-data/outputs/" + document_id + ".png", "display_png_sha256": hashlib.sha256(raw).hexdigest()}, base64.b64encode(
            raw
        ).decode()

    monkeypatch.setattr("app.experiments.collusion_audit.vision.render_original", render)
    command = asyncio.run(build_pair_view_tool(adapter).ainvoke({"name": "view_image_pair", "args": {"run_id": runid, "candidate_offset": 0}, "id": "call", "type": "tool_call"}))
    assert len(command.update["viewed_images"]) == 2
    assert adapter.displayed_pairs() == set()
    images = [HumanMessage(content=[{"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(d.encode()).decode()}}]) for d in ["d001", "d002"]]
    adapter.register_submitted_images(images[:1])
    assert adapter.displayed_pairs() == set()
    adapter.register_submitted_images(images)
    assert adapter.displayed_pairs() == {0}


def test_explicit_contract_uses_host_grade_and_only_real_ids_and_does_not_override_new_human(tmp_path):
    from app.experiments.collusion_audit.profile import CollusionProfile
    from app.experiments.tender_audit.observability import AuditTrace
    from deerflow.sp.subagents import SPSubagentTask

    project = tmp_path / "project"
    project.mkdir()
    (project / "manifest.json").write_text(json.dumps({"documents": [{"id": "d001", "role": "bid", "path": "bid.pdf"}], "project_id": "synthetic"}))
    profile = CollusionProfile(
        {"合并规则ID": "M11", "规则名称": "synthetic", "证据等级": "电子文件辅助线索"},
        project,
        tmp_path / "outputs",
        tmp_path / "cache",
        AuditTrace(tmp_path / "trace"),
        rule_level_review=True,
        quality_controls=True,
        request_run_id="run",
    )
    task = SPSubagentTask("a", "reporter", "report SHA-256 TimesNewRomanPS-BoldMT", "report", run_id="run")
    task.context_refs["latest_user_input"] = profile.user_input()
    task.context_refs["report_requirements"] = {"required_literal_tokens": ["SHA-256", "PS-B"]}
    profile.explicit_report_contract(task, [{"audit_run_id": "M11_20261108T000000_0123456789"}])
    assert task.report_reference_contract.required_literals == ("M11", "M11_20261108T000000_0123456789")
    assert "PS-B" not in task.context_refs["report_requirements"]["required_literal_tokens"]
    newer = SPSubagentTask("b", "reporter", "report", "report", run_id="run")
    newer.context_refs["latest_user_input"] = "a real new human instruction"
    profile.explicit_report_contract(newer, [])
    assert newer.report_reference_contract is None


def test_rag_tools_scope_document_ids_at_schema_boundary_without_changing_remote_mapping():
    from types import SimpleNamespace

    from pydantic import ValidationError

    from app.experiments.collusion_audit.knowledge_tools import build_knowledge_tools

    index = {"documents": {"d001": {"dataset_id": "remote-dataset", "document_id": "remote-document"}}}
    adapter = SimpleNamespace(quality_controls=True, knowledge=SimpleNamespace(index=index), ledger=None)
    tools = {t.name: t for t in build_knowledge_tools(adapter)}
    assert tools["knowledge_read"].args_schema.model_validate({"document_id": "d001"}).document_id == "d001"
    for name, args in [("knowledge_read", {}), ("ragflow_retrieve", {"question": "generic source question"})]:
        with pytest.raises(ValidationError):
            tools[name].args_schema.model_validate({"document_id": "remote-document", **args})
    assert index["documents"]["d001"]["document_id"] == "remote-document"


def test_identical_display_pixels_keep_both_registered_source_endpoints(tmp_path):
    import base64
    import hashlib

    from langchain_core.messages import HumanMessage

    from app.experiments.collusion_audit.tools import AuditTools
    from app.experiments.tender_audit.observability import AuditTrace

    adapter = AuditTools("M06", tmp_path, tmp_path / "outputs", tmp_path / "cache", AuditTrace(tmp_path / "trace"), rule_level_review=True, quality_controls=True)
    raw = b"identical original display pixels"
    sha = hashlib.sha256(raw).hexdigest()
    for doc in ["d001", "d002"]:
        adapter.register_rendered_image({"document_id": doc, "physical_page": 1, "image_name": "/A", "display_png_sha256": sha})
    adapter.register_submitted_images([HumanMessage(content=[{"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(raw).decode()}}])])
    assert adapter.submitted_images == {("d001", 1, "/A"), ("d002", 1, "/A")}


@pytest.mark.parametrize(
    "text",
    [
        "子集前缀为PDF生成工具的确定性输出。\n后续说明：生成机制尚未验证。",
        "真实字体字节复用（已由来源验证排除）：零组，否定跨主体嵌入字体文件级复用。",
    ],
)
def test_font_report_rejects_unverified_mechanism_and_absolute_reuse_exclusion(text):
    from app.experiments.collusion_audit.report_checks import report_text_errors

    assert report_text_errors(text, {}, expected_grade="辅助线索", rule_id="M11")


def test_font_report_allows_conditional_mechanisms_and_scoped_zero_findings():
    from app.experiments.collusion_audit.report_checks import report_text_errors

    assert report_text_errors("子集前缀可能为确定性工具输出，但生成机制未验证。\n当前成功提取并比较的字体中未发现相同字节组，不能据此排除共同制作来源或覆盖缺口。", {}, expected_grade="辅助线索", rule_id="M11") == []
