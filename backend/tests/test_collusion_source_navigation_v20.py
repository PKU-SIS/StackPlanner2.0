"""Generic binary representation and immutable current-code provenance."""

import asyncio
import json
from pathlib import Path

import pytest
from test_collusion_audit import scoped_profile as scoped_profile


@pytest.fixture(autouse=True)
def vendor(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "vendor/bid_audit_tools"))


def test_raw_window_representatives_cover_document_sets_and_resource_classes():
    from bid_audit.enhanced_pdf import raw_representatives

    def row(docs, hint, offset):
        return {
            "sha256": str(offset),
            "decoded_preview": "full shared syntax " + str(offset),
            "resource_type_hint": hint,
            "occurrence_counts_by_document": {d: 1 for d in docs},
            "evidence": [{"document_id": d, "object_id": offset, "raw_byte_offset": offset + 10, "byte_length": 80} for d in docs],
        }

    source = [row(["a", "b"], "syntax", i) for i in range(10)] + [row(["b", "c"], "font", 40)]
    reps = raw_representatives(source)
    assert len(reps) == 2
    assert reps[0]["window_hash_groups"] == 10
    assert reps[1]["document_ids"] == ["b", "c"] and reps[1]["decoded_preview"].endswith("40")
    assert {p["document_id"] for p in reps[1]["representative_source_positions"]} == {"b", "c"}
    assert reps[1]["evidence"] == source[-1]["evidence"]


def test_binary_representative_source_index_retains_all_actual_byte_examples(tmp_path):
    from app.experiments.collusion_audit.general_delivery import materialize

    rows = [
        {
            "document_ids": ["a", "b"],
            "resource_type_hint": "syntax",
            "decoded_preview": "full bytes " + str(i),
            "sha256": str(i),
            "window_hash_groups": 2,
            "representative_source_positions": [{"document_id": "a", "raw_byte_offset": i}, {"document_id": "b", "raw_byte_offset": i + 10}],
        }
        for i in range(14)
    ]
    nav = materialize(tmp_path, "/current", {"summary": {"run_id": "M12_test"}}, [], [], collections={"raw_syntax_representatives": rows})
    source = nav["supplementary_collections"]["raw_syntax_representatives"]["source_observation_index"]
    assert len(source) == 14 and source[-1]["decoded_preview"] == "full bytes 13"
    assert len(source[-1]["representative_source_positions"]) == 2


def test_only_hash_verified_registered_calculation_files_are_reference_sources(tmp_path):
    from app.experiments.collusion_audit.tools import AuditTools
    from app.experiments.tender_audit.observability import AuditTrace

    adapter = AuditTools("M12", tmp_path, tmp_path / "outputs", tmp_path / "cache", AuditTrace(tmp_path / "trace"), rule_level_review=True, quality_controls=True)
    run = "M12_20261109T000000_0123456789"
    root = adapter.outputs / "bid_audit" / run
    root.mkdir(parents=True)
    result = {"run_id": run, "collections": {"findings": {"file": "findings.jsonl", "count": 1}}}
    adapter.runs.append(result)
    (root / "candidates").mkdir()
    good = root / "candidates/C000001.json"
    good.write_text(json.dumps({"model": "MODEL-A711"}))
    forged = root / "candidates/C999999.json"
    forged.write_text(json.dumps({"model": "FAKE-B999"}))
    adapter.register_calculation_sources(result)
    read = next(t for t in adapter.build() if t.name == "read_file")
    good_path = "/mnt/user-data/outputs/bid_audit/" + run + "/candidates/C000001.json"
    observed = json.loads(asyncio.run(read.ainvoke({"file_path": good_path})))
    assert observed["source"] == "verified_current_calculation_source"
    assert "MODEL-A711" in adapter.source_reference_tokens()
    bad = json.loads(asyncio.run(read.ainvoke({"file_path": good_path.replace("C000001", "C999999")})))
    assert bad.get("source") != "verified_current_calculation_source"
    assert "FAKE-B999" not in adapter.source_reference_tokens()
    good.write_text(json.dumps({"model": "FORGED-X777"}))
    changed = json.loads(asyncio.run(read.ainvoke({"file_path": good_path})))
    assert changed.get("source") != "verified_current_calculation_source"
    assert "FORGED-X777" not in adapter.source_reference_tokens()
    draft = adapter.outputs / "model-draft.md"
    draft.write_text("DRAFT-N888")
    asyncio.run(read.ainvoke({"file_path": "/mnt/user-data/outputs/model-draft.md"}))
    assert "DRAFT-N888" not in adapter.source_reference_tokens()


