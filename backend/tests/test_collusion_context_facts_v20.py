"""Avoid duplicating actual code evidence in delegation transport and live pin."""

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from test_collusion_audit import scoped_profile as scoped_profile


@pytest.fixture
def profile(scoped_profile):
    from app.experiments.collusion_audit.profile import CollusionProfile

    p = CollusionProfile({**scoped_profile.rule, "合并规则ID": "M02"}, scoped_profile.project, scoped_profile.outputs, scoped_profile.outputs / "cache", scoped_profile.trace, rule_level_review=True, quality_controls=True)
    body = "完整源证据正文，末尾原文必须保留。" * 100
    nav = {
        "delivery": "indexed_large_rule_result",
        "candidate_count": 31,
        "candidate_index_path": "/current/candidates/index.json",
        "complete_code_result_path": "/current/complete-code-result.json",
        "candidate_previews": [{"candidate": {"text_a": body, "text_b": "另一端完整原文。"}}],
        "supplementary_collections": {"extra": {"total": 2, "full_collection_path": "/current/extra.jsonl", "examples": [{"record": body}], "source_observation_index": [{"offset": 0, "requirement": "索引项"}]}},
    }
    run = {"run_id": "M02_20261109T000000_0123456789", "status": "partial", "collections": {"findings": 31}, "result_navigation": nav, "code_facts": {"rule_id": "M02", "result_navigation": nav}}
    p.adapters.runs = [run, dict(run)]
    p.adapters.fact_indexes[run["run_id"]] = {"rule_id": "M02", "audit_run_id": run["run_id"], "findings": [], "result_navigation": nav}
    return p, body


def test_quality_delegation_keeps_metadata_and_single_canonical_source_copy(profile):
    from app.experiments.collusion_audit.fact_context import CurrentCodeFactsMiddleware
    from deerflow.sp.subagents import SPSubagentTask

    p, body = profile
    before = json.dumps(p.adapters.runs, ensure_ascii=False)
    task = SPSubagentTask("a", "coder", "核对当前规则", "test")
    p.prepare_task(task)
    refs = task.context_refs["audit_calculation_runs"]
    assert len(refs) == 1
    assert refs[0]["result_navigation"]["candidate_index_path"] == "/current/candidates/index.json"
    assert refs[0]["result_navigation"]["supplementary_collections"]["extra"]["full_collection_path"] == "/current/extra.jsonl"
    assert body not in json.dumps(refs, ensure_ascii=False)
    assert task.context_refs["audit_task_context"]["rule"] == p.rule
    assert task.subagent_type == "coder"
    history = AIMessage(content="先前代理回复", additional_kwargs={"reasoning_content": "完整历史reasoning保留"})
    request = SimpleNamespace(messages=[history, HumanMessage(content=json.dumps(task.context_refs, ensure_ascii=False))], override=lambda **kwargs: SimpleNamespace(**kwargs))
    projected = CurrentCodeFactsMiddleware(p.adapters).project(request)
    text = "\n".join(str(m.content) for m in projected.messages)
    assert projected.messages[0] is history
    assert body in text
    assert text.count(body) == 2  # one candidate body and one complete supplemental example, both canonical
    assert json.dumps(p.adapters.runs, ensure_ascii=False) == before


def test_non_quality_transport_remains_compatible(profile):
    from deerflow.sp.subagents import SPSubagentTask

    p, body = profile
    p.quality_controls = False
    task = SPSubagentTask("a", "coder", "review", "test")
    p.prepare_task(task)
    assert body in json.dumps(task.context_refs["audit_calculation_runs"], ensure_ascii=False)


def test_no_canonical_live_index_never_drops_raw_source_body(profile):
    from deerflow.sp.subagents import SPSubagentTask

    p, body = profile
    p.adapters.fact_indexes.clear()
    task = SPSubagentTask("a", "coder", "review", "test")
    p.prepare_task(task)
    assert body in json.dumps(task.context_refs["audit_calculation_runs"], ensure_ascii=False)


