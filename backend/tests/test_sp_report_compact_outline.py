"""Opt-in first-pass scope limits without per-chapter compression calls."""

import json
import re
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_sp_report_length import _budgeted_tree, _enabled_context, _small_contract
from test_sp_report_lifecycle import _register_gap
from test_sp_report_writing import _outline, _writer_action

from deerflow.sp.report import hooks
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError
from deerflow.sp.report.length import normalize_contract, validate_budgets
from deerflow.sp.subagents import SPSubagentTask
from deerflow.subagents.config import SubagentConfig


def _compact(tmp_path):
    ctx = _enabled_context(tmp_path)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    ledger.payload["length_contract"] = _small_contract(compact_outline=True)
    ledger.save()
    tree = _budgeted_tree()
    tree["nodes"][1]["constraints"]["scope_exclusions"] = ["Optional historical survey and extra cases"]
    return ctx, ledger, tree


@pytest.mark.parametrize("value", [1, "true", None])
def test_compact_flag_is_strict_boolean(value):
    with pytest.raises(ReportValidationError, match="compact_outline"):
        normalize_contract({"compact_outline": value})


@pytest.mark.parametrize("other", ["paragraph_plan_required", "writer_submission_limit"])
def test_compact_mode_cannot_reintroduce_arithmetic_plans_or_local_resubmissions(other):
    with pytest.raises(ReportValidationError, match="compact_outline cannot combine"):
        normalize_contract({"compact_outline": True, other: True})


def test_compact_scope_is_guidance_and_does_not_block_required_detail(tmp_path):
    _, ledger, tree = _compact(tmp_path)
    node = tree["nodes"][1]
    node["argument"] = ["Core judgement", "Another required judgement", "Third required judgement"]
    node["constraints"].pop("scope_exclusions")
    node["constraints"].update(target_words=1, max_words=2)
    for i in range(3, 12):
        extra = deepcopy(node)
        extra["node_id"] = f"n{i}"
        tree["nodes"].append(extra)
    validate_budgets(tree, ledger)


def test_up_to_eight_compact_chapters_keep_original_budget_checks(tmp_path):
    _, ledger, tree = _compact(tmp_path)
    ledger.payload["length_contract"] = _small_contract(target_words=200, max_words=300, compact_outline=True)
    base = tree["nodes"][1]
    base["constraints"].update(target_words=5, max_words=8)
    for i in range(3, 10):
        node = deepcopy(base)
        node["node_id"] = f"n{i}"
        tree["nodes"].append(node)
    validate_budgets(tree, ledger)
    tree["nodes"][1]["constraints"]["max_words"] = 500
    with pytest.raises(ReportValidationError, match="node word budget sums"):
        validate_budgets(tree, ledger)


@pytest.mark.parametrize("field,words", [("argument", 101), ("evidence", 81)])
def test_compact_text_length_does_not_trigger_resubmission(tmp_path, field, words):
    _, ledger, tree = _compact(tmp_path)
    node = tree["nodes"][1]
    if field == "argument":
        node["argument"] = ["证" * words]
    else:
        node["evidence"] = [{"use": "证" * words}]
    validate_budgets(tree, ledger)


def test_scope_limits_do_not_change_legacy_length_reports(tmp_path):
    _, ledger, tree = _compact(tmp_path)
    ledger.payload["length_contract"].pop("compact_outline")
    tree["nodes"][1]["argument"] = ["one", "two", "three", "four"]
    tree["nodes"][1]["constraints"].pop("scope_exclusions")
    validate_budgets(tree, ledger)


