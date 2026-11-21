"""An explicitly required fixed round cannot be skipped by staging or delivery."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from test_outline_evidence_action_middleware import packet
from test_outline_followup_actions import original_request, read_history
from test_sp_report_outline_recovery import _context, _outline_delegate, _tree

from deerflow.agents.middlewares.outline_evidence_action_middleware import (
    REQUIRED_REGISTER_INSTRUCTION,
    OutlineEvidenceActionMiddleware,
)
from deerflow.sp.artifacts import SPArtifactAdapter
from deerflow.sp.report import hooks
from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.report.submit_outline import build_submit_outline_tool, validate_required_research_registration
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus


def register(ledger):
    gap = ledger.register_gap(mode="new", question="核对简报所需的来源证据", constraints={},
        why_insufficient="尚缺该判断的原文证据", stop_condition="核对相关原文并报告结果",
        requirement_refs=[{"input_id": "u1", "quote": "简报"}], node_ids=[], materials_checked=[])
    ledger.save()
    return gap["gap_id"]


def required_middleware():
    return OutlineEvidenceActionMiddleware(packet=packet() | {"supplementary_registration_required": True}, followup_actions=True)


def test_required_next_action_preserves_full_context_and_restores_submit_only_after_registration():
    request = original_request()
    request = request.override(messages=read_history(request))
    middleware = required_middleware()
    guided = middleware.prepare_request(request)
    assert guided.messages[:-1] == request.messages
    assert guided.messages[-1].content == REQUIRED_REGISTER_INSTRUCTION
    assert guided.model_settings is request.model_settings
    assert {tool["name"] for tool in guided.tools} == {"report_read_material", "report_register_research_request"}
    response = AIMessage(content="", tool_calls=[{"name": "report_register_research_request", "args": {}, "id": "gap"}])
    result = ToolMessage(content='{"registered":[{"gap_id":"g1"}]}', name="report_register_research_request", tool_call_id="gap")
    following = request.override(messages=[*request.messages, response, result])
    assert middleware.prepare_request(following).tools is request.tools


def test_required_action_does_not_restore_tools_removed_by_native_guard():
    request = original_request()
    request = request.override(messages=read_history(request), tools=[{"name": "report_register_research_request"}])
    guided = required_middleware().prepare_request(request)
    assert guided.tools == request.tools


@pytest.mark.parametrize("registered,attached", [(False, False), (True, False), (True, True)])
def test_staging_requires_a_real_registered_and_attached_open_gap(tmp_path, registered, attached):
    ctx = _context(tmp_path)
    ledger = hooks.ledger_for_state(ctx.state)
    tree = _tree()
    tree["write_requests"] = []
    if registered:
        gap_id = register(ledger)
        if attached:
            tree["nodes"][0]["gap_ids"] = [gap_id]
            tree["nodes"][0]["status"] = "needs_evidence"
    tool = build_submit_outline_tool(paths=ledger.paths, delivery_path=ledger.paths.directory/"outline/candidate.json",
        require_research_registration=True)
    result = json.loads(tool.invoke({"tree": tree}))
    assert result["validation_ok"] is (registered and attached)
    assert not ReportLedger.load(ledger.paths).payload.get("tree")


@pytest.mark.parametrize("file_delivery", [True, False])
def test_normal_acceptance_rejects_skipped_research_even_when_staging_is_bypassed(tmp_path, file_delivery):
    ctx = _context(tmp_path)
    action, task = _outline_delegate(ctx)
    task = replace(task, metadata={**task.metadata, "report_supplementary_registration_required": True})
    tree = _tree()
    if file_delivery:
        path = Path(task.metadata["outline_delivery_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(tree))
    result = SPSubagentResult(status=SPSubagentStatus.COMPLETED, result="candidate", artifact_type="outline",
        artifact_content=None if file_delivery else json.dumps(tree),
        artifact_metadata={"completion_status": "complete"})
    entry = ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning")
    accepted = hooks.handle_success(action, ctx, result, task, artifact_adapter=SPArtifactAdapter(), delegate_entry=entry)
    assert accepted.error and "supplementary-research round" in accepted.error
    assert not hooks.ledger_for_state(ctx.state).payload.get("tree")
    assert not hooks.ledger_for_state(ctx.state).payload.get("write_requests")


def test_required_initial_tree_cannot_close_research_before_it_runs(tmp_path):
    ctx = _context(tmp_path)
    ledger = hooks.ledger_for_state(ctx.state)
    gap_id = register(ledger)
    tree = _tree()
    tree["nodes"][0]["gap_ids"] = [gap_id]
    tree["gap_outcomes"] = [{"gap_id": gap_id, "outcome": "not_needed", "optional": True}]
    with pytest.raises(ValueError, match="has not returned"):
        validate_required_research_registration(tree, ledger, required=True)


def test_native_acceptance_keeps_registered_research_open_for_the_original_next_stage(tmp_path):
    ctx = _context(tmp_path)
    action, task = _outline_delegate(ctx)
    task = replace(task, metadata={**task.metadata, "report_supplementary_registration_required": True})
    ledger = hooks.ledger_for_state(ctx.state)
    gap_id = register(ledger)
    tree = _tree()
    tree["nodes"][0].update(gap_ids=[gap_id], status="needs_evidence")
    tree["write_requests"] = []
    result = SPSubagentResult(status=SPSubagentStatus.COMPLETED, result="candidate", artifact_type="outline",
        artifact_content=json.dumps(tree), artifact_metadata={"completion_status": "complete"})
    entry = ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning")
    accepted = hooks.handle_success(action, ctx, result, task, artifact_adapter=SPArtifactAdapter(), delegate_entry=entry)
    ledger = hooks.ledger_for_state(ctx.state)
    assert not accepted.error
    assert ledger.payload.get("tree")
    assert ledger.gap(gap_id)["status"] == "open"
    assert ledger.gap(gap_id)["node_ids"] == ["n1"]


def test_unmarked_supplied_only_scope_and_existing_tree_keep_native_behavior(tmp_path):
    ledger = hooks.ledger_for_state(_context(tmp_path).state)
    validate_required_research_registration(_tree(), ledger, required=False)
    ledger.payload["tree"] = {"version": 1}
    validate_required_research_registration(_tree(), ledger, required=True)


def test_required_round_survives_retry_metadata_omission(tmp_path):
    from deerflow.sp.actions.schema import ActionType, SPAction

    ctx = _context(tmp_path)
    action = SPAction.create(ActionType.DELEGATE, action_id="required-plan", target_agent="outline",
        reason="test", task="plan", metadata={"report_task": "plan", "report_supplementary_registration_required": True})
    metadata, refs = {}, {}
    hooks.prepare_delegation(action, ctx, metadata, refs)
    assert metadata["report_supplementary_registration_required"] is True
    ledger = hooks.ledger_for_state(ctx.state)
    assert ledger.payload["supplementary_registration_required"] is True
    _, retry_task = _outline_delegate(ctx)
    assert retry_task.metadata["report_supplementary_registration_required"] is True
    with pytest.raises(ValueError, match="supplementary-research round"):
        validate_required_research_registration(_tree(), ledger, required=False)
