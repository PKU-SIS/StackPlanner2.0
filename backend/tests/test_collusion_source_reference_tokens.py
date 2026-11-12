"""Observed facts may permit IDs; model queries, drafts and errors never do."""

import asyncio
import json

import pytest
from langchain_core.messages import HumanMessage

from app.experiments.collusion_audit.profile import CollusionProfile
from app.experiments.tender_audit.observability import AuditTrace
from deerflow.sp.actions.handlers.context import _visible_user_inputs
from deerflow.sp.subagents import SPSubagentTask


def make_profile(tmp_path, **kwargs):
    project = tmp_path / "project"
    project.mkdir()
    (project / "manifest.json").write_text(json.dumps({"documents": [], "project_id": "synthetic"}))
    return CollusionProfile({"合并规则ID": "M07", "规则名称": "synthetic rule", "通用规则内容": "generic " * 600}, project, tmp_path / "outputs", tmp_path / "cache", AuditTrace(tmp_path / "trace"), rule_level_review=True, **kwargs)


def test_observed_raw_facts_stamped_into_typed_report_task_only(tmp_path):
    p = make_profile(tmp_path)
    asyncio.run(p.adapters.recorded("project_search", {"query": "FAKE-999"}, lambda: {"query": "FAKE-999", "items": [{"excerpt": "原文产品 CV-290 CLV-290SL"}]}))
    task = SPSubagentTask("a", "reporter", "report", "report")
    p.prepare_task(task)
    assert {"CV-290", "CLV-290SL"} <= set(task.source_reference_tokens)
    assert "FAKE-999" not in task.source_reference_tokens
    assert "source_reference_tokens" not in task.protocol_payload()
    assert not set(task.source_reference_tokens) & set(task.context_refs.get("report_requirements", {}).get("required_literal_tokens", []))


@pytest.mark.parametrize(
    "tool,args,result,expected",
    [
        ("audit_m07", {}, {"status": "partial", "run_id": "M07_20261107T000000_0123456789", "complete_code_result": {"collections": {"records": {"groups": [{"common": {}, "rows": [{"model": "CV-290"}]}]}}}}, {"CV-290"}),
        ("audit_read_result", {}, {"items": [{"quote": "CV-290"}], "collection": "evidence"}, {"CV-290"}),
        ("ragflow_retrieve", {"question": "FAKE-999"}, {"document_id": "d001", "chunks": [{"content": "CV-290", "query": "FAKE-999"}]}, {"CV-290"}),
        ("knowledge_read", {}, {"items": [{"content": "CV-290"}]}, {"CV-290"}),
        ("project_read", {}, {"pages": [{"text": "CV-290"}]}, {"CV-290"}),
        ("project_search", {"query": "FAKE-999"}, {"query": "FAKE-999", "items": []}, set()),
        ("project_search", {}, {"status": "error", "items": [{"excerpt": "FAKE-999"}]}, set()),
        ("read_file", {"file_path": "/mnt/user-data/outputs/draft.md"}, {"file_path": "/mnt/user-data/outputs/draft.md", "text": "FAKE-999"}, set()),
        ("read_file", {"file_path": "/knowledge/d001/parsed.md"}, {"file_path": "/knowledge/d001/parsed.md", "source": "persistent_MinerU_snapshot", "text": "CV-290"}, {"CV-290"}),
        ("write_file", {}, {"created": "/mnt/user-data/outputs/FAKE-999.md"}, set()),
    ],
)
def test_registry_accepts_only_fact_fields(tmp_path, tool, args, result, expected):
    from app.experiments.collusion_audit.identifier_provenance import source_reference_tokens

    actual = set(source_reference_tokens(tool, result, args))
    assert expected <= actual
    assert "FAKE-999" not in actual


def revision_task(p, *, run_id="run", latest=None, first=None):
    task = SPSubagentTask("a", "reporter", "remove BAD-1", "revision", run_id=run_id)
    task.context_refs = {
        "latest_user_input": latest if latest is not None else _visible_user_inputs({"messages": [HumanMessage(content=p.user_input())]})[0],
        "report_requirements": {"required_literal_tokens": ["POLICY-V3", "BAD-1"]},
        "artifact_refs": {
            "report": {
                "type": "report",
                "run_id": "run",
                "metadata": {
                    "target_agent": "reporter",
                    "quality_checks": {
                        "required_literal_tokens": {"required": ["POLICY-V3"]},
                        "literal_token_provenance": {"allowed_tokens": ["POLICY-V3", "CV-290"], "source_tokens": ["CV-290"], "introduced_tokens": ["BAD-1"], "passed": False},
                    },
                },
            }
        },
    }
    if first is not None:
        task.context_refs["thread_first_user_input"] = first
    return task


def test_native_clipped_original_context_does_not_turn_delete_instruction_into_requirement(tmp_path):
    p = make_profile(tmp_path, request_run_id="run")
    task = revision_task(p)
    assert len(task.context_refs["latest_user_input"]) == 2400
    assert task.context_refs["latest_user_input"] != p.user_input()
    p.prepare_task(task)
    assert task.context_refs["report_requirements"]["required_literal_tokens"] == ["POLICY-V3"]


@pytest.mark.parametrize("kwargs", [{"run_id": "other"}, {"latest": "new actual human instruction BAD-1"}, {"first": "older actual human instruction"}])
def test_clipped_context_projection_never_overrides_wrong_run_or_new_human(tmp_path, kwargs):
    p = make_profile(tmp_path, request_run_id="run")
    task = revision_task(p, **kwargs)
    p.prepare_task(task)
    assert "BAD-1" in task.context_refs["report_requirements"]["required_literal_tokens"]


def test_unbound_profile_keeps_strict_origin_guard(tmp_path):
    p = make_profile(tmp_path)
    task = revision_task(p)
    p.prepare_task(task)
    assert "BAD-1" in task.context_refs["report_requirements"]["required_literal_tokens"]
