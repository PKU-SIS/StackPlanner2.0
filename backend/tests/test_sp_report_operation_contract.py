"""Central's explicit follow-up operation bounds GitAgent's terminal tools."""

import json

import pytest
from report_pilot_audit import native_role
from test_sp_report_turns import accepted, followup

from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.report.ledger import ReportValidationError
from deerflow.sp.report.turns import adapter
from deerflow.sp.report.turns.tools import bind_profile, configure
from deerflow.sp.subagents import SPSubagentTask
from deerflow.subagents.config import SubagentConfig


def bind(tmp_path, operation):
    ctx, _ = accepted(tmp_path)
    followup(ctx, text="Restore the whole earlier accepted version; discard later edits")
    action = SPAction.create(ActionType.DELEGATE, task="Inspect report history", reason="User follow-up", target_agent="perception", metadata={"report_profile": "git_agent", "report_operation": operation})
    meta, refs = {}, {}
    bind_profile(action, ctx, meta, refs)
    task = SPSubagentTask(action_id=action.action_id, description="resolve", subagent_type="perception", task="Resolve follow-up", thread_id="t1", run_id=ctx.run_id, metadata=meta)
    cfg, tools, role = configure(task, ctx.state, SubagentConfig(name="sp-perception", description="Git profile"))
    return ctx, cfg, tools


def test_exact_reuse_contract_cannot_prepare_and_does_not_advance_head(tmp_path):
    ctx, cfg, tools = bind(tmp_path, "exact_reuse")
    assert {t.name for t in tools} == {"report_inspect_version", "report_reuse_version"}
    assert native_role("sp-perception", cfg.tools) == "git_agent"
    store, _, head = adapter.session(ctx.state, thread_id="t1")
    before = store.index()["head"]
    reuse = next(t for t in tools if t.name == "report_reuse_version")
    with pytest.raises(ReportValidationError):
        reuse.invoke({"version_id": head["turn_id"], "mode": "read_only"})
    result = json.loads(reuse.invoke({"version_id": head["turn_id"], "mode": "exact_reuse"}))
    assert result["status"] == "exact_reuse"
    assert store.index()["head"] == before
    assert store.index()["active_version"] == head["turn_id"]
    assert len(list((tmp_path / "report").glob("r*"))) == 1


def test_patch_contract_uses_prepare_only(tmp_path):
    _, cfg, tools = bind(tmp_path, "patch")
    assert {t.name for t in tools} == {"report_inspect_version", "report_prepare_turn"}
    assert native_role("sp-perception", cfg.tools) == "git_agent"


def test_readonly_contract_rejects_version_switch(tmp_path):
    ctx, cfg, tools = bind(tmp_path, "read_only")
    assert {t.name for t in tools} == {"report_inspect_version", "report_reuse_version"}
    store, _, head = adapter.session(ctx.state, thread_id="t1")
    reuse = next(t for t in tools if t.name == "report_reuse_version")
    before = store.index()
    with pytest.raises(ReportValidationError):
        reuse.invoke({"version_id": head["turn_id"], "mode": "exact_reuse"})
    assert store.index() == before


def test_unknown_operation_fails_before_recording_attempt(tmp_path):
    with pytest.raises(ReportValidationError):
        bind(tmp_path, "unrestricted")
