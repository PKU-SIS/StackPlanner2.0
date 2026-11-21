"""A premature consolidation exit can submit in the same guarded native loop."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from test_invalid_tool_call_repair_middleware import Request, bad
from test_outline_candidate_budget_repair import setup_candidate
from test_outline_evidence_action_middleware import RecordingModel
from test_sp_report_compact_outline import _compact
from test_sp_report_writing import _delegate

from deerflow.agents.middlewares.invalid_tool_call_repair_middleware import InvalidToolCallRepairMiddleware
from deerflow.config.token_budget_config import TokenBudgetConfig
from deerflow.sp.artifacts import SPArtifactAdapter
from deerflow.sp.report import hooks
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus
from deerflow.subagents.config import SubagentConfig


def premature(**kwargs):
    return AIMessage(content='{"proceed":true,"notes":"Consolidation is complete"}',
                     response_metadata={"finish_reason": "stop"}, **kwargs)


def middleware(**kwargs):
    return InvalidToolCallRepairMiddleware(require_outline_submission=True, bound_source_correction=True, **kwargs)


def test_missing_submission_reuses_history_and_only_offers_the_existing_submit_tool():
    repair = middleware()
    original = [SystemMessage(content="FULL ORIGINAL CONTRACT"), HumanMessage(content="original task"), premature()]
    update = repair.after_model({"messages": original}, None)
    assert update["jump_to"] == "model"
    assert "MISSING OUTLINE SUBMISSION" in update["messages"][0].content
    settings = {"max_tokens": 32768, "reasoning_effort": "low"}
    request = Request(messages=[*original, *update["messages"]], model_settings=settings,
                      tools=[SimpleNamespace(name=name) for name in
                             ("report_submit_outline", "report_read_material", "report_register_research_request")])
    prepared = repair.prepare_request(request)
    assert [tool.name for tool in prepared.tools] == ["report_submit_outline"]
    assert prepared.model_settings is settings
    assert prepared.messages[1:] == request.messages[1:]
    assert original[0].content == "FULL ORIGINAL CONTRACT"
    assert original[-1].content == premature().content
    assert repair.events[-1]["reason"] == "missing_outline_submission"


def test_one_shared_correction_budget_does_not_loop_on_another_empty_completion():
    repair = middleware()
    update = repair.after_model({"messages": [premature()]}, None)
    repair.prepare_request(Request(messages=update["messages"], tools=[SimpleNamespace(name="report_submit_outline")]))
    assert repair.after_model({"messages": [premature()]}, None) is None
    assert repair.consume_stop_reason() == "outline_delivery_repair_failed"
    assert repair.after_model({"messages": [premature()]}, None) is None
    assert sum(event["status"] == "correction_requested" for event in repair.events) == 1


def test_normal_successful_submission_does_not_add_a_completion_turn():
    repair = middleware()
    result = ToolMessage(content='{"saved":true,"validation_ok":true,"tree_path":"/mnt/real-tree.json"}', tool_call_id="submit")
    repair.wrap_tool_call(SimpleNamespace(tool_call={"name": "report_submit_outline"}), lambda _: result)
    assert repair.validated_outline_submission is True
    assert repair.after_model({"messages": [premature()]}, None) is None
    assert not any(event["status"] == "correction_requested" for event in repair.events)


def test_model_claims_and_invalid_saved_trees_are_not_a_validation_receipt():
    repair = middleware()
    invalid = ToolMessage(content='{"saved":true,"validation_ok":false,"error":"invalid_outline_tree","detail":"bad budget"}', tool_call_id="submit")
    repair.wrap_tool_call(SimpleNamespace(tool_call={"name": "report_submit_outline"}), lambda _: invalid)
    assert not repair.validated_outline_submission
    claim = AIMessage(content='{"status":"completed","artifact_metadata":{"created_paths":["/mnt/fake.json"]}}')
    assert repair.after_model({"messages": [claim]}, None)["jump_to"] == "model"


@pytest.mark.parametrize("finish", ["length", "max_tokens"])
def test_output_cap_does_not_grant_a_delivery_correction(finish):
    message = premature().model_copy(update={"response_metadata": {"finish_reason": finish}})
    repair = middleware()
    assert repair.after_model({"messages": [message]}, None) is None
    assert repair.events[-1]["status"] == "skipped_output_limit"


@pytest.mark.parametrize("status", ["failed", "input_required", "cancelled", "blocked"])
def test_explicit_failure_or_user_input_request_is_not_overridden(status):
    message = AIMessage(content=json.dumps({"status": status, "result": "needs user input"}))
    assert middleware().after_model({"messages": [message]}, None) is None


@pytest.mark.parametrize("content", ["FORCED STOP: loop cap", "TOKEN BUDGET EXCEEDED", '{"stop_reason":"token_capped"}'])
def test_existing_guard_stop_is_not_restarted(content):
    assert middleware().after_model({"messages": [AIMessage(content=content)]}, None) is None


def test_shared_token_tool_and_cancellation_limits_still_prevent_correction():
    last = premature(usage_metadata={"input_tokens": 750, "output_tokens": 100, "total_tokens": 850})
    repair = middleware(budget=TokenBudgetConfig(enabled=True, max_tokens=1000), output_limit=128)
    assert repair.after_model({"messages": [last]}, None) is None
    assert repair.events[-1]["status"] == "skipped_budget"
    assert middleware(tool_limit=1).after_model({"messages": [ToolMessage(content="evidence", tool_call_id="read"), last]}, None) is None
    assert middleware(aborted=lambda: True).after_model({"messages": [premature()]}, None) is None


def test_syntax_repair_and_missing_delivery_cannot_each_grant_a_fresh_retry():
    repair = middleware()
    update = repair.after_model({"messages": [bad()]}, None)
    repair.prepare_request(Request(messages=update["messages"], tools=[SimpleNamespace(name="report_submit_outline")]))
    assert repair.after_model({"messages": [premature()]}, None) is None
    assert repair.consume_stop_reason() == "tool_argument_repair_failed"
    assert sum(event["status"] == "correction_requested" for event in repair.events) == 1


def test_unmarked_agents_keep_the_original_natural_exit():
    assert InvalidToolCallRepairMiddleware().after_model({"messages": [premature()]}, None) is None
    assert SubagentConfig(name="ordinary", description="ordinary").require_outline_submission is False


@pytest.mark.parametrize("report_task,structured,expected", [("plan", False, False), ("revise", True, True), ("integrate_review", False, False)])
def test_only_structured_outline_delivery_assignments_enable_the_guard(tmp_path, report_task, structured, expected):
    ctx, _, _ = _compact(tmp_path)
    _, task = _delegate(ctx, "delivery-scope", "outline", {"report_task": "plan"})
    task = replace(task, metadata={**task.metadata, "report_task": report_task, "report_outline_structured": structured})
    config, _, _ = hooks.configure_report_subagent(
        task, state=ctx.state, available_tools=[SimpleNamespace(name="read_file")],
        subagent_config=SubagentConfig(name="sp-outline", description="outline"),
    )
    assert config.require_outline_submission is expected


@pytest.mark.parametrize("early_exit", [True, False])
def test_real_graph_stages_a_valid_tree_and_uses_normal_host_acceptance(tmp_path, early_exit):
    ctx, action, task, tree, _, tools = setup_candidate(tmp_path)
    tree["nodes"][1]["constraints"]["max_words"] = 50
    tree["nodes"][1]["evidence"][0]["source_ids"] = ["S1"]
    submit = AIMessage(content="", tool_calls=[{"name": "report_submit_outline", "args": {"tree": tree}, "id": "submit"}])
    finish = AIMessage(content=json.dumps({"status": "completed", "artifact_type": "outline", "artifact_content": None,
                                         "artifact_metadata": {"completion_status": "complete"}}))
    model = RecordingModel(responses=([premature()] if early_exit else []) + [submit, finish])
    repair = middleware()
    graph = create_agent(model=model, tools=[tools["report_submit_outline"], tools["report_patch_outline_budget"]], middleware=[repair])
    history = [HumanMessage(content="Original full contract, saved tree and returned research remain available")]
    result = graph.invoke({"messages": history})
    assert len(model.seen) == (3 if early_exit else 2)
    assert result["messages"][0] == history[0]
    executed = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(executed) == 1 and json.loads(executed[0].content)["validation_ok"] is True
    assert hooks.ledger_for_state(ctx.state).payload["tree"] is None  # staging cannot commit
    entry = ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning")
    accepted = hooks.handle_success(action, ctx, SPSubagentResult(status=SPSubagentStatus.COMPLETED, result="staged",
        artifact_type="outline", artifact_content=None, artifact_metadata={"completion_status": "complete"}),
        task, artifact_adapter=SPArtifactAdapter(), delegate_entry=entry)
    assert accepted.error is None
    assert hooks.ledger_for_state(ctx.state).payload["tree"]


@pytest.mark.asyncio
async def test_async_guard_uses_the_same_bounded_correction():
    repair = middleware()
    update = await repair.aafter_model({"messages": [premature()]}, None)
    assert update["jump_to"] == "model"
    request = Request(messages=update["messages"], tools=[SimpleNamespace(name="report_submit_outline")])
    seen = []

    async def handler(prepared):
        seen.append(prepared)
        return premature()

    await repair.awrap_model_call(request, handler)
    assert [tool.name for tool in seen[0].tools] == ["report_submit_outline"]
    assert await repair.aafter_model({"messages": [premature()]}, None) is None
    assert repair.consume_stop_reason() == "outline_delivery_repair_failed"