def test_full_navigation_in_actual_tool_message_is_not_injected_twice(tmp_path):
    from langchain_core.messages import ToolMessage

    from app.experiments.collusion_audit.fact_context import CurrentCodeFactsMiddleware

    body = "完整双端响应末尾，不可删。"
    nav = {"delivery": "indexed_large_rule_result", "candidate_count": 1, "candidate_previews": [{"response_a": body, "response_b": "另一端正文"}]}
    adapter = SimpleNamespace(fact_indexes={"run": {"rule_id": "M03", "audit_run_id": "run", "findings": [], "result_navigation": nav}}, ocr_receipts=None)
    tool = ToolMessage(content=json.dumps({"run_id": "run", "result_navigation": nav}, ensure_ascii=False), name="audit_m03", tool_call_id="c")
    request = SimpleNamespace(messages=[tool], override=lambda **kwargs: SimpleNamespace(**kwargs))
    result = CurrentCodeFactsMiddleware(adapter).project(request)
    assert result.messages[0] is tool
    assert sum(str(m.content).count(body) for m in result.messages) == 1
    later = SimpleNamespace(messages=[], override=lambda **kwargs: SimpleNamespace(**kwargs))
    full = CurrentCodeFactsMiddleware(adapter).project(later)
    assert body in full.messages[-1].content


def test_partial_navigation_does_not_replace_complete_live_evidence():
    from langchain_core.messages import ToolMessage

    from app.experiments.collusion_audit.fact_context import CurrentCodeFactsMiddleware

    nav = {"delivery": "indexed_large_rule_result", "candidate_previews": [{"response_a": "完整响应尾部"}]}
    adapter = SimpleNamespace(fact_indexes={"run": {"rule_id": "M03", "audit_run_id": "run", "findings": [], "result_navigation": nav}}, ocr_receipts=None)
    tool = ToolMessage(content=json.dumps({"result_navigation": {"delivery": "indexed_large_rule_result"}}), name="audit_m03", tool_call_id="c")
    request = SimpleNamespace(messages=[tool], override=lambda **kwargs: SimpleNamespace(**kwargs))
    result = CurrentCodeFactsMiddleware(adapter).project(request)
    assert "完整响应尾部" in result.messages[-1].content


def test_central_compacts_pair_view_but_scoped_agents_keep_all_source_text():
    from app.experiments.collusion_audit.fact_context import CurrentCodeFactsMiddleware

    body = "全部双端响应正文必须仍供审核代理使用。"
    view = {"total": 146, "source_observation_index_complete": True, "source_observation_index": [{"response_a": body, "response_b": "另一端完整正文"}], "full_collection_path": "/current/candidates/response-pairs.json"}
    nav = {"candidate_count": 146, "candidate_previews": [{"response_a": "代表性完整证据"}], "supplementary_collections": {"paired_response_observations": view}}
    adapter = SimpleNamespace(fact_indexes={"run": {"rule_id": "M03", "audit_run_id": "run", "findings": [], "result_navigation": nav}}, ocr_receipts=None)
    request = SimpleNamespace(messages=[HumanMessage(content="完整规则正文")], override=lambda **kwargs: SimpleNamespace(**kwargs))
    central = CurrentCodeFactsMiddleware(adapter, compact_paired_source_view=True).project(request)
    assert central.messages[0] is request.messages[0]
    assert body not in central.messages[-1].content
    assert "代表性完整证据" in central.messages[-1].content
    data = json.loads(central.messages[-1].content.split("\n", 1)[1])[0]["code_facts"]["result_navigation"]["supplementary_collections"]["paired_response_observations"]
    assert data["total"] == 146 and not data["source_observation_index_complete"]
    assert data["full_collection_path"] == "/current/candidates/response-pairs.json"
    scoped = CurrentCodeFactsMiddleware(adapter).project(request)
    assert body in scoped.messages[-1].content
    assert nav["supplementary_collections"]["paired_response_observations"]["source_observation_index_complete"] is True


def test_only_quality_central_uses_compact_pair_view(scoped_profile):
    from deerflow.sp.subagents import SPSubagentTask

    assert scoped_profile.extensions().central_middlewares == ()
    scoped_profile.quality_controls = True
    extension = scoped_profile.extensions()
    assert extension.central_middlewares[0].compact_paired_source_view is True
    scoped = extension.subagent_middlewares(SPSubagentTask("a", "researcher", "review", "test"))
    from app.experiments.collusion_audit.fact_context import CurrentCodeFactsMiddleware

    assert all(not m.compact_paired_source_view for m in scoped if isinstance(m, CurrentCodeFactsMiddleware))
