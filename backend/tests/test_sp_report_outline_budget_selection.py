"""Separate the final length contract from Outline's internal body allocation."""

import json
import re
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_sp_report_length import _budgeted_tree, _enabled_context
from test_sp_report_writing import _outline, _writer_action

from deerflow.sp.report import hooks, store
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError
from deerflow.sp.report.length import budget_summary, candidate_length, normalize_contract, validate_budgets
from deerflow.sp.report.packets import build_outline_packet
from deerflow.subagents.config import SubagentConfig


def _contract(target=30000, **overrides):
    return normalize_contract(
        {"min_words": target * 9 // 10, "target_words": target, "max_words": target * 11 // 10, "compact_outline": True, "writer_task_cards": True, "length_auto_revision": False, "outline_budget_selection": True, **overrides}
    )


@pytest.mark.parametrize(("target", "expected"), [(10000, (3000, 3500, 4000)), (20000, (8000, 9500, 11500)), (30000, (13000, 15000, 16500))])
def test_recommendations_are_body_allocations_not_final_length_minus_reserves(target, expected):
    ledger = SimpleNamespace(payload={"length_contract": _contract(target)})
    summary = budget_summary({}, ledger)
    rec = summary["body_budget_recommendation"]
    assert (rec["min_words"], rec["default_words"], rec["max_words"]) == expected
    assert summary["body_target_words"] == expected[1]
    assert summary["selected_body_target_words"] is None
    assert summary["contract"]["target_words"] == target


@pytest.mark.parametrize("chosen", [13000, 14200, 15000, 16300, 16500])
def test_existing_aggregate_gate_accepts_outline_choices_on_both_sides_of_default(chosen):
    ledger = SimpleNamespace(payload={"length_contract": _contract()})
    tree = {"nodes": [{"node_id": "n1", "kind": "writable", "constraints": {"target_words": chosen, "max_words": chosen}}]}
    before = deepcopy(tree)
    validate_budgets(tree, ledger)
    summary = budget_summary(tree, ledger)
    assert summary["selected_body_target_words"] == chosen
    assert summary["body_target_words"] == chosen
    assert tree == before  # no host allocation or post-generation adjustment


@pytest.mark.parametrize(("target", "maximum"), [(12999, 15000), (16501, 16501), (15000, 16501)])
def test_native_budget_gate_retains_range_and_node_max_checks(target, maximum):
    ledger = SimpleNamespace(payload={"length_contract": _contract()})
    tree = {"nodes": [{"node_id": "n1", "kind": "writable", "constraints": {"target_words": target, "max_words": maximum}}]}
    with pytest.raises(ReportValidationError, match="budget"):
        validate_budgets(tree, ledger)


def test_structure_is_a_reference_without_count_quota_or_formula():
    ledger = SimpleNamespace(payload={"length_contract": _contract()})
    for count, arguments in [(3, 1), (10, 8)]:
        tree = {"nodes": [{"node_id": str(i), "kind": "writable", "argument": ["Required answer"] * arguments, "constraints": {"target_words": 15000 // count, "max_words": 15000 // count}} for i in range(count)]}
        validate_budgets(tree, ledger)
        assert budget_summary(tree, ledger)["selected_body_target_words"] == 15000


@pytest.mark.parametrize("changes", [{"outline_budget_selection": 1}, {"outline_budget_selection": None}, {"compact_outline": False, "writer_task_cards": False}, {"target_words": 25000}, {"target_words": []}, {"writer_target_percent": 60}])
def test_invalid_or_conflicting_opt_in_is_rejected(changes):
    with pytest.raises(ReportValidationError):
        _contract(**changes)


def test_legacy_contract_and_budget_reserves_are_unchanged():
    contract = normalize_contract({"min_words": 15000, "target_words": 19000, "max_words": 25000})
    ledger = SimpleNamespace(payload={"length_contract": contract})
    summary = budget_summary({}, ledger)
    assert summary["body_target_words"] == 17200
    assert "body_budget_recommendation" not in summary
    assert "selected_body_target_words" not in summary


def test_native_outline_acceptance_passes_selected_budget_to_writer_without_extra_tool(tmp_path):
    ctx = _enabled_context(tmp_path)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    ledger.payload["length_contract"] = _contract()
    ledger.save()
    initial = build_outline_packet(ledger)["length"]
    assert initial["body_target_words"] == 15000
    tree = _budgeted_tree()
    tree["nodes"][1]["constraints"].update(target_words=16000, max_words=16500)
    result = _outline(ctx, tree)
    assert result.error is None
    _, task, _ = _writer_action(ctx, "w1")
    packet = task.context_refs["report_packet"]
    assert packet["length"]["selected_body_target_words"] == 16000
    assert packet["length"]["node"] == {"target_words": 16000, "max_words": 16500}
    config, _, _ = hooks.configure_report_subagent(task, state=ctx.state, available_tools=[SimpleNamespace(name=n) for n in ("read_file", "write_file")], subagent_config=SubagentConfig(name="sp-reporter", description="write"))
    assigned = json.loads(re.search(r"<assigned-chapter-length>\s*(.*?)\s*</assigned-chapter-length>", config.system_prompt, re.S).group(1))
    assert assigned["target_words"] == 16000
    assert "report_measure_draft" not in config.tools
    assert config.max_output_tokens is None


def test_adaptive_budget_does_not_bypass_native_evidence_acceptance(tmp_path):
    ctx = _enabled_context(tmp_path)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    ledger.payload["length_contract"] = _contract()
    ledger.save()
    tree = _budgeted_tree()
    tree["nodes"][1]["constraints"].update(target_words=15000, max_words=16500)
    tree["nodes"][1]["evidence"] = [{"material_id": "nonexistent-material", "source_ids": [], "locator": {"lines": [1, 2]}, "use": "Required support"}]
    result = _outline(ctx, tree)
    assert result.error is not None


@pytest.mark.parametrize(("words", "within"), [(16000, False), (30000, True), (34000, False)])
def test_final_measurement_uses_30k_contract_not_internal_generation_range(tmp_path, words, within):
    path = tmp_path / "candidate.md"
    text = "证" * words
    path.write_text(text)
    candidate = {"candidate_id": "c1", "path": str(path), "sha256": store.sha256_text(text), "sections": [], "source_table": []}
    measurement = candidate_length(SimpleNamespace(payload={"length_contract": _contract()}), candidate)
    assert measurement["in_estimated_range"] is within
    assert measurement["target_words"] == 30000
