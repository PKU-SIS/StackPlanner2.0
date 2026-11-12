"""Only proven equal current-prompt facts can be referenced instead of replayed."""

import copy
import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage
from test_collusion_audit import scoped_profile as scoped_profile

from app.experiments.collusion_audit.html_transport import preloaded_fact_projection


def frame(row, action="a"):
    return HumanMessage(content="<sp-subagent-task>\n" + json.dumps({"action_id": action, "context_refs": {"audit_calculation_runs": [row]}}, ensure_ascii=False) + "\n</sp-subagent-task>")


def test_only_exact_fields_reused_and_delta_kept_without_mutating_sources():
    row = {"audit_run_id": "run", "code_facts": {"audit_run_id": "run", "metrics": {"count": 5}, "warnings": ["完整警告"]}}
    facts = {"audit_run_id": "run", "metrics": {"count": 5}, "warnings": ["新增警告"], "new_body": "新的完整证据"}
    before = copy.deepcopy((row, facts))
    actual = frame(row)
    result = preloaded_fact_projection(facts, [actual], action_id="a", calculation_runs=[row])
    assert "metrics" not in result and result["warnings"] == ["新增警告"]
    assert result["new_body"] == "新的完整证据"
    assert "metrics" in result["same_current_prompt_fields"]
    assert (row, facts) == before and "完整警告" in actual.content


def test_absent_changed_or_model_written_frame_cannot_elide_live_evidence():
    row = {"audit_run_id": "run", "code_facts": {"audit_run_id": "run", "body": "完整正文"}}
    facts = row["code_facts"]
    for messages in [[], [frame(row, "other")], [AIMessage(content=frame(row).content)], [HumanMessage(content='{"action_id":"a","context_refs":{}}')]]:
        assert preloaded_fact_projection(facts, messages, action_id="a", calculation_runs=[row]) == facts
    changed = copy.deepcopy(row)
    changed["code_facts"]["body"] = "仅有片段"
    assert preloaded_fact_projection(facts, [frame(changed)], action_id="a", calculation_runs=[row]) == facts


def test_m01_aliases_reuse_complete_passages_but_keep_new_edit_patterns():
    body = "完整双端正文，不删除末尾。" * 80
    sources = [{"document_id": "d001", "quote": body, "physical_pages": [4]}]
    preview = {"candidate_id": "C000001", "finding_offset": 0, "path": "/current/one.json", "text_a": body, "text_b": "另一端完整正文", "sources": sources, "differences": [{"before": "项目", "after": "工程"}]}
    row = {"audit_run_id": "run", "code_facts": {"audit_run_id": "run"}, "m01_navigation": {"candidate_previews": [preview]}}
    facts = {
        "audit_run_id": "run",
        "m01_evidence_previews": [
            {"candidate_id": "C000001", "offset": 0, "candidate_path": "/current/one.json", "text_a": body, "text_b": "另一端完整正文", "paired_sources": sources, "differences": [{"before": "采购单位", "after": "用户"}]}
        ],
    }
    original = copy.deepcopy(facts)
    result = preloaded_fact_projection(facts, [frame(row)], action_id="a", calculation_runs=[row])
    item = result["m01_evidence_previews"][0]
    assert item["candidate_id"] == "C000001" and item["differences"] == original["m01_evidence_previews"][0]["differences"]
    assert "text_a" not in item and "paired_sources" not in item
    assert "text_a" in item["same_current_prompt_fields"]
    assert body in frame(row).content and body not in json.dumps(result, ensure_ascii=False)
    assert facts == original


