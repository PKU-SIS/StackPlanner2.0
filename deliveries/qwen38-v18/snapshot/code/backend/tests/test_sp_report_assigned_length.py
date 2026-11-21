"""Keep exact host chapter budgets visible in the scoped Writer instruction."""

import json
import re
from types import SimpleNamespace

import pytest
from test_sp_report_length import _budgeted_tree, _enabled_context, _small_contract
from test_sp_report_writing import _outline, _writer_action

from deerflow.sp.report import hooks
from deerflow.sp.report.ledger import ReportLedger
from deerflow.subagents.config import SubagentConfig


@pytest.mark.parametrize("percent,expected", [(100, (30, 50)), (40, (12, 20))])
def test_bound_chapter_budget_is_in_writer_system_instruction(tmp_path, percent, expected):
    ctx = _enabled_context(tmp_path)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    ledger.payload["length_contract"] = _small_contract(writer_target_percent=percent)
    ledger.save()
    assert _outline(ctx, _budgeted_tree()).error is None
    _, task, _ = _writer_action(ctx, "w1")
    tools = [SimpleNamespace(name=n) for n in ("read_file", "write_file")]
    cfg, _, _ = hooks.configure_report_subagent(task, state=ctx.state, available_tools=tools, subagent_config=SubagentConfig(name="sp-reporter", description="write"))
    match = re.search(r"<assigned-chapter-length>\s*(.*?)\s*</assigned-chapter-length>", cfg.system_prompt, re.S)
    assert match, "The actual system instruction must carry the assigned numeric budget"
    assigned = json.loads(match.group(1))
    assert assigned["node_id"] == "n2"
    assert (assigned["target_words"], assigned["max_words"]) == expected
    assert assigned["scope"] == "chapter_body"
    assert assigned["complete_draft_required"] is True
    assert assigned["truncate_allowed"] is False
    assert cfg.max_output_tokens is None
    # Ordinary reports must not inherit another report's length instruction.
    ledger = ReportLedger.load(ledger.paths)
    del ledger.payload["length_contract"]
    ledger.save()
    ordinary, _, _ = hooks.configure_report_subagent(task, state=ctx.state, available_tools=tools, subagent_config=SubagentConfig(name="sp-reporter", description="write"))
    assert "<assigned-chapter-length>" not in ordinary.system_prompt
