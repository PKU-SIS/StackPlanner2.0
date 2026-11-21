"""Small candidate repairs preserve native research, evidence and staging gates."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from test_invalid_tool_call_repair_middleware import Request, bad
from test_outline_evidence_action_middleware import RecordingModel
from test_sp_report_compact_outline import _compact
from test_sp_report_writing import _add_material, _delegate, _outline

from deerflow.agents.middlewares.invalid_tool_call_repair_middleware import InvalidToolCallRepairMiddleware
from deerflow.sp.artifacts import SPArtifactAdapter
from deerflow.sp.report import hooks
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError
from deerflow.sp.report.submit_outline import build_patch_outline_budget_tool, build_submit_outline_tool
from deerflow.sp.report.tree import validate_tree_with_diagnostics
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus
from deerflow.subagents.config import SubagentConfig


def setup_candidate(tmp_path, *, require_research=False):
    ctx, _, tree = _compact(tmp_path)
    _add_material(ctx)
    ledger = hooks.ledger_for_state(ctx.state)
    ledger.payload["sources"].append({"source_id": "S1", "title": "Official evidence"})
    ledger.save()
    tree["nodes"][1]["evidence"] = [{
        "material_id": "m1", "source_ids": ["S999"],
        "locator": {"lines": [1, 2]}, "use": "Support the judgement",
    }]
    tree["nodes"][1]["constraints"]["max_words"] = 500
    metadata = {"report_task": "plan", "report_supplementary_registration_required": require_research}
    action, task = _delegate(ctx, "candidate-plan", "outline", metadata)
    cfg, available, _ = hooks.configure_report_subagent(
        task, state=ctx.state, available_tools=[SimpleNamespace(name="read_file")],
        subagent_config=SubagentConfig(name="sp-outline", description="plan"),
    )
    tools = {tool.name: tool for tool in available}
    return ctx, action, task, tree, cfg, tools


def offer(tools, tree):
    response = json.loads(tools["report_submit_outline"].invoke({"tree": tree}))
    assert response["validation_ok"] is False
    return response, response["candidate_budget_patch"]


def patch_args(hashes):
    return {**hashes, "node_budgets": [{"node_id": "n2", "target_words": 30, "max_words": 50}]}


def test_initial_source_correction_and_small_repair_reach_only_normal_acceptance(tmp_path):
    ctx, action, task, tree, _, tools = setup_candidate(tmp_path)
    ledger = hooks.ledger_for_state(ctx.state)
    original, before = deepcopy(tree), ledger.paths.ledger.read_bytes()
    response, hashes = offer(tools, tree)
    assert response["source_id_corrections"][0]["after"] == ["S1"]
    assert "word budget sums exceed" in response["detail"]
    assert tree == original
    delivery = Path(task.metadata["outline_delivery_path"])
    rejected = json.loads(delivery.read_text())
    assert rejected["nodes"][1]["evidence"][0]["source_ids"] == ["S1"]
    assert json.loads(delivery.with_suffix(".source-ids-original.json").read_text()) == original
    result = json.loads(tools["report_patch_outline_budget"].invoke(patch_args(hashes)))
    assert result["validation_ok"] is True
    expected = deepcopy(rejected)
    expected["nodes"][1]["constraints"].update(target_words=30, max_words=50)
    assert json.loads(delivery.read_text()) == expected
    assert ledger.paths.ledger.read_bytes() == before
    assert ReportLedger.load(ledger.paths).payload["tree"] is None
    assert json.loads(next(delivery.parent.glob("*.budget-base-*.json")).read_text()) == rejected
    entry = ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning")
    accepted = hooks.handle_success(
        action, ctx,
        SPSubagentResult(status=SPSubagentStatus.COMPLETED, result="staged", artifact_type="outline",
                         artifact_content=None, artifact_metadata={"completion_status": "complete"}),
        task, artifact_adapter=SPArtifactAdapter(), delegate_entry=entry,
    )
    assert accepted.error is None
    assert hooks.ledger_for_state(ctx.state).payload["tree"]


def test_independent_budget_error_is_reported_with_source_error_in_one_rejection(tmp_path):
    ctx, _, _, tree, _, _ = setup_candidate(tmp_path)
    with pytest.raises(ReportValidationError) as caught:
        validate_tree_with_diagnostics(tree, hooks.ledger_for_state(ctx.state))
    assert "source_ids do not include S1" in str(caught.value)
    assert "word budget sums exceed" in str(caught.value)


@pytest.mark.parametrize("change", ["candidate", "ledger", "new_invalid", "new_valid"])
def test_stale_candidate_or_ledger_cannot_overwrite_newer_progress(tmp_path, change):
    ctx, _, task, tree, _, tools = setup_candidate(tmp_path)
    _, hashes = offer(tools, tree)
    delivery = Path(task.metadata["outline_delivery_path"])
    if change == "candidate":
        delivery.write_text(delivery.read_text() + " ")
    elif change == "ledger":
        ledger = hooks.ledger_for_state(ctx.state)
        ledger.add_user_input("New user requirement", source="hitl_feedback", source_message_id="new-user")
        ledger.save()
    else:
        replacement = deepcopy(tree)
        replacement["report_title"] = "Newer planning progress"
        if change == "new_valid":
            replacement["nodes"][1]["constraints"]["max_words"] = 50
        tools["report_submit_outline"].invoke({"tree": replacement})
    before = delivery.read_bytes()
    result = json.loads(tools["report_patch_outline_budget"].invoke(patch_args(hashes)))
    assert result["error"] == "stale_candidate_budget_patch"
    assert delivery.read_bytes() == before
    assert hooks.ledger_for_state(ctx.state).payload["tree"] is None


@pytest.mark.parametrize("budgets", [
    [], [{"node_id": "unknown", "target_words": 30, "max_words": 50}],
    [{"node_id": "n1", "target_words": 30, "max_words": 50}],
    [{"node_id": "n2", "target_words": True, "max_words": 50}],
    [{"node_id": "n2", "target_words": 30, "max_words": 20}],
    [{"node_id": "n2", "target_words": 30, "max_words": 50, "evidence": []}],
    [{"node_id": "n2", "target_words": 30, "max_words": 50}] * 2,
])
def test_candidate_patch_rejects_nonbudget_changes_and_invalid_nodes(tmp_path, budgets):
    _, _, task, tree, _, tools = setup_candidate(tmp_path)
    _, hashes = offer(tools, tree)
    delivery = Path(task.metadata["outline_delivery_path"])
    before = delivery.read_bytes()
    result = json.loads(tools["report_patch_outline_budget"].invoke({**hashes, "node_budgets": budgets}))
    assert result["error"] == "invalid_candidate_budget_patch"
    assert delivery.read_bytes() == before


@pytest.mark.parametrize("extra", [
    {"write_requests": []}, {"feedback_dispositions": []}, {"keep_nodes": []},
    {"base_tree_version": 1, "base_tree_sha256": "accepted"},
])
def test_candidate_mode_rejects_dispositions_and_accepted_base_fields(tmp_path, extra):
    _, _, task, tree, _, tools = setup_candidate(tmp_path)
    _, hashes = offer(tools, tree)
    delivery = Path(task.metadata["outline_delivery_path"])
    before = delivery.read_bytes()
    result = json.loads(tools["report_patch_outline_budget"].invoke({**patch_args(hashes), **extra}))
    assert result["error"] == "invalid_candidate_budget_patch"
    assert delivery.read_bytes() == before


@pytest.mark.parametrize("problem", ["unknown_material", "missing_research", "still_over_budget"])
def test_budget_repair_does_not_bypass_evidence_research_or_length_validation(tmp_path, problem):
    ctx, _, _, tree, _, tools = setup_candidate(tmp_path, require_research=problem == "missing_research")
    if problem == "unknown_material":
        tree["nodes"][1]["evidence"][0]["material_id"] = "m999"
    _, hashes = offer(tools, tree)
    args = patch_args(hashes)
    if problem == "still_over_budget":
        args["node_budgets"][0]["max_words"] = 499
    result = json.loads(tools["report_patch_outline_budget"].invoke(args))
    assert result["validation_ok"] is False
    expected = {"unknown_material": "unknown material", "missing_research": "supplementary-research round", "still_over_budget": "word budget sums exceed"}
    assert expected[problem] in result["detail"]
    assert hooks.ledger_for_state(ctx.state).payload["tree"] is None


def test_invalid_candidate_repair_preserves_last_valid_proposal(tmp_path):
    _, _, task, tree, _, tools = setup_candidate(tmp_path)
    valid = deepcopy(tree)
    valid["nodes"][1]["constraints"]["max_words"] = 50
    assert json.loads(tools["report_submit_outline"].invoke({"tree": valid}))["validation_ok"]
    delivery = Path(task.metadata["outline_delivery_path"])
    before = delivery.read_bytes()
    response, hashes = offer(tools, tree)
    assert response["previous_proposal_preserved"] is True
    assert delivery.read_bytes() == before
    args = patch_args(hashes)
    args["node_budgets"][0]["max_words"] = 499
    assert not json.loads(tools["report_patch_outline_budget"].invoke(args))["validation_ok"]
    assert delivery.read_bytes() == before


def test_invalid_accepted_tree_patch_also_preserves_last_valid_proposal(tmp_path):
    ctx, _, task, tree, _, tools = setup_candidate(tmp_path)
    tree["nodes"][1]["constraints"]["max_words"] = 50
    tree["nodes"][1]["evidence"][0]["source_ids"] = ["S1"]
    assert _outline(ctx, tree).error is None
    ledger = hooks.ledger_for_state(ctx.state)
    before_ledger = ledger.paths.ledger.read_bytes()
    base = {"base_tree_version": ledger.payload["tree"]["version"], "base_tree_sha256": ledger.payload["tree"]["sha256"]}
    valid = {**base, "node_budgets": [{"node_id": "n2", "target_words": 25, "max_words": 45}]}
    assert json.loads(tools["report_patch_outline_budget"].invoke(valid))["validation_ok"]
    delivery = Path(task.metadata["outline_delivery_path"])
    before = delivery.read_bytes()
    invalid = {**base, "node_budgets": [{"node_id": "n2", "target_words": 25, "max_words": 500}]}
    response = json.loads(tools["report_patch_outline_budget"].invoke(invalid))
    assert response["error"] == "invalid_budget_patch"
    assert response["previous_proposal_preserved"] is True
    assert delivery.read_bytes() == before
    assert ledger.paths.ledger.read_bytes() == before_ledger


def test_candidate_patch_is_opt_in_and_not_offered_for_valid_trees(tmp_path):
    ctx, _, task, tree, _, _ = setup_candidate(tmp_path)
    paths = hooks.ledger_for_state(ctx.state).paths
    delivery = Path(task.metadata["outline_delivery_path"])
    submit = build_submit_outline_tool(paths=paths, delivery_path=delivery)
    assert "candidate_budget_patch" not in json.loads(submit.invoke({"tree": tree}))
    patch = build_patch_outline_budget_tool(paths=paths, delivery_path=delivery)
    assert json.loads(patch.invoke(patch_args({"candidate_sha256": "x", "candidate_ledger_sha256": "y"})))["error"] == "invalid_candidate_budget_patch"
    tree["nodes"][1]["constraints"]["max_words"] = 50
    enabled = build_submit_outline_tool(paths=paths, delivery_path=delivery, normalize_sources=True, allow_staged_budget_patch=True)
    valid = json.loads(enabled.invoke({"tree": tree}))
    assert valid["validation_ok"] is True and "candidate_budget_patch" not in valid


def test_syntax_repair_can_use_offered_budget_patch_without_opening_reads():
    middleware = InvalidToolCallRepairMiddleware()
    update = middleware.after_model({"messages": [bad()]}, None)
    request = Request(messages=update["messages"], tools=[SimpleNamespace(name=name) for name in
                      ("report_submit_outline", "report_patch_outline_budget", "report_read_material")])
    assert [tool.name for tool in middleware.prepare_request(request).tools] == ["report_submit_outline"]
    corrected = AIMessage(content="", tool_calls=[{"name": "report_submit_outline", "args": {"tree": {}}, "id": "fixed"}])
    middleware.after_model({"messages": [corrected]}, None)
    response = ToolMessage(content=json.dumps({"error": "invalid_outline_tree", "candidate_budget_patch": {"candidate_sha256": "x", "candidate_ledger_sha256": "y"}}), tool_call_id="fixed")
    middleware.wrap_tool_call(SimpleNamespace(tool_call={"name": "report_submit_outline"}), lambda _: response)
    assert [tool.name for tool in middleware.prepare_request(request).tools] == ["report_submit_outline", "report_patch_outline_budget"]
    small = AIMessage(content="", tool_calls=[{"name": "report_patch_outline_budget", "args": {}, "id": "patch"}])
    assert middleware.after_model({"messages": [small]}, None) is None
    success = ToolMessage(content='{"saved":true,"validation_ok":true}', tool_call_id="patch")
    middleware.wrap_tool_call(SimpleNamespace(tool_call={"name": "report_patch_outline_budget"}), lambda _: success)
    assert middleware.prepare_request(request).tools == []
    assert middleware.consume_stop_reason() is None


@pytest.mark.parametrize("bound_source", [False, True])
def test_real_agent_graph_uses_one_small_correction_and_normal_envelope(tmp_path, bound_source):
    ctx, _, _, tree, _, tools = setup_candidate(tmp_path)
    response, hashes = offer(tools, tree)
    model = RecordingModel(responses=[
        AIMessage(content="", tool_calls=[{"name": "report_patch_outline_budget", "args": patch_args(hashes), "id": "patch"}]),
        AIMessage(content=json.dumps({"artifact_type": "outline", "artifact_content": None, "artifact_metadata": {"delivery_mode": "file"}})),
    ])
    history = [HumanMessage(content="Keep the original report contract"),
               AIMessage(content="", tool_calls=[{"name": "report_submit_outline", "args": {"tree": tree}, "id": "submit"}]),
               ToolMessage(content=json.dumps(response), name="report_submit_outline", tool_call_id="submit")]
    middleware = InvalidToolCallRepairMiddleware(bound_source_correction=bound_source)
    graph = create_agent(model=model, tools=[tools["report_submit_outline"], tools["report_patch_outline_budget"]],
                         middleware=[middleware])
    result = graph.invoke({"messages": history})
    assert len(model.seen) == 2  # One correction and the ordinary final envelope.
    executed = [message for message in result["messages"] if isinstance(message, ToolMessage) and message.name == "report_patch_outline_budget"]
    assert len(executed) == 1 and json.loads(executed[0].content)["validation_ok"] is True
    assert middleware.finish_only is True
    assert result["messages"][:3] == history
    assert hooks.ledger_for_state(ctx.state).payload["tree"] is None