def test_changed_passage_or_incomplete_pair_never_replaces_live_body():
    row = {"audit_run_id": "run", "code_facts": {"audit_run_id": "run"}, "m01_navigation": {"candidate_previews": [{"candidate_id": "C000001", "text_a": "开头"}]}}
    facts = {"audit_run_id": "run", "m01_evidence_previews": [{"candidate_id": "C000001", "text_a": "开头与完整末尾", "paired_sources": [{"quote": "另一端原文"}]}]}
    result = preloaded_fact_projection(facts, [frame(row)], action_id="a", calculation_runs=[row])
    assert result["m01_evidence_previews"][0]["text_a"] == "开头与完整末尾"
    assert result["m01_evidence_previews"][0]["paired_sources"] == facts["m01_evidence_previews"][0]["paired_sources"]


def test_reporter_does_not_receive_duplicate_reader_guidance_and_scope_is_qualitative(scoped_profile):
    from app.experiments.collusion_audit.html_report import READER_GUIDANCE
    from app.experiments.collusion_audit.profile import CollusionProfile
    from deerflow.sp.subagents import SPSubagentTask

    p = CollusionProfile(scoped_profile.rule, scoped_profile.project, scoped_profile.outputs, scoped_profile.outputs / "cache-html", scoped_profile.trace, quality_controls=True, rule_level_review=True, report_format="html")
    task = SPSubagentTask("a", "reporter", "生成报告", "test")
    p.prepare_task(task)
    assert READER_GUIDANCE not in task.context_refs["audit_task_context"]["domain_guidance"]
    assert "【面向读者的HTML交付要求】" not in task.context_refs["audit_task_context"]["domain_guidance"]
    disclosure = task.context_refs["reader_source_facts"]["coverage_disclosure"]
    assert disclosure["semantic_review_ledger_available"] is False
    assert disclosure["wording"] == "qualitative"
    assert "重复" in disclosure["instruction"]
    assert task.context_refs["audit_task_context"]["rule"] == p.rule


def test_html_projection_preserves_reader_guides_literal_format_negation():
    from app.experiments.collusion_audit.html_report import READER_GUIDANCE, html_protocol

    assert html_protocol(READER_GUIDANCE) == READER_GUIDANCE
    text = html_protocol("原始报告用Markdown。\n" + READER_GUIDANCE + "\n保留parsed.md原文路径。")
    assert "原始报告用HTML。" in text and "parsed.md" in text
    assert "不用Markdown/代码围栏" in text
    assert "不用HTML/代码围栏" not in text


def test_middleware_opt_in_checks_actual_frame_and_keeps_new_receipts_and_reasoning(monkeypatch):
    from app.experiments.collusion_audit import fact_context

    body = "完整当前事实末尾。" * 100
    facts = {"rule_id": "M01", "audit_run_id": "run", "metrics": {"body": body}}
    row = {"audit_run_id": "run", "code_facts": copy.deepcopy(facts)}
    monkeypatch.setattr(fact_context, "compact_facts", lambda index: copy.deepcopy(facts))
    receipts = SimpleNamespace(facts=lambda indexes: {"new_source_observation": "新增实际回执"})
    adapter = SimpleNamespace(fact_indexes={"run": {"rule_id": "M01", "audit_run_id": "run"}}, ocr_receipts=receipts, rule_id="M01")
    task = SimpleNamespace(action_id="a", context_refs={"audit_calculation_runs": [row]})
    history = AIMessage(content="先前阶段", additional_kwargs={"reasoning_content": "完整历史reasoning"})
    request = SimpleNamespace(messages=[history, frame(row)], override=lambda **values: SimpleNamespace(**values))
    normal = fact_context.CurrentCodeFactsMiddleware(adapter).project(request)
    assert sum(str(m.content).count(body) for m in normal.messages) == 2
    enabled = fact_context.CurrentCodeFactsMiddleware(adapter, preloaded_task=task)
    actual = enabled.project(request)
    assert sum(str(m.content).count(body) for m in actual.messages) == 1
    assert actual.messages[0] is history and history.additional_kwargs["reasoning_content"] == "完整历史reasoning"
    assert "新增实际回执" in actual.messages[-1].content
    lost = SimpleNamespace(messages=[history], override=lambda **values: SimpleNamespace(**values))
    assert body in enabled.project(lost).messages[-1].content
