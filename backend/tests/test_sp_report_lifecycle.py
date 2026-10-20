"""P1 report lifecycle: request, gaps, research rounds, HITL continuation."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain.tools import tool
from langchain_core.messages import HumanMessage

from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.handlers.delegate import DelegateHandler
from deerflow.sp.actions.handlers.finish import FinishHandler
from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.artifacts import SPArtifactAdapter
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.report import hooks
from deerflow.sp.report import store as report_store
from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.report.materials import build_fetch_material_tool
from deerflow.sp.report.tools import build_register_research_request_tool
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus, SPSubagentTask


def _state(tmp_path: Path, messages: list[HumanMessage] | None = None) -> dict:
    return {
        "thread_data": {"outputs_path": str(tmp_path)},
        "messages": messages or [HumanMessage(content="请写一份关于示例公司的研究报告。", id="m1")],
    }


def _context(state: dict, stack: TaskMemoryStack | None = None) -> HandlerContext:
    return HandlerContext(state=state, stack=stack or TaskMemoryStack(), thread_id="t1", run_id="run1")


def _start(ctx: HandlerContext) -> dict:
    action = SPAction.create(ActionType.REPLAN, action_id="a-start", reason="start report", task="start report", metadata={"report_action": "start"})
    return hooks.start_report(ctx, action)


def _report_id(ctx: HandlerContext) -> str:
    context = hooks.report_context(ctx.state)
    assert context.report_id
    return context.report_id


def _paths(ctx: HandlerContext) -> report_store.ReportPaths:
    context = hooks.report_context(ctx.state)
    assert context.outputs_path and context.report_id
    return report_store.ReportPaths(context.outputs_path, context.report_id)


def _register_gap(ctx: HandlerContext, action_id: str, *, question: str = "示例公司 FY2024 偿付能力", mode: str = "new", continue_gap_id: str | None = None) -> dict:
    tool = build_register_research_request_tool(paths=_paths(ctx), action_id=action_id)
    request: dict = {
        "mode": mode,
        "question": question,
        "constraints": {"period": "FY2024"},
        "why_insufficient": "saved materials do not contain the metric",
        "stop_condition": "official annual report note",
        "requirement_refs": [{"input_id": "u1", "quote": "示例公司"}],
        "node_ids": [],
        "materials_checked": [],
    }
    if continue_gap_id:
        request["continue_gap_id"] = continue_gap_id
    raw = tool.func(questions=[request], tool_call_id=f"tc-{action_id}-{question}")
    return json.loads(raw)


def _delegate(ctx: HandlerContext, action_id: str, target: str, metadata: dict) -> tuple[SPAction, SPSubagentTask]:
    action = SPAction.create(
        ActionType.DELEGATE,
        action_id=action_id,
        reason=f"delegate {target}",
        task=f"{target} task",
        target_agent=target,
        stage="research" if target == "researcher" else "planning",
        metadata=metadata,
    )
    task_meta = {"stage": action.stage}
    refs: dict = {}
    hooks.prepare_delegation(action, ctx, task_meta, refs)
    task = SPSubagentTask(
        action_id=action_id,
        subagent_type=target,
        task=f"{target} task",
        description="test",
        metadata=task_meta,
        context_refs=refs,
        stage=action.stage,
    )
    return action, task


def _tree(*, gap_ids: list[str], outcomes: list[dict] | None = None, decisions: list[dict] | None = None, title: str = "示例公司报告") -> dict:
    return {
        "report_title": title,
        "requirements": [{"id": "R1", "input_id": "u1", "text": "示例公司 FY2024 偿付能力", "hard": True}],
        "nodes": [
            {"node_id": "n1", "parent_id": None, "kind": "section", "title": "偿付能力"},
            {
                "node_id": "n2",
                "parent_id": "n1",
                "kind": "writable",
                "title": "FY2024 指标",
                "question": "示例公司 FY2024 母公司口径偿付能力如何？",
                "argument": ["读取官方附注", "核对口径"],
                "conclusion": "待证据支持",
                "evidence": [],
                "constraints": {"period": "FY2024"},
                "requirement_ids": ["R1"],
                "relations": [],
                "gap_ids": gap_ids,
                "status": "needs_evidence",
            },
        ],
        "removed_node_ids": [],
        "write_requests": [],
        "gap_outcomes": outcomes or [],
        "user_decisions": decisions or [],
        "feedback_dispositions": [],
        "draft_dispositions": [],
    }


def _accept_outline(ctx: HandlerContext, action: SPAction, task: SPSubagentTask, tree: dict) -> object:
    delegate_entry = ctx.stack.append_delegate("delegate outline", thread_id="t1", run_id="run1", stage="planning")
    result = SPSubagentResult(
        status=SPSubagentStatus.COMPLETED,
        result="tree",
        artifact_content=json.dumps(tree, ensure_ascii=False),
        artifact_type="outline",
        artifact_metadata={"completion_status": "complete"},
    )
    return hooks.handle_success(action, ctx, result, task, artifact_adapter=SPArtifactAdapter(), delegate_entry=delegate_entry)


def _research(
    ctx: HandlerContext,
    action_id: str,
    gap_ids: list[str],
    *,
    execution: str = "searched",
    tool_ok: bool = True,
    save_material: bool = True,
    host_executions: list[dict] | None = None,
    model_executions: bool = True,
    summary: str = "research summary",
) -> object:
    action, task = _delegate(ctx, action_id, "researcher", {"report_gap_ids": gap_ids})
    material_id = None
    if save_material:

        @tool("web_fetch")
        def fetch(url: str) -> str:
            """Fetch a URL."""
            return f"Saved evidence for {gap_ids[0]} at {url}."

        material_tool = build_fetch_material_tool(paths=_paths(ctx), action_id=action_id, gap_ids=gap_ids, fetch_tool=fetch)
        material_payload = json.loads(material_tool.func(url=f"https://example.com/{action_id}"))
        assert material_payload["saved"] is True
        material_id = material_payload["material_id"]
    delegate_entry = ctx.stack.append_delegate("delegate researcher", thread_id="t1", run_id="run1", stage="research")
    result = SPSubagentResult(
        status=SPSubagentStatus.COMPLETED,
        result=summary,
        artifact_content=f"# Summary\n\n{summary}",
        artifact_type="research_observation",
        artifact_metadata={
            "completion_status": "complete",
            "gap_attempts": [{"gap_id": gap_ids[0], "execution": execution, "material_ids": [material_id] if material_id else [], "note": "test"}],
            **({"host_tool_execution_records": host_executions} if host_executions is not None else {}),
            **({"tool_execution_records": [{"tool": "web_search", "ok": tool_ok}]} if model_executions else {}),
        },
    )
    return hooks.handle_success(action, ctx, result, task, artifact_adapter=SPArtifactAdapter(), delegate_entry=delegate_entry)


def test_start_preserves_full_request_and_supplements(tmp_path):
    long_request = "关于示例公司的偿付能力研究。" * 400
    state = _state(
        tmp_path,
        [
            HumanMessage(content=long_request, id="m1"),
            HumanMessage(content="补充一句：只看母公司口径。", id="m2"),
            HumanMessage(content="继续", id="m3"),
        ],
    )
    ctx = _context(state)
    result = _start(ctx)
    assert result["started"] is True
    assert result["input_ids"] == ["u1", "u2", "u3"]
    assert result["request_chars"] > 2400
    request_text = (tmp_path / "report" / "r1" / "request.md").read_text(encoding="utf-8")
    assert long_request[:80] in request_text
    assert "只看母公司口径" in request_text
    assert request_text.rstrip().endswith("继续")
    ledger = ReportLedger.load_for_context(hooks.report_context(state))
    assert ledger.input("u3")["preview"] == "继续"
    assert ledger.inputs()[0]["applied"] is True
    assert ledger.unapplied_input_ids() == []


def test_start_reports_existing_active_report_without_abandoning(tmp_path):
    ctx = _context(_state(tmp_path))
    _start(ctx)
    second = hooks.start_report(ctx, SPAction.create(ActionType.REPLAN, reason="start again", task="start", metadata={"report_action": "start"}))
    assert second["started"] is False
    assert second["report_id"] == "r1"
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.payload["status"] == "active"


def test_gap_registration_continue_preserves_identity_and_new_does_not_inherit(tmp_path):
    ctx = _context(_state(tmp_path))
    _start(ctx)
    first = _register_gap(ctx, "a-outline-1", question="示例公司 FY2024 偿付能力")
    assert first["registered"][0]["gap_id"] == "g1"
    again = _register_gap(ctx, "a-outline-2", question="示例公司 FY2024 偿付能力（补充附注）", mode="continue", continue_gap_id="g1")
    assert again["registered"][0]["gap_id"] == "g1"
    separate = _register_gap(ctx, "a-outline-3", question="示例公司 FY2024 偿付能力", mode="new")
    assert separate["registered"][0]["gap_id"] == "g2"
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    g1 = ledger.require_gap("g1")
    assert g1["question"].endswith("（补充附注）")
    assert len(g1["history"]) >= 2


def test_invalid_tree_is_not_committed_and_registered_gap_survives(tmp_path):
    ctx = _context(_state(tmp_path))
    _start(ctx)
    _register_gap(ctx, "a-outline")
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.gaps()[0]["status"] == "open"
    broken = _tree(gap_ids=["g1"])
    broken["nodes"][1]["evidence"] = [{"material_id": "m-unknown", "source_ids": [], "locator": {}, "use": "x"}]
    action, task = _delegate(ctx, "a-outline", "outline", {})
    result = _accept_outline(ctx, action, task, broken)
    assert result.next_step == "error_recoverable"
    assert "unknown material" in result.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.payload["tree"] is None
    assert not list((tmp_path / "report" / "r1" / "tree").glob("*.json")) if (tmp_path / "report" / "r1" / "tree").exists() else True
    assert ledger.gaps()[0]["status"] == "open"


def test_two_effective_rounds_then_user_question_and_answer_returns_to_outline(tmp_path):
    ctx = _context(_state(tmp_path))
    _start(ctx)
    _register_gap(ctx, "a-outline")
    outline_action, outline_task = _delegate(ctx, "a-outline", "outline", {})
    accepted = _accept_outline(ctx, outline_action, outline_task, _tree(gap_ids=["g1"]))
    assert accepted.next_step == "continue", accepted.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.require_gap("g1")["node_ids"] == ["n2"]

    first = _research(ctx, "a-r1", ["g1"], tool_ok=True)
    assert first.next_step == "continue", first.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert (ledger.require_gap("g1")["status"], ledger.require_gap("g1")["rounds"]) == ("awaiting_outline", 1)

    judged1 = _accept_outline(ctx, outline_action, outline_task, _tree(gap_ids=["g1"], outcomes=[{"gap_id": "g1", "outcome": "unresolved", "reason": "口径不符"}]))
    assert judged1.next_step == "continue", judged1.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert (ledger.require_gap("g1")["status"], ledger.require_gap("g1")["rounds"]) == ("open", 1)

    second = _research(ctx, "a-r2", ["g1"], tool_ok=True)
    assert second.next_step == "continue", second.error
    judged2 = _accept_outline(ctx, outline_action, outline_task, _tree(gap_ids=["g1"], outcomes=[{"gap_id": "g1", "outcome": "unresolved", "reason": "两轮仍未找到 FY2024 口径"}]))
    assert judged2.next_step == "continue", judged2.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    gap = ledger.require_gap("g1")
    assert (gap["status"], gap["rounds"]) == ("awaiting_user", 2)
    assert ledger.control_summary()["gaps"][0]["next_action"] == "ask_human"

    ask = SPAction.create(
        ActionType.ASK_HUMAN,
        action_id="a-ask",
        reason="need user decision",
        task="请提供 FY2024 来源或授权调整口径",
        metadata={"report_gap_ids": ["g1"], "question": "请提供 FY2024 来源或授权调整口径"},
    )
    hooks.prepare_ask_human(ask, ctx, SimpleNamespace(interaction_id="sphitl_1"))
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.require_gap("g1")["interaction_id"] == "sphitl_1"

    recorded = hooks.record_human_feedback_input(
        {**ctx.state, "sp_pending_human_interaction": {"interaction_id": "sphitl_1", "metadata": {"report_gap_ids": ["g1"]}}},
        "我不知道，按已公开口径处理。",
        pending={"interaction_id": "sphitl_1", "metadata": {"report_gap_ids": ["g1"]}},
    )
    assert recorded and recorded["gap_ids"] == ["g1"]
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    gap = ledger.require_gap("g1")
    assert gap["status"] == "awaiting_outline"
    assert gap["rounds"] == 2

    tree = _tree(
        gap_ids=["g1"],
        outcomes=[
            {
                "gap_id": "g1",
                "outcome": "resolved",
                "reason": "按用户授权采用公开口径，材料已核对",
                "evidence_material_ids": [ledger.materials()[0]["material_id"]],
                "user_input_id": recorded["input_id"],
            }
        ],
        decisions=[
            {
                "input_id": recorded["input_id"],
                "gap_ids": ["g1"],
                "action": "provide_material",
                "reason": "用户给出处置方向",
            }
        ],
    )
    resolved = _accept_outline(ctx, outline_action, outline_task, tree)
    assert resolved.next_step == "continue", resolved.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.require_gap("g1")["status"] == "resolved"
    assert ledger.payload["tree"]["version"] == 4
    assert ledger.input(recorded["input_id"])["applied"] is True


def test_technical_failure_does_not_charge_a_round(tmp_path):
    ctx = _context(_state(tmp_path))
    _start(ctx)
    _register_gap(ctx, "a-outline")
    outline_action, outline_task = _delegate(ctx, "a-outline", "outline", {})
    _accept_outline(ctx, outline_action, outline_task, _tree(gap_ids=["g1"]))
    result = _research(ctx, "a-r1", ["g1"], execution="searched", tool_ok=False, save_material=False)
    assert result.next_step == "continue", result.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    gap = ledger.require_gap("g1")
    assert gap["rounds"] == 0
    assert gap["status"] == "awaiting_outline"
    assert gap["technical_attempts"] == 1


def test_finish_is_blocked_while_report_active(tmp_path):
    ctx = _context(_state(tmp_path))
    _start(ctx)
    action = SPAction.create(ActionType.FINISH, action_id="a-finish", reason="done", task="done")
    result = FinishHandler().handle(action, ctx)
    assert result.next_step == "error_recoverable"
    assert "P1" in result.error


def test_ledger_read_failure_is_not_treated_as_disabled(tmp_path):
    ctx = _context(_state(tmp_path))
    _start(ctx)
    ledger_path = tmp_path / "report" / "r1" / "ledger.json"
    ledger_path.write_text("{not-json", encoding="utf-8")
    context = hooks.report_context(ctx.state)
    assert context.status == "error"
    assert hooks.finish_block_reason(ctx).startswith("Report state is unavailable")
    action = SPAction.create(ActionType.DELEGATE, action_id="a-x", reason="x", task="x", target_agent="outline", stage="planning")
    task_meta: dict = {}
    with pytest.raises(Exception, match="report ledger unavailable"):
        hooks.prepare_delegation(action, ctx, task_meta, {})


def test_user_decision_continue_research_adds_rounds_and_replay_is_rejected(tmp_path):
    ctx = _context(_state(tmp_path))
    _start(ctx)
    _register_gap(ctx, "a-outline")
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    gap = ledger.require_gap("g1")
    gap["status"] = "awaiting_user"
    gap["rounds"] = 2
    input_id = ledger.add_user_input("请再查一轮", source="hitl_feedback", interaction_id="sphitl_continue")
    ledger.save()
    decision = {
        "input_id": input_id,
        "gap_ids": ["g1"],
        "action": "continue_research",
        "reason": "用户明确授权再查一轮",
        "additional_rounds": 1,
    }
    applied = ledger.apply_user_decisions([decision])
    assert applied and applied[0]["action"] == "continue_research"
    gap = ledger.require_gap("g1")
    assert gap["status"] == "open"
    assert gap["additional_rounds"] == 1
    assert gap["rounds"] == 2
    ledger.save()
    with pytest.raises(Exception, match="already applied"):
        ledger.apply_user_decisions([decision])


def test_user_answer_i_do_not_know_does_not_close_gap(tmp_path):
    ctx = _context(_state(tmp_path))
    _start(ctx)
    _register_gap(ctx, "a-outline")
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    gap = ledger.require_gap("g1")
    gap["status"] = "awaiting_user"
    input_id = ledger.add_user_input("我不知道", source="hitl_feedback", interaction_id="sphitl_unknown")
    ledger.save()
    ledger.apply_user_decisions(
        [
            {
                "input_id": input_id,
                "gap_ids": ["g1"],
                "action": "no_decision",
                "reason": "用户无法提供，保留未解决",
            }
        ]
    )
    gap = ledger.require_gap("g1")
    assert gap["status"] == "awaiting_user"
    assert gap["outcome"] is None


def test_resume_and_close_report_lifecycle(tmp_path):
    ctx = _context(_state(tmp_path))
    _start(ctx)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    ledger.payload["status"] = "delivered"
    ledger.payload["delivered"] = {"artifact_id": "spart_demo", "version": 9}
    ledger.save()
    resumed = hooks.resume_report(ctx, SPAction.create(ActionType.REPLAN, reason="resume", task="resume", metadata={"report_action": "resume"}))
    assert resumed["status"] == "active"
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.payload["delivered"] is None
    assert ledger.payload["delivery_history"][0]["artifact_id"] == "spart_demo"

    input_id = ledger.add_user_input("取消这份报告", source="user_message", source_message_id="m-cancel")
    ledger.save()
    with pytest.raises(Exception, match="report_input_id"):
        hooks.close_report(
            ctx,
            SPAction.create(ActionType.REPLAN, reason="close", task="close", metadata={"report_close_reason": "用户取消"}),
            reason="用户取消",
        )
    closed = hooks.close_report(
        ctx,
        SPAction.create(
            ActionType.REPLAN,
            reason="close",
            task="close",
            metadata={"report_close_reason": "用户取消", "report_input_id": input_id},
        ),
        reason="用户取消",
    )
    assert closed["status"] == "abandoned"
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.payload["closed"]["input_id"] == input_id
    with pytest.raises(Exception, match="abandoned"):
        hooks.resume_report(ctx, SPAction.create(ActionType.REPLAN, reason="resume", task="resume", metadata={"report_action": "resume"}))


def test_sync_user_inputs_deduplicates_visible_messages(tmp_path):
    ctx = _context(_state(tmp_path))
    _start(ctx)
    state = {
        **ctx.state,
        "messages": [
            HumanMessage(content="请写一份关于示例公司的研究报告。", id="m1"),
            HumanMessage(content="只看母公司口径", id="m2"),
        ],
    }
    hooks.sync_user_inputs(state)
    hooks.sync_user_inputs(state)
    ledger = ReportLedger.load_for_context(hooks.report_context(state))
    assert [item["preview"] for item in ledger.inputs()] == ["请写一份关于示例公司的研究报告。", "只看母公司口径"]
    assert ledger.unapplied_input_ids() == ["u2"]


def test_executor_exception_releases_in_flight_action_without_a_round(tmp_path):
    ctx = _context(_state(tmp_path))
    _start(ctx)
    _register_gap(ctx, "a-outline")
    outline_action, outline_task = _delegate(ctx, "a-outline", "outline", {})
    assert _accept_outline(ctx, outline_action, outline_task, _tree(gap_ids=["g1"])).next_step == "continue"

    class RaisingExecutor:
        def execute(self, task):
            raise RuntimeError("executor unavailable")

    handler = DelegateHandler(executor=RaisingExecutor())
    action = SPAction.create(
        ActionType.DELEGATE,
        action_id="a-r-raise",
        reason="research",
        task="research",
        target_agent="researcher",
        stage="research",
        metadata={"report_gap_ids": ["g1"]},
    )
    with pytest.raises(RuntimeError, match="executor unavailable"):
        handler.handle(action, ctx)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    gap = ledger.require_gap("g1")
    assert gap["status"] == "open"
    assert gap["rounds"] == 0
    assert gap["technical_attempts"] == 1
    assert ledger.action("a-r-raise")["status"] == "failed"


def _prepare_gap_for_round(ctx: HandlerContext) -> None:
    _register_gap(ctx, "a-outline")
    action, task = _delegate(ctx, "a-outline", "outline", {})
    accepted = _accept_outline(ctx, action, task, _tree(gap_ids=["g1"]))
    assert accepted.next_step == "continue", accepted.error


def test_search_completed_without_hit_counts_as_an_effective_round(tmp_path):
    ctx = _context(_state(tmp_path))
    _start(ctx)
    _prepare_gap_for_round(ctx)
    result = _research(
        ctx,
        "a-r-search",
        ["g1"],
        save_material=False,
        host_executions=[{"tool": "web_search", "ok": True}],
        model_executions=False,
    )
    assert result.next_step == "continue", result.error
    gap = ReportLedger.load_for_context(hooks.report_context(ctx.state)).require_gap("g1")
    assert (gap["status"], gap["rounds"]) == ("awaiting_outline", 1)


def test_only_read_file_or_model_claimed_execution_never_charges_a_round(tmp_path):
    ctx = _context(_state(tmp_path))
    _start(ctx)
    _prepare_gap_for_round(ctx)
    result = _research(
        ctx,
        "a-r-read",
        ["g1"],
        save_material=False,
        host_executions=[{"tool": "read_file", "ok": True}],
        model_executions=True,
    )
    assert result.next_step == "continue", result.error
    gap = ReportLedger.load_for_context(hooks.report_context(ctx.state)).require_gap("g1")
    assert (gap["status"], gap["rounds"]) == ("awaiting_outline", 0)
    assert gap["technical_attempts"] == 1


def test_all_host_research_executions_failed_does_not_charge_a_round(tmp_path):
    ctx = _context(_state(tmp_path))
    _start(ctx)
    _prepare_gap_for_round(ctx)
    result = _research(
        ctx,
        "a-r-fail",
        ["g1"],
        save_material=False,
        host_executions=[{"tool": "web_search", "ok": False}, {"tool": "report_fetch_material", "ok": False}],
        model_executions=False,
    )
    assert result.next_step == "continue", result.error
    gap = ReportLedger.load_for_context(hooks.report_context(ctx.state)).require_gap("g1")
    assert (gap["status"], gap["rounds"]) == ("awaiting_outline", 0)
    assert gap["technical_attempts"] == 1


def test_ordinary_user_supplement_uses_apply_to_plan_without_a_gap(tmp_path):
    ctx = _context(_state(tmp_path))
    _start(ctx)
    state = {
        **ctx.state,
        "messages": [
            HumanMessage(content="请写一份关于示例公司的研究报告。", id="m1"),
            HumanMessage(content="增加一张横向比较表。", id="m2"),
        ],
    }
    hooks.sync_user_inputs(state)
    ledger = ReportLedger.load_for_context(hooks.report_context(state))
    input_id = ledger.unapplied_input_ids()[0]
    action, task = _delegate(ctx, "a-outline", "outline", {})
    tree = _tree(
        gap_ids=[],
        decisions=[
            {
                "input_id": input_id,
                "gap_ids": [],
                "action": "apply_to_plan",
                "reason": "在树中加入横向比较节点",
            }
        ],
    )
    result = _accept_outline(ctx, action, task, tree)
    assert result.next_step == "continue", result.error
    ledger = ReportLedger.load_for_context(hooks.report_context(state))
    assert ledger.input(input_id)["applied"] is True
    decision = ledger.payload["user_decisions"][0]
    assert decision["action"] == "apply_to_plan"
    assert decision["gap_id"] is None


def test_start_after_close_creates_a_new_report_and_unrelated_work_is_not_blocked(tmp_path):
    ctx = _context(_state(tmp_path))
    _start(ctx)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    input_id = ledger.add_user_input("取消旧报告", source="user_message", source_message_id="m-cancel")
    ledger.save()
    hooks.close_report(
        ctx,
        SPAction.create(
            ActionType.REPLAN,
            reason="close",
            task="close",
            metadata={"report_close_reason": "用户取消", "report_input_id": input_id},
        ),
        reason="用户取消",
    )

    researcher = SPAction.create(
        ActionType.DELEGATE,
        action_id="ordinary-research",
        reason="unrelated",
        task="研究另一个公开主题",
        target_agent="researcher",
        stage="research",
    )
    task_meta: dict = {"stage": "research"}
    refs: dict = {}
    hooks.prepare_delegation(researcher, ctx, task_meta, refs)
    assert "report_role" not in task_meta
    assert refs == {}

    fresh = hooks.start_report(ctx, SPAction.create(ActionType.REPLAN, reason="start", task="start", metadata={"report_action": "start"}))
    assert fresh["started"] is True
    assert fresh["report_id"] == "r2"
    assert (tmp_path / "report" / "r1" / "ledger.json").exists()
    assert (tmp_path / "report" / "r2" / "ledger.json").exists()


def test_delivered_report_can_start_a_new_report_or_resume(tmp_path):
    ctx = _context(_state(tmp_path))
    _start(ctx)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    ledger.payload["status"] = "delivered"
    ledger.save()
    resumed = hooks.resume_report(ctx, SPAction.create(ActionType.REPLAN, reason="resume", task="resume", metadata={"report_action": "resume"}))
    assert resumed["status"] == "active"

    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    ledger.payload["status"] = "delivered"
    ledger.save()
    fresh = hooks.start_report(ctx, SPAction.create(ActionType.REPLAN, reason="start", task="start", metadata={"report_action": "start"}))
    assert fresh["started"] is True
    assert fresh["report_id"] == "r2"


def test_concurrent_gap_registration_keeps_both_gaps(tmp_path):
    ctx = _context(_state(tmp_path))
    _start(ctx)
    tool = build_register_research_request_tool(paths=_paths(ctx), action_id="a-outline")

    def register(index: int) -> dict:
        return json.loads(
            tool.func(
                questions=[
                    {
                        "mode": "new",
                        "question": f"并发问题 {index}",
                        "constraints": {},
                        "why_insufficient": "test",
                        "stop_condition": "test",
                        "requirement_refs": [{"input_id": "u1", "quote": "示例公司"}],
                        "node_ids": [],
                        "materials_checked": [],
                    }
                ],
                tool_call_id=f"tc-{index}",
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(register, [1, 2]))
    assert {item["registered"][0]["gap_id"] for item in results} == {"g1", "g2"}
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert {gap["gap_id"] for gap in ledger.gaps()} == {"g1", "g2"}
