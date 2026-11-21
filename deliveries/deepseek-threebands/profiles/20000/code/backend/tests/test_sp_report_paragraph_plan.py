"""Opt-in structural validation for Outline-authored paragraph budgets."""

import json
import re
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_sp_report_length import _budgeted_tree, _enabled_context, _small_contract
from test_sp_report_writing import _outline, _writer_action

from deerflow.sp.report import hooks
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError
from deerflow.sp.report.length import normalize_contract, validate_budgets
from deerflow.subagents.config import SubagentConfig


def _planned(tmp_path):
    ctx = _enabled_context(tmp_path)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    ledger.payload["length_contract"] = {**_small_contract(), "paragraph_plan_required": True}
    ledger.save()
    tree = _budgeted_tree()
    tree["nodes"][1]["constraints"].update(
        paragraph_plan=[{"paragraph": i, "purpose": f"Core point {i}", "target_words": 10} for i in (1, 2, 3)],
        scope_exclusions=["Optional historical survey"],
    )
    return ctx, ledger, tree


def test_paragraph_contract_is_explicit_and_strict():
    assert normalize_contract({"paragraph_plan_required": True})["paragraph_plan_required"] is True
    for value in (1, "true", None):
        with pytest.raises(ReportValidationError, match="paragraph_plan_required"):
            normalize_contract({"paragraph_plan_required": value})


@pytest.mark.parametrize("problem", ["missing", "empty", "bad_purpose", "duplicate", "oversum", "undersum", "no_exclusions"])
def test_invalid_paragraph_plan_is_rejected_before_writing(tmp_path, problem):
    _, ledger, tree = _planned(tmp_path)
    c = tree["nodes"][1]["constraints"]
    if problem == "missing":
        c.pop("paragraph_plan")
    elif problem == "empty":
        c["paragraph_plan"] = []
    elif problem == "bad_purpose":
        c["paragraph_plan"][0]["purpose"] = ""
    elif problem == "duplicate":
        c["paragraph_plan"][1]["paragraph"] = 1
    elif problem == "oversum":
        c["paragraph_plan"][0]["target_words"] = 99
    elif problem == "undersum":
        for p in c["paragraph_plan"]:
            p["target_words"] = 1
    else:
        c["scope_exclusions"] = []
    with pytest.raises(ReportValidationError, match="paragraph|scope_exclusions"):
        validate_budgets(tree, ledger)
    ledger.payload["length_contract"].pop("paragraph_plan_required")
    validate_budgets(tree, ledger)  # Legacy reports keep their existing policy.


def test_accepted_plan_reaches_writer_system_instruction(tmp_path):
    ctx, ledger, tree = _planned(tmp_path)
    original = deepcopy(tree["nodes"][1]["constraints"])
    validate_budgets(tree, ledger)
    assert _outline(ctx, tree).error is None
    _, task, _ = _writer_action(ctx, "w1")
    cfg, _, _ = hooks.configure_report_subagent(task, state=ctx.state, available_tools=[SimpleNamespace(name=n) for n in ("read_file", "write_file")], subagent_config=SubagentConfig(name="sp-reporter", description="write"))
    match = re.search(r"<assigned-chapter-length>\s*(.*?)\s*</assigned-chapter-length>", cfg.system_prompt, re.S)
    assigned = json.loads(match.group(1))
    assert assigned["paragraph_plan"] == original["paragraph_plan"]
    assert assigned["scope_exclusions"] == original["scope_exclusions"]
    assert assigned["max_words"] == 50
    assert cfg.max_output_tokens is None
