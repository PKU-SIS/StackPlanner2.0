"""Opt-in whole-document estimated Word budgets, without claiming Word execution."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_sp_report_acceptance_state import _host_review, _review_result
from test_sp_report_p3 import _seed
from test_sp_report_writing import _context, _outline, _tree, _writer_action

from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.artifacts import SPArtifactAdapter
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.report import hooks, store
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError
from deerflow.sp.report.length import (
    COUNTER_VERSION,
    budget_summary,
    build_measure_draft_tool,
    candidate_length,
    estimate_words,
    normalize_contract,
    validate_budgets,
)
from deerflow.sp.report.submit_outline import build_patch_outline_budget_tool
from deerflow.subagents.config import SubagentConfig


def _small_contract(**overrides):
    return normalize_contract({"min_words": 5, "target_words": 60, "max_words": 100, "reference_reserve_words": 0, "heading_reserve_words": 0, "safety_margin_words": 0, **overrides})


def _enabled_context(tmp_path):
    ctx = _context(tmp_path)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    ledger.payload["length_contract"] = _small_contract()
    ledger.save()
    return ctx


def _budgeted_tree():
    tree = _tree(write_requests=[{"key": "w1", "node_ids": ["n2"], "feedback_wanted": False}])
    tree["nodes"][1]["constraints"].update(target_words=30, max_words=50)
    return tree


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("中文 English words", 4),
        ("你好，World！", 5),
        ("## 标题\n\n**正文** [link](https://hidden.example/long/path)", 5),
        ("| 名称 | 数值 |\n|---|---|\n| 碳钢 | 12.5 |", 7),
        ("[1] Source https://example.com/a/b?q=1", 3),
        ("```text\n中文 English\n```", 3),
    ],
)
def test_estimator_counts_displayed_mixed_text_not_markdown_or_url_characters(text, expected):
    assert estimate_words(text) == expected


def test_contract_is_explicit_and_preserves_unit():
    assert normalize_contract(None) is None
    contract = normalize_contract({})
    assert (contract["min_words"], contract["target_words"], contract["max_words"]) == (10000, 12000, 15000)
    assert contract["counter_version"] == COUNTER_VERSION
    assert contract["scope"] == "whole_document"
    assert contract["measurement_mode"] == "estimate"


@pytest.mark.parametrize("value", [False, [], {"min_words": True}, {"target_words": 9999}, {"max_words": -1}, {"unit": "characters"}, {"measurement_mode": "word_measured"}, {"safety_margin_words": 12000}])
def test_invalid_or_falsely_measured_contract_is_rejected(value):
    with pytest.raises(ReportValidationError):
        normalize_contract(value)


def test_start_uses_explicit_user_message_contract_and_invalid_input_leaves_no_report(tmp_path):
    state = {"thread_data": {"outputs_path": str(tmp_path)}, "messages": [HumanMessage(content="Write report", id="m1", additional_kwargs={"report_length": {}})]}
    ctx = HandlerContext(state=state, stack=TaskMemoryStack(), thread_id="t1", run_id="r1")
    hooks.start_report(ctx, SPAction.create(ActionType.REPLAN, reason="start", task="start", metadata={"report_action": "start"}))
    assert ReportLedger.load_for_context(hooks.report_context(state)).payload["length_contract"]["max_words"] == 15000
    other = tmp_path / "invalid"
    state = {"thread_data": {"outputs_path": str(other)}, "messages": [HumanMessage(content="write", additional_kwargs={"report_length": {"max_words": False}})]}
    with pytest.raises(ReportValidationError):
        hooks.start_report(HandlerContext(state=state, stack=TaskMemoryStack()), SPAction.create(ActionType.REPLAN, reason="start", task="start"))
    assert not store.pointer_path(other).exists()


def test_default_start_and_packets_have_no_length_contract(tmp_path):
    from deerflow.sp.report.packets import build_outline_packet

    ctx = _context(tmp_path)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert "length_contract" not in ledger.payload
    assert "length" not in build_outline_packet(ledger)
    validate_budgets(_tree(), ledger)


def test_budget_sums_and_missing_node_limits_are_checked(tmp_path):
    ctx = _enabled_context(tmp_path)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    tree = _budgeted_tree()
    validate_budgets(tree, ledger)
    summary = budget_summary(tree, ledger)
    assert summary["node_target_sum"] == 30 and summary["node_max_sum"] == 50
    bad = deepcopy(tree)
    bad["nodes"][1]["constraints"]["max_words"] = 200
    with pytest.raises(ReportValidationError, match="budget"):
        validate_budgets(bad, ledger)
    del bad["nodes"][1]["constraints"]["max_words"]
    with pytest.raises(ReportValidationError, match="max_words"):
        validate_budgets(bad, ledger)


def test_budgeted_writer_receives_contract_and_scoped_measure_tool(tmp_path):
    ctx = _enabled_context(tmp_path)
    assert _outline(ctx, _budgeted_tree()).error is None
    _, task, _ = _writer_action(ctx, "w1")
    assert task.context_refs["report_packet"]["length"]["node"]["max_words"] == 50
    tools = [SimpleNamespace(name=n) for n in ("read_file", "write_file", "web_search")]
    config, available, _ = hooks.configure_report_subagent(task, state=ctx.state, available_tools=tools, subagent_config=SubagentConfig(name="sp-reporter", description="write"))
    assert "report_measure_draft" in config.tools
    assert "report_measure_draft" in [t.name for t in available]
    assert "web_search" not in config.tools
    assert "one" in config.system_prompt.lower()


def test_measurement_is_bound_to_inflight_draft_and_checks_are_bounded(tmp_path):
    ctx = _enabled_context(tmp_path)
    assert _outline(ctx, _budgeted_tree()).error is None
    action, task, path = _writer_action(ctx, "w1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("证" * 60)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    tool = build_measure_draft_tool(paths=ledger.paths, node_id="n2", write_key="w1", draft_id=task.metadata["report_draft_id"], action_id=action.action_id)
    measured = json.loads(tool.invoke({}))
    assert measured["estimated_words"] == 60 and measured["over_node_max"]
    assert measured["word_verified"] is False and measured["measured_word_count"] is None
    path.write_text("证" * 30)
    assert json.loads(tool.invoke({}))["estimated_words"] == 30
    assert json.loads(tool.invoke({}))["error"] == "length_check_limit"
    ledger = ReportLedger.load(ledger.paths)
    ledger.payload["write_requests"][0]["status"] = "completed"
    ledger.save()
    assert json.loads(tool.invoke({}))["error"] == "writer_not_in_flight"


def test_measurement_rejects_symlink_outside_bound_draft(tmp_path):
    ctx = _enabled_context(tmp_path)
    assert _outline(ctx, _budgeted_tree()).error is None
    action, task, path = _writer_action(ctx, "w1")
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = tmp_path / "outside.txt"
    secret.write_text("not draft")
    path.symlink_to(secret)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    tool = build_measure_draft_tool(paths=ledger.paths, node_id="n2", write_key="w1", draft_id=task.metadata["report_draft_id"], action_id=action.action_id)
    result = json.loads(tool.invoke({}))
    assert result["error"] == "invalid_draft_path" and "not draft" not in str(result)


def test_candidate_counts_final_export_sources_and_never_claims_word_measurement(tmp_path):
    from deerflow.sp.report.assembly import ensure_candidate

    ctx, ledger = _seed(tmp_path, body="中文 English [S1].\n", with_sources=True)
    ledger.payload["length_contract"] = _small_contract()
    ledger.save()
    candidate = ensure_candidate(ledger)
    result = candidate_length(ledger, candidate)
    assert result["reference_words"] > 0 and result["estimated_words"] > estimate_words("中文 English")
    assert result["word_verified"] is False and result["measured_word_count"] is None
    assert result["candidate_sha256"] == candidate["sha256"]


def test_host_overrides_ready_review_when_whole_report_is_out_of_range(tmp_path):
    ctx, ledger = _seed(tmp_path, body="证" * 200)
    ledger.payload["length_contract"] = _small_contract()
    ledger.save()
    action, task, entry = _host_review(ctx)
    handled = hooks.handle_success(action, ctx, _review_result(task, status="ready", outcome="deliver"), task, artifact_adapter=SPArtifactAdapter(), delegate_entry=entry)
    assert handled.error is None
    ledger = ReportLedger.load(ledger.paths)
    assert ledger.payload["review"]["outcome"] == "revise" and ledger.payload["final_report"] is None
    assert any("length" in f["message"].lower() for f in ledger.payload["review"]["findings"])
    assert ledger.payload["review"]["length"]["estimated_words"] > 100
    from deerflow.sp.report.acceptance import next_acceptance_action

    next_action = next_acceptance_action(ctx.state)
    assert next_action.tool_calls[0]["args"]["metadata"]["report_task"] == "revise"


def test_budget_patch_stages_full_valid_tree_without_committing_or_changing_evidence(tmp_path):
    ctx = _enabled_context(tmp_path)
    assert _outline(ctx, _budgeted_tree()).error is None
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    before = deepcopy(ledger.payload)
    path = ledger.paths.directory / "outline/patch.json"
    tool = build_patch_outline_budget_tool(paths=ledger.paths, delivery_path=path)
    result = json.loads(tool.invoke({"base_tree_version": ledger.payload["tree"]["version"], "base_tree_sha256": ledger.payload["tree"]["sha256"], "node_budgets": [{"node_id": "n2", "target_words": 25, "max_words": 45}]}))
    assert result["validation_ok"]
    proposed = json.loads(path.read_text())
    base = json.loads(Path(ledger.payload["tree"]["path"]).read_text())
    assert proposed["nodes"][1]["evidence"] == base["nodes"][1]["evidence"]
    assert proposed["nodes"][1]["constraints"]["max_words"] == 45
    assert ReportLedger.load(ledger.paths).payload == before


@pytest.mark.parametrize("node_budgets", [[{"node_id": "n2", "max_words": 40, "target_words": 20, "evidence": []}], [{"node_id": "unknown", "max_words": 40, "target_words": 20}], [{"node_id": "n2", "max_words": True, "target_words": 20}]])
def test_budget_patch_rejects_unknown_nodes_fields_and_invalid_limits(tmp_path, node_budgets):
    ctx = _enabled_context(tmp_path)
    assert _outline(ctx, _budgeted_tree()).error is None
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    tool = build_patch_outline_budget_tool(paths=ledger.paths, delivery_path=ledger.paths.directory / "outline/patch.json")
    result = json.loads(tool.invoke({"base_tree_version": ledger.payload["tree"]["version"], "base_tree_sha256": ledger.payload["tree"]["sha256"], "node_budgets": node_budgets}))
    assert result.get("validation_ok") is not True


def test_budget_patch_rejects_stale_version_and_does_not_overwrite_accepted_tree(tmp_path):
    ctx = _enabled_context(tmp_path)
    assert _outline(ctx, _budgeted_tree()).error is None
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    original = Path(ledger.payload["tree"]["path"]).read_bytes()
    tool = build_patch_outline_budget_tool(paths=ledger.paths, delivery_path=ledger.paths.directory / "outline/patch.json")
    result = json.loads(tool.invoke({"base_tree_version": 999, "base_tree_sha256": ledger.payload["tree"]["sha256"], "node_budgets": [{"node_id": "n2", "target_words": 25, "max_words": 45}]}))
    assert result["error"] == "stale_budget_patch"
    assert Path(ledger.payload["tree"]["path"]).read_bytes() == original


def test_html_display_text_is_not_silently_omitted():
    assert estimate_words("<div>中文 English</div>") == 3
    assert estimate_words("one<br>two") == 2


def test_budget_patch_cannot_change_content_after_staging(tmp_path):
    from deerflow.sp.report.tree import validate_tree_with_diagnostics

    ctx = _enabled_context(tmp_path)
    assert _outline(ctx, _budgeted_tree()).error is None
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    path = ledger.paths.directory / "outline/patch.json"
    tool = build_patch_outline_budget_tool(paths=ledger.paths, delivery_path=path)
    result = json.loads(tool.invoke({"base_tree_version": ledger.payload["tree"]["version"], "base_tree_sha256": ledger.payload["tree"]["sha256"], "node_budgets": [{"node_id": "n2", "target_words": 25, "max_words": 45}]}))
    assert result["validation_ok"]
    proposal = json.loads(path.read_text())
    proposal["nodes"][1]["argument"] = ["Changed content disguised as a budget update"]
    with pytest.raises(ReportValidationError, match="budget patch"):
        validate_tree_with_diagnostics(proposal, ledger)


def test_overlong_node_draft_is_content_not_technical_failure(tmp_path):
    from test_sp_report_writing import _accept_writer, _writer_result

    ctx = _enabled_context(tmp_path)
    assert _outline(ctx, _budgeted_tree()).error is None
    action, task, path = _writer_action(ctx, "w1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("证" * 75)
    result = _accept_writer(ctx, action, task, _writer_result(None, task))
    assert result.error is None
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.payload["write_requests"][0]["status"] == "completed"
    assert ledger.payload["drafts"][0]["length"]["estimated_words"] == 75
    assert ledger.payload["drafts"][0]["length"]["word_verified"] is False


def test_contract_change_invalidates_existing_candidate_binding(tmp_path):
    from deerflow.sp.report.assembly import base_inputs_digest, ensure_candidate

    _, ledger = _seed(tmp_path)
    ledger.payload["length_contract"] = _small_contract()
    ledger.save()
    candidate = ensure_candidate(ledger)
    before = candidate["base_digest"]
    ledger.payload["length_contract"]["max_words"] = 101
    assert base_inputs_digest(ledger) != before


@pytest.mark.parametrize("at_boundary", ["min", "max"])
def test_whole_document_range_boundaries_are_inclusive(tmp_path, at_boundary):
    from deerflow.sp.report.assembly import ensure_candidate

    _, ledger = _seed(tmp_path, body="证" * 20)
    ledger.payload["length_contract"] = _small_contract()
    ledger.save()
    n = candidate_length(ledger, ensure_candidate(ledger))["estimated_words"]
    ledger.payload["length_contract"] = _small_contract(min_words=n if at_boundary == "min" else 1, target_words=n, max_words=n if at_boundary == "max" else n + 10)
    assert candidate_length(ledger, ensure_candidate(ledger))["in_estimated_range"]


def test_full_graph_uses_single_content_revision_then_delivers(monkeypatch):
    from test_sp_report_acceptance_flows import _invoke, _ledger, _NoCentralModel, _RewriteExecutor, _state_without_messages

    state, ledger = _state_without_messages(body="证" * 200, node_status="ready")
    tree = json.loads(Path(ledger.payload["tree"]["path"]).read_text())
    tree["nodes"][1]["constraints"].update(target_words=30, max_words=50)
    store.write_json_atomic(Path(ledger.payload["tree"]["path"]), tree)
    ledger.payload["tree"]["sha256"] = store.sha256_text(store.read_text(Path(ledger.payload["tree"]["path"])))
    ledger.payload["length_contract"] = _small_contract()
    ledger.save()
    executor = _RewriteExecutor(["ready", "ready"], ledger.paths.outputs_path, "证" * 20)
    _invoke(monkeypatch, executor=executor, model=_NoCentralModel(), state=state)
    completed = _ledger(state)
    assert completed.payload["delivered"] is not None
    assert completed.payload["acceptance"]["content_revision_used"]
    assert completed.payload["acceptance"]["review_count"] == 2
    assert completed.payload["final_report"]["length"]["in_estimated_range"]
    assert completed.payload["final_report"]["length"]["measured_word_count"] is None


def test_full_graph_does_not_loop_when_length_stays_wrong(monkeypatch):
    from test_sp_report_acceptance_flows import _invoke, _ledger, _NoCentralModel, _RewriteExecutor, _state_without_messages

    state, ledger = _state_without_messages(body="证" * 200, node_status="ready")
    tree = json.loads(Path(ledger.payload["tree"]["path"]).read_text())
    tree["nodes"][1]["constraints"].update(target_words=30, max_words=50)
    store.write_json_atomic(Path(ledger.payload["tree"]["path"]), tree)
    ledger.payload["tree"]["sha256"] = store.sha256_text(store.read_text(Path(ledger.payload["tree"]["path"])))
    ledger.payload["length_contract"] = _small_contract()
    ledger.save()
    executor = _RewriteExecutor(["ready", "ready"], ledger.paths.outputs_path, "证" * 200)
    _invoke(monkeypatch, executor=executor, model=_NoCentralModel(), state=state)
    completed = _ledger(state)
    assert completed.payload["delivered"] is None
    assert completed.payload["acceptance"]["status"] == "blocked_user"
    assert completed.payload["acceptance"]["review_count"] == 2
    assert len(executor.revise_action_ids) == 1


def test_explicit_user_contract_cannot_be_overridden_by_start_action(tmp_path):
    state = {"thread_data": {"outputs_path": str(tmp_path)}, "messages": [HumanMessage(content="write", additional_kwargs={"report_length": {}})]}
    ctx = HandlerContext(state=state, stack=TaskMemoryStack())
    with pytest.raises(ReportValidationError, match="conflict"):
        hooks.start_report(ctx, SPAction.create(ActionType.REPLAN, reason="start", task="start", metadata={"report_length": {"enabled": False}}))
    assert not store.pointer_path(tmp_path).exists()


def test_budget_patch_goes_through_native_acceptance_and_preserves_old_version(tmp_path):
    ctx = _enabled_context(tmp_path)
    assert _outline(ctx, _budgeted_tree()).error is None
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    old_ref = deepcopy(ledger.payload["tree"])
    old_text = Path(old_ref["path"]).read_bytes()
    path = ledger.paths.directory / "outline/patch.json"
    tool = build_patch_outline_budget_tool(paths=ledger.paths, delivery_path=path)
    staged = json.loads(tool.invoke({"base_tree_version": old_ref["version"], "base_tree_sha256": old_ref["sha256"], "node_budgets": [{"node_id": "n2", "target_words": 25, "max_words": 45}]}))
    assert staged["validation_ok"]
    assert _outline(ctx, json.loads(path.read_text()), action_id="a-budget-patch").error is None
    after = ReportLedger.load(ledger.paths)
    assert after.payload["tree"]["version"] == old_ref["version"] + 1
    committed = json.loads(Path(after.payload["tree"]["path"]).read_text())
    assert "budget_patch_base" not in committed
    assert Path(old_ref["path"]).read_bytes() == old_text


def test_runner_prepares_length_experiment_without_model_calls(tmp_path):
    import subprocess

    root = Path(__file__).resolve().parents[2]
    destination = tmp_path / "prepared"
    result = subprocess.run(
        [sys.executable, str(root / "backend/scripts/evaluate_report_full.py"), "--ids", "11", "--output", str(destination), "--not-before", "2026-11-04T00:00:00+00:00", "--word-length", "10000", "12000", "15000"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    query = json.loads((destination / "11/query.json").read_text())
    assert query["report_length"]["target_words"] == 12000
    assert json.loads((destination / "manifest.json").read_text())["report_length"]["counter_version"] == COUNTER_VERSION
    assert not (destination / "11/metrics.json").exists()
    assert "no models or paid APIs" in result.stdout


def test_underallocated_target_budget_is_rejected_before_writing(tmp_path):
    ctx = _enabled_context(tmp_path)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    ledger.payload["length_contract"] = _small_contract(min_words=70, target_words=80, max_words=100)
    tree = _budgeted_tree()
    tree["nodes"][1]["constraints"].update(target_words=1, max_words=50)
    with pytest.raises(ReportValidationError, match="minimum"):
        validate_budgets(tree, ledger)


@pytest.mark.parametrize("version", [True, 1.0, "1"])
def test_budget_patch_version_is_strict_integer(tmp_path, version):
    ctx = _enabled_context(tmp_path)
    assert _outline(ctx, _budgeted_tree()).error is None
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    tool = build_patch_outline_budget_tool(paths=ledger.paths, delivery_path=ledger.paths.directory / "outline/patch.json")
    with pytest.raises(ValueError):
        tool.invoke({"base_tree_version": version, "base_tree_sha256": ledger.payload["tree"]["sha256"], "node_budgets": [{"node_id": "n2", "target_words": 25, "max_words": 45}]})


def test_reserved_words_are_not_counted_as_written_content_for_minimum(tmp_path):
    ctx = _enabled_context(tmp_path)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    ledger.payload["length_contract"] = _small_contract(min_words=50, target_words=100, max_words=150, reference_reserve_words=30, heading_reserve_words=20)
    tree = _budgeted_tree()
    tree["nodes"][1]["constraints"].update(target_words=10, max_words=50)
    with pytest.raises(ReportValidationError, match="minimum"):
        validate_budgets(tree, ledger)


def test_length_skill_is_loaded_only_for_budgeted_roles(tmp_path):
    ctx = _enabled_context(tmp_path)
    assert _outline(ctx, _budgeted_tree()).error is None
    _, task, _ = _writer_action(ctx, "w1")
    config, _, _ = hooks.configure_report_subagent(task, state=ctx.state, available_tools=[SimpleNamespace(name=n) for n in ("read_file", "write_file")], subagent_config=SubagentConfig(name="sp-reporter", description="write"))
    assert config.skills == ["report-node-writing", "report-word-budget"]