def test_writer_has_exact_budget_and_automatic_counting_without_extra_measure_tool(tmp_path):
    ctx, ledger, tree = _compact(tmp_path)
    assert _outline(ctx, tree).error is None
    _, task, _ = _writer_action(ctx, "w1")
    cfg, _, role = hooks.configure_report_subagent(task, state=ctx.state, available_tools=[SimpleNamespace(name=n) for n in ("read_file", "write_file")], subagent_config=SubagentConfig(name="sp-reporter", description="write"))
    assert role == "writer"
    assert "report_measure_draft" not in cfg.tools
    assert cfg.report_writer_length is not None  # local completion observation stays enabled
    assert cfg.max_output_tokens is None  # no provider cutoff that might truncate prose
    assigned = json.loads(re.search(r"<assigned-chapter-length>\s*(.*?)\s*</assigned-chapter-length>", cfg.system_prompt, re.S).group(1))
    assert assigned["target_words"] == 30 and assigned["max_words"] == 50
    assert assigned["scope_exclusions"] == tree["nodes"][1]["constraints"]["scope_exclusions"]
    assert not ledger.payload["length_contract"].get("writer_submission_limit")


def test_compact_research_can_cover_more_than_three_required_gaps(tmp_path):
    ctx, ledger, _ = _compact(tmp_path)
    for i in range(3):
        assert "registered" in _register_gap(ctx, f"g{i}", question=f"Core evidence {i}")
    result = _register_gap(ctx, "g4", question="Optional fourth evidence question")
    assert "registered" in result
    assert len(ReportLedger.load(ledger.paths).gaps()) == 4
    assert "registered" in _register_gap(ctx, "g2", question="Core evidence 2")  # idempotent replay at the cap
    assert len(ReportLedger.load(ledger.paths).gaps()) == 4


def test_compact_gap_batch_uses_native_registration_without_extra_cap(tmp_path):
    from deerflow.sp.report.tools import build_register_research_request_tool

    _, ledger, _ = _compact(tmp_path)
    question = {
        "mode": "new",
        "question": "Essential evidence",
        "constraints": {},
        "why_insufficient": "Not in saved material",
        "stop_condition": "Required fact supported",
        "requirement_refs": [{"input_id": "u1", "quote": "示例公司"}],
        "node_ids": [],
        "materials_checked": [],
    }
    tool = build_register_research_request_tool(paths=ledger.paths, action_id="a")
    result = json.loads(tool.func(questions=[deepcopy(question) for _ in range(4)], tool_call_id="four"))
    assert "registered" in result
    assert len(ReportLedger.load(ledger.paths).gaps()) == 4


@pytest.mark.parametrize("supplement", [False, True])
def test_compact_research_keeps_native_tools_and_stage_budgets(tmp_path, supplement):
    from deerflow.sp.report.packets import build_pre_research_packet, build_researcher_packet

    ctx, ledger, _ = _compact(tmp_path)
    if supplement:
        _register_gap(ctx, "gap", question="Essential support")
        ledger = ReportLedger.load(ledger.paths)

    def packet():
        return build_researcher_packet(ledger, ["g1"], action_id="research") if supplement else build_pre_research_packet(ledger, action_id="research")

    task = SPSubagentTask(
        action_id="research",
        subagent_type="researcher",
        task="research",
        description="research",
        metadata={"report_role": "researcher", "report_id": ledger.paths.report_id, **({"report_gap_ids": ["g1"]} if supplement else {"report_pre_research": True})},
        context_refs={"report_packet": packet()},
    )
    assert task.context_refs["report_packet"]["compact_outline"] is True
    cfg, tools, role = hooks.configure_report_subagent(
        task, state=ctx.state, available_tools=[SimpleNamespace(name=n) for n in ("read_file", "web_search", "web_fetch")], subagent_config=SubagentConfig(name="sp-researcher", description="research")
    )
    assert role == "researcher" and cfg.tool_call_limit == 50 and cfg.finalize_after_tool_calls == 40
    assert "web_search" in cfg.tools and "report_fetch_material" in cfg.tools
    assert cfg.system_prompt.endswith("</compact-report-research>")
    ledger.payload.pop("length_contract")
    ledger.save()
    task.context_refs["report_packet"] = packet()
    assert "compact_outline" not in task.context_refs["report_packet"]
    ordinary, _, _ = hooks.configure_report_subagent(
        task, state=ctx.state, available_tools=[SimpleNamespace(name=n) for n in ("read_file", "web_search", "web_fetch")], subagent_config=SubagentConfig(name="sp-researcher", description="research")
    )
    assert not ordinary.system_prompt.endswith("</compact-report-research>")