def test_original_image_capability_available_for_any_quality_rule(scoped_profile):
    assert scoped_profile.extensions().image_tool_names is None
    scoped_profile.quality_controls = True
    assert scoped_profile.rule_id != "M06"
    assert scoped_profile.extensions().image_tool_names == ("view_image",)
    assert "view_image" in {tool.name for tool in scoped_profile.tools}


@pytest.mark.parametrize("template,verified", [("功能:完整相同的通用规范要求。", True), ("另外一个无关的模板条目。", False)])
def test_own_requirement_copy_is_only_template_verified_with_actual_source(monkeypatch, template, verified):
    from types import SimpleNamespace

    from bid_audit import enhanced_tables

    q = "2）功能：完整相同的通用规范要求。"
    rows = [{"document_id": d, "bidder": d, "round": "1", "lot": "1", "section": "设备", "requirement": q, "response": q, "evidence": []} for d in ["a", "b"]]
    source = {"id": "t", "round": "1", "lot": "1", "role": "tender"}
    block = {"text": template, "physical_pages": [2], "chunk_ids": ["chunk"], "source_channel": "ragflow_mineru"}
    monkeypatch.setattr(enhanced_tables, "response_rows", lambda ctx: rows)
    monkeypatch.setattr(enhanced_tables, "blocks", lambda ctx, doc: [block])
    monkeypatch.setattr(enhanced_tables, "evidence", lambda ctx, doc, b, quote, **kw: {"document_id": doc["id"], "quote": quote, **kw})
    ctx = SimpleNamespace(params={}, _response_coverage_v20=[], documents=lambda *roles: [source])
    result = enhanced_tables.m03(ctx)
    record = result["requirement_copy_pairs"][0]
    assert record["requirement_reference_verified"] is verified
    assert result["metrics"]["own_requirement_copy_pairs_observed"] == 1
    assert result["metrics"]["template_only_pairs_excluded"] == int(verified)
    assert result["metrics"]["unverified_requirement_copy_pairs"] == int(not verified)
    if verified:
        assert any(e["document_id"] == "t" for e in record["evidence"])


def test_template_match_does_not_strip_a_leading_decimal_measurement(monkeypatch):
    from types import SimpleNamespace

    from bid_audit import enhanced_tables

    doc = {"id": "t", "round": "1", "lot": "1"}
    block = {"text": "0mm连续精度测试要求。", "physical_pages": [2], "chunk_ids": [], "source_channel": "original_text"}
    monkeypatch.setattr(enhanced_tables, "blocks", lambda ctx, doc: [block])
    monkeypatch.setattr(enhanced_tables, "evidence", lambda *args, **kwargs: {})
    ctx = SimpleNamespace(documents=lambda *roles: [doc])
    assert enhanced_tables.template_requirement_proof(ctx, "5.0mm连续精度测试要求。", "1", "1") == []


def test_bounded_aligned_response_view_delivers_every_full_pair_with_sources(tmp_path):
    from app.experiments.collusion_audit.general_delivery import materialize

    source = []
    findings = []
    for i in range(25):
        a, b = "A" + str(i), "B" + str(i)
        source.extend([{"evidence_id": a, "document_id": "a", "page": i + 1}, {"evidence_id": b, "document_id": "b", "physical_pages": [i + 2]}])
        findings.append(
            {
                "kind": "aligned_response_similarity",
                "section": "device",
                "requirement": "完整要求" + str(i),
                "response_a": "甲完整响应末尾" + str(i),
                "response_b": "乙完整响应末尾" + str(i),
                "document_a": "a",
                "document_b": "b",
                "evidence_refs": [a, b],
            }
        )
    nav = materialize(tmp_path, "/current", {"summary": {"run_id": "M03_test"}}, findings, source)
    pairs = nav["supplementary_collections"]["paired_response_observations"]
    assert pairs["source_observation_index_complete"] and pairs["total"] == 25
    assert pairs["source_observation_index"][-1]["response_a"] == "甲完整响应末尾24"
    assert pairs["source_observation_index"][-1]["response_b"] == "乙完整响应末尾24"
    assert len(pairs["source_observation_index"][-1]["source_positions"]) == 2
    assert (tmp_path / "candidates/response-pairs.json").is_file()


