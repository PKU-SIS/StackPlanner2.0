"""Lossless reads and recovery identity without forced role selection."""

import asyncio
import json
from types import SimpleNamespace

from langchain_core.messages import ToolMessage
from test_collusion_audit import scoped_profile as scoped_profile
from test_collusion_html_candidate_delivery_v21 import Adapter, create, invoke
from test_collusion_reporter_budget_v23 import real_executor_module as real_executor_module

from app.experiments.collusion_audit.closing import BudgetClosingMiddleware
from app.experiments.collusion_audit.recovery import recovery_scope_key, tool_scope_refs
from deerflow.sp.subagents import SPSubagentTask
from deerflow.subagents.config import SubagentConfig


def test_large_receipts_do_not_remove_tools_when_enabled():
    tools = [object()]
    messages = [ToolMessage("甲" * 30000, tool_call_id=str(i)) for i in range(8)]

    class Request:
        state = {"messages": messages}

        def __init__(self):
            self.messages = messages
            self.tools = tools

        def override(self, **kw):
            return SimpleNamespace(messages=kw.get("messages", self.messages), tools=kw.get("tools", self.tools))

    got = BudgetClosingMiddleware(compact_results=True, enforce_read_limits=False).project(Request())
    assert got.tools is tools
    repeated = [ToolMessage(json.dumps({"status": "already_returned", "result_ref": "same"}), tool_call_id=str(i)) for i in range(8)]
    request = Request()
    request.messages = repeated
    request.state = {"messages": repeated}
    assert BudgetClosingMiddleware(close_repeated_reads=True, enforce_read_limits=False).project(request).tools is tools


def test_complete_candidate_delivery_is_not_limited_to_eight_or_sixty_thousand(tmp_path):
    adapter = Adapter(tmp_path)
    adapter.resilient_delivery = True
    ids = []
    for i in range(1, 11):
        cid, _ = create(tmp_path, i, 5000)
        ids.append(cid)
    result = invoke(adapter, ids)
    assert len(result["candidates"]) == 10 and result["complete_requested_set"]
    assert not result["remaining_candidate_ids"]


def test_full_file_read_and_verified_candidate_identity(scoped_profile, tmp_path):
    p = type(scoped_profile)(scoped_profile.rule, scoped_profile.project, scoped_profile.outputs, tmp_path / "newcache", scoped_profile.trace, quality_controls=True, rule_level_review=True, report_format="html", resilient_delivery=True)
    run = "M01_20261111T100000_abcdef1234"
    p.adapters.runs.append({"run_id": run, "status": "partial"})
    root = p.outputs / ("bid_audit/" + run + "/m01_candidates")
    root.mkdir(parents=True)
    text = json.dumps({"candidate_id": "C000001", "evidence": "原文" * 50000}, ensure_ascii=False)
    file = root / "C000001.json"
    file.write_text(text)
    p.adapters.register_calculation_sources({"run_id": run, "collections": {"findings": {"count": 1}}})
    reader = next(t for t in p.tools if t.name == "read_file")
    args = {"file_path": "/mnt/user-data/outputs/bid_audit/" + run + "/m01_candidates/C000001.json"}
    result = json.loads(asyncio.run(reader.ainvoke(args)))
    assert result["text"] == text and result["next_offset"] is None
    assert tool_scope_refs("read_file", result, args) == {"code-candidate:" + run + ":C000001"}
    forged = {**result, "source": "model_authored_note"}
    assert not tool_scope_refs("read_file", forged, args)
    file.write_text(text + " ")
    changed = json.loads(asyncio.run(reader.ainvoke(args)))
    assert not tool_scope_refs("read_file", changed, args)
    part = json.loads(asyncio.run(reader.ainvoke({**args, "limit": 40})))
    assert part["next_offset"] == 40


def test_primary_current_artifact_scope_is_not_lost_to_background_refs():
    task = SimpleNamespace(
        action_id="a",
        input_refs=["main"],
        metadata={"source_artifact_ids": ["main", "background"]},
        context_refs={"artifact_refs": {"one": {"artifact_id": "main", "metadata": {"_sp_recovery_scope_key": "main-scope"}}, "_history": [{"artifact_id": "background", "metadata": {"_sp_recovery_scope_key": "other"}}]}},
    )
    assert recovery_scope_key(SimpleNamespace(scope_refs=lambda _: set()), task) == "main-scope"


def test_only_opt_in_evidence_roles_receive_larger_call_budget(scoped_profile, tmp_path):
    p = type(scoped_profile)(scoped_profile.rule, scoped_profile.project, scoped_profile.outputs, tmp_path / "rescache", scoped_profile.trace, quality_controls=True, rule_level_review=True, report_format="html", resilient_delivery=True)
    for role in ["researcher", "coder", "reporter", "perception"]:
        task = SPSubagentTask("a", role, "自由决定如何核对原件", "stage")
        p.prepare_task(task)
        cfg = p.configure_subagent(task, SubagentConfig("sp-" + role, "native"))
        assert cfg.tool_call_limit == (64 if role in ["researcher", "coder"] else None)
        if role in ["researcher", "coder"]:
            assert cfg.max_turns >= 390
    assert p.extensions().allow_limited_report
    assert not scoped_profile.extensions().allow_limited_report


def test_native_loop_budget_preserves_defaults_and_accepts_trusted_capacity(real_executor_module):
    _subagent_tool_loop_limits = real_executor_module._subagent_tool_loop_limits
    _reserve_subagent_loop_guard_budget = real_executor_module._reserve_subagent_loop_guard_budget
    assert _subagent_tool_loop_limits(400) == (12, 20)
    assert _subagent_tool_loop_limits(400, 64) == (38, 64)
    guard = SimpleNamespace(tool_freq_hard_limit=50, tool_freq_warn=30)
    _reserve_subagent_loop_guard_budget([guard], max_turns=400, tool_call_limit=64)
    assert guard.tool_total_hard_limit == 64 and guard.tool_freq_hard_limit == 64