def test_oversized_response_view_is_explicitly_not_all_in_context(tmp_path):
    from app.experiments.collusion_audit.general_delivery import materialize

    rows = [{"kind": "aligned_response_similarity", "requirement": "完整要求", "response_a": "甲" * 7000, "response_b": "乙" * 7000, "document_a": "a", "document_b": "b", "evidence_refs": []} for _ in range(25)]
    nav = materialize(tmp_path, "/current", {"summary": {"run_id": "M03_large"}}, rows, [])
    pairs = nav["supplementary_collections"]["paired_response_observations"]
    assert pairs["total"] == 25 and pairs["source_observation_index_complete"] is False
    assert not pairs["source_observation_index"]
    assert (tmp_path / "candidates/response-pairs.json").is_file()


def test_skewed_repeated_contexts_keep_both_document_sides_in_preview(tmp_path):
    from app.experiments.collusion_audit.general_delivery import materialize

    contexts = [{"document_id": "a", "text": "甲方原文" + str(i)} for i in range(10)] + [{"document_id": "b", "text": "乙方完整原文"}]
    evidence = [{"evidence_id": "E" + str(i), "document_id": r["document_id"], "quote": r["text"]} for i, r in enumerate(contexts)]
    row = {"kind": "shared_entity", "entity_type": "person", "value": "合同联系人", "contexts": contexts, "evidence_refs": [r["evidence_id"] for r in evidence]}
    before = json.dumps(row, ensure_ascii=False)
    nav = materialize(tmp_path, "/current", {"summary": {"run_id": "M09_test"}}, [row], evidence)
    preview = nav["candidate_previews"][0]
    assert {r["document_id"] for r in preview["candidate"]["contexts"]} == {"a", "b"}
    assert {r["document_id"] for r in preview["evidence"]} == {"a", "b"}
    assert preview["explicit_subset_fields"]["contexts"] == {"total": 11, "shown": 6}
    full = json.loads((tmp_path / "candidates/C000001.json").read_text())
    assert len(full["candidate"]["contexts"]) == 11 and len(full["evidence"]) == 11
    assert json.dumps(row, ensure_ascii=False) == before


def test_policy_source_index_can_locate_each_form_and_its_unverified_body_status(tmp_path):
    from app.experiments.collusion_audit.general_delivery import materialize

    rows = [
        {
            "kind": "policy_form_observation",
            "document_id": "a",
            "form_titles": ["通用政策声明" + str(i)],
            "default_markers": ["不适用"] if i == 14 else [],
            "blank_cell_count": i,
            "form_body_status": "candidate_body_not_verified",
            "table_rows": [[["原始单元格"]]],
            "evidence_refs": ["E1"],
        }
        for i in range(15)
    ]
    evidence = [{"evidence_id": "E1", "document_id": "a", "physical_pages": [4]}]
    nav = materialize(tmp_path, "/current", {"summary": {"run_id": "M05_test"}}, [], evidence, collections={"form_observations": rows})
    source = nav["supplementary_collections"]["form_observations"]["source_observation_index"]
    assert source[14]["form_titles"] == ["通用政策声明14"]
    assert source[14]["default_markers"] == ["不适用"]
    assert source[14]["form_body_status"] == "candidate_body_not_verified"
    assert source[14]["source_positions"][0]["physical_pages"] == [4]
    assert "table_rows" not in source[14]


def test_template_context_index_keeps_source_and_template_locations(tmp_path):
    from app.experiments.collusion_audit.general_delivery import materialize

    row = {"kind": "template_punctuation_exclusion", "context": "设备采用通用流程；；继续运行。", "matched_local_context": "采用通用流程;;继续", "punctuation": "；；", "evidence_refs": ["E1"], "template_source": {"evidence_refs": ["E2"]}}
    evidence = [{"evidence_id": "E1", "document_id": "a", "page": 3}, {"evidence_id": "E2", "document_id": "t", "page": 2}]
    nav = materialize(tmp_path, "/current", {"summary": {"run_id": "M08_test"}}, [], evidence, collections={"template_exclusions": [row]})
    source = nav["supplementary_collections"]["template_exclusions"]["source_observation_index"][0]
    assert source["context"] == row["context"] and source["matched_local_context"] == row["matched_local_context"]
    assert {r["document_id"] for r in source["source_positions"]} == {"a", "t"}
