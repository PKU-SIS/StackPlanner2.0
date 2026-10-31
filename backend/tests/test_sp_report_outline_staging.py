"""Report-only Outline staging behavior, including the real acceptance handler."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from langchain.tools import tool
from langchain_core.messages import HumanMessage

from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.artifacts import SPArtifactAdapter
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.report import hooks
from deerflow.sp.report import store as report_store
from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.report.outline_staging import OutlineTreeStage, build_outline_staging_tool
from deerflow.sp.report.tools import build_register_research_request_tool
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus, SPSubagentTask
from deerflow.subagents import get_subagent_config


def _state(tmp_path: Path) -> dict:
    return {
        "thread_data": {"outputs_path": str(tmp_path)},
        "messages": [HumanMessage(content="请写一份研究报告。", id="m1")],
    }


def _ctx(tmp_path: Path) -> HandlerContext:
    ctx = HandlerContext(state=_state(tmp_path), stack=TaskMemoryStack(), thread_id="t1", run_id="run1")
    hooks.start_report(ctx, SPAction.create(ActionType.REPLAN, reason="start", task="start", metadata={"report_action": "start"}))
    return ctx


def _paths(ctx: HandlerContext) -> report_store.ReportPaths:
    context = hooks.report_context(ctx.state)
    assert context.outputs_path and context.report_id
    return report_store.ReportPaths(context.outputs_path, context.report_id)


def _register(ctx: HandlerContext, action_id: str, *, quote: str = "报告") -> str:
    tool = build_register_research_request_tool(paths=_paths(ctx), action_id=action_id)
    payload = {
        "mode": "new",
        "question": f"{quote}的事实依据",
        "constraints": {},
        "why_insufficient": "no saved material",
        "stop_condition": "one attempt",
        "requirement_refs": [{"input_id": "u1", "quote": quote}],
        "node_ids": [],
        "materials_checked": [],
    }
    return json.loads(tool.func(questions=[payload], tool_call_id=f"tc-{action_id}"))["registered"][0]["gap_id"]


def _delegate(ctx: HandlerContext, action_id: str, report_task: str) -> tuple[SPAction, SPSubagentTask]:
    action = SPAction.create(
        ActionType.DELEGATE,
        action_id=action_id,
        reason="test",
        task="test outline",
        target_agent="outline",
        stage="planning",
        metadata={"report_task": report_task},
    )
    meta = {"stage": "planning"}
    refs: dict = {}
    hooks.prepare_delegation(action, ctx, meta, refs)
    task = SPSubagentTask(
        action_id=action_id,
        subagent_type="outline",
        task="test outline",
        description="test",
        metadata=meta,
        context_refs=refs,
        stage="planning",
    )
    return action, task


def _tree(*, gap_ids: list[str]) -> dict:
    return {
        "report_title": "暂存测试",
        "requirements": [{"id": "R1", "input_id": "u1", "text": "报告", "hard": True}],
        "nodes": [
            {"node_id": "n1", "parent_id": None, "kind": "section", "title": "背景"},
            {
                "node_id": "n2",
                "parent_id": "n1",
                "kind": "writable",
                "title": "事实",
                "question": "事实是什么？",
                "argument": ["逐项核对"],
                "conclusion": "待证据",
                "evidence": [],
                "constraints": {},
                "requirement_ids": ["R1"],
                "relations": [],
                "gap_ids": gap_ids,
                "status": "needs_evidence",
            },
        ],
        "removed_node_ids": [],
        "write_requests": [],
        "gap_outcomes": [],
        "user_decisions": [],
        "feedback_dispositions": [],
        "draft_dispositions": [],
    }


def _accept(ctx: HandlerContext, action: SPAction, task: SPSubagentTask, tree: dict) -> object:
    entry = ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning")
    result = SPSubagentResult(
        status=SPSubagentStatus.COMPLETED,
        result="tree",
        artifact_content=json.dumps(tree, ensure_ascii=False),
        artifact_type="outline",
        artifact_metadata={"completion_status": "complete"},
    )
    return hooks.handle_success(action, ctx, result, task, artifact_adapter=SPArtifactAdapter(), delegate_entry=entry)


def _accept_delivery(ctx: HandlerContext, action: SPAction, task: SPSubagentTask) -> object:
    entry = ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning")
    result = SPSubagentResult(
        status=SPSubagentStatus.COMPLETED,
        result="",
        artifact_type="outline",
        artifact_metadata={"completion_status": "complete", "delivery_mode": "file"},
    )
    return hooks.handle_success(action, ctx, result, task, artifact_adapter=SPArtifactAdapter(), delegate_entry=entry)


def _accept_baseline(ctx: HandlerContext, gap_ids: list[str]) -> None:
    plan_action, plan_task = _delegate(ctx, "a-plan", "plan")
    accepted = _accept(ctx, plan_action, plan_task, _tree(gap_ids=gap_ids))
    assert accepted.next_step == "continue", accepted.error


def _revise_tool(ctx: HandlerContext, action_id: str = "a-revise"):
    action, task = _delegate(ctx, action_id, "revise")
    delivery = Path(str(task.metadata["outline_delivery_path"]))
    tool = build_outline_staging_tool(
        paths=_paths(ctx),
        action_id=action.action_id,
        delivery_path=delivery,
        resume_action_id=task.metadata.get("outline_stage_resume_action_id"),
    )
    return action, task, tool, delivery


def test_stage_finalize_uses_unresolved_and_real_handler_accepts(tmp_path):
    ctx = _ctx(tmp_path)
    gap_id = _register(ctx, "a-gap")
    _accept_baseline(ctx, [gap_id])
    action, task, tool, delivery = _revise_tool(ctx)
    assert task.context_refs["report_packet"]["delivery"]["staging_tool"] is True
    assert "report_update_outline_tree" in task.context_refs["report_packet"]["delivery"]["instruction"]

    receipt = json.loads(tool.func(
        base_version=1,
        patch={
            "upsert_nodes": [{"node_id": "n2", "title": "事实（修订）"}],
            "gap_outcomes": [{"gap_id": gap_id, "outcome": "unresolved", "reason": "证据不足"}],
            "write_requests": [],
        },
        finalize=True,
    ))
    assert receipt["staged"] is False
    assert Path(receipt["delivery_path"]) == delivery

    accepted = _accept_delivery(ctx, action, task)
    assert accepted.next_step == "continue", accepted.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.payload["tree"]["version"] == 2
    assert ledger.require_gap(gap_id)["status"] == "awaiting_user"


def test_mixed_closed_and_pending_gaps_do_not_require_repeating_closed_outcome(tmp_path):
    ctx = _ctx(tmp_path)
    g1 = _register(ctx, "a-gap", quote="报告")
    g2 = _register(ctx, "a-gap-2", quote="报告")
    _accept_baseline(ctx, [g1, g2])
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    ledger.require_gap(g2)["status"] = "resolved"
    ledger.save()

    action, task, tool, _ = _revise_tool(ctx)
    raw = tool.func(
        base_version=1,
        patch={"gap_outcomes": [{"gap_id": g1, "outcome": "unresolved", "reason": "仍缺"}], "write_requests": []},
        finalize=True,
    )
    assert json.loads(raw)["staged"] is False
    accepted = _accept_delivery(ctx, action, task)
    assert accepted.next_step == "continue", accepted.error


def test_write_requests_must_be_explicit_and_omission_does_not_clear(tmp_path):
    ctx = _ctx(tmp_path)
    gap_id = _register(ctx, "a-gap")
    _accept_baseline(ctx, [gap_id])
    _, _, tool, delivery = _revise_tool(ctx)

    raw = tool.func(
        base_version=1,
        patch={"gap_outcomes": [{"gap_id": gap_id, "outcome": "unresolved", "reason": "缺"}]},
        finalize=True,
    )
    payload = json.loads(raw)
    assert payload["staged"] is False
    assert "write_requests must be explicitly supplied" in payload["error"]
    assert not delivery.exists()


def test_parallel_patches_preserve_both_nodes(tmp_path):
    ctx = _ctx(tmp_path)
    gap_id = _register(ctx, "a-gap")
    _accept_baseline(ctx, [gap_id])
    _, _, tool, _ = _revise_tool(ctx)

    def patch(node_id: str, title: str) -> None:
        raw = tool.func(base_version=1, patch={"upsert_nodes": [{"node_id": node_id, "title": title}]})
        assert json.loads(raw)["staged"] is True

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda args: patch(*args), [("n1", "并行一"), ("n2", "并行二")]))

    stage = OutlineTreeStage(_paths(ctx), "a-revise", Path(str(_paths(ctx).directory / "outline" / "delivery-a-revise.json")))
    titles = {node["node_id"]: node["title"] for node in stage._candidate["nodes"]}
    assert titles["n1"] == "并行一"
    assert titles["n2"] == "并行二"


def test_stale_base_and_node_loss_reject_without_delivery(tmp_path):
    ctx = _ctx(tmp_path)
    gap_id = _register(ctx, "a-gap")
    _accept_baseline(ctx, [gap_id])
    _, _, tool, delivery = _revise_tool(ctx)
    tool.func(base_version=1, patch={"gap_outcomes": [{"gap_id": gap_id, "outcome": "unresolved", "reason": "缺"}], "write_requests": []})

    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    ledger.payload["tree"]["version"] = 2
    ledger.save()
    stale = json.loads(tool.func(base_version=1, patch={}, finalize=True))
    assert stale["staged"] is False
    assert "stale" in stale["error"] or "base" in stale["error"]
    assert not delivery.exists()


def test_truncated_stage_is_rejected_and_accepted_tree_unchanged(tmp_path):
    ctx = _ctx(tmp_path)
    gap_id = _register(ctx, "a-gap")
    _accept_baseline(ctx, [gap_id])
    paths = _paths(ctx)
    before = (paths.tree_file(1)).read_bytes()
    stage_path = paths.directory / "outline" / "_staging" / "a-broken.json"
    stage_path.parent.mkdir(parents=True, exist_ok=True)
    stage_path.write_text('{"candidate": {"nodes": [', encoding="utf-8")

    with pytest.raises(Exception):
        OutlineTreeStage(paths, "a-broken", paths.directory / "outline" / "delivery.json")
    assert paths.tree_file(1).read_bytes() == before


def test_node_loss_is_rejected_without_delivery_or_accepted_tree_change(tmp_path):
    ctx = _ctx(tmp_path)
    gap_id = _register(ctx, "a-gap")
    _accept_baseline(ctx, [gap_id])
    paths = _paths(ctx)
    before = paths.tree_file(1).read_bytes()
    action, task, tool, delivery = _revise_tool(ctx)
    tool.func(
        base_version=1,
        patch={"gap_outcomes": [{"gap_id": gap_id, "outcome": "unresolved", "reason": "缺"}], "write_requests": []},
    )
    stage_path = paths.directory / "outline" / "_staging" / "a-revise.json"
    stage = json.loads(stage_path.read_text(encoding="utf-8"))
    stage["candidate"]["nodes"] = [n for n in stage["candidate"]["nodes"] if n["node_id"] != "n1"]
    stage_path.write_text(json.dumps(stage, ensure_ascii=False), encoding="utf-8")
    raw = tool.func(base_version=1, patch={}, finalize=True)
    payload = json.loads(raw)
    assert payload["staged"] is False
    assert "missing" in payload["error"] or "accepted nodes" in payload["error"]
    assert not delivery.exists()
    assert paths.tree_file(1).read_bytes() == before


def test_new_action_can_resume_same_report_base_stage(tmp_path):
    ctx = _ctx(tmp_path)
    gap_id = _register(ctx, "a-gap")
    _accept_baseline(ctx, [gap_id])
    paths = _paths(ctx)
    source_action = "a-source-stage"
    first = build_outline_staging_tool(
        paths=paths,
        action_id=source_action,
        delivery_path=paths.directory / "outline" / "delivery-source.json",
    )
    assert json.loads(first.func(base_version=1, patch={"upsert_nodes": [{"node_id": "n1", "title": "源改动"}]}))["staged"] is True

    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    ledger.payload["rejected_outline"] = {"action_id": source_action, "path": "unused"}
    ledger.save()
    _, resumed_task = _delegate(ctx, "a-resumed-stage", "revise")
    assert resumed_task.metadata["outline_stage_resume_action_id"] == source_action
    assert resumed_task.context_refs["report_packet"]["delivery"]["staging_resume_action_id"] == source_action

    second = build_outline_staging_tool(
        paths=paths,
        action_id="a-resumed-stage",
        delivery_path=Path(str(resumed_task.metadata["outline_delivery_path"])),
        resume_action_id=resumed_task.metadata["outline_stage_resume_action_id"],
    )
    raw = second.func(
        base_version=1,
        patch={"upsert_nodes": [{"node_id": "n2", "title": "续改动"}], "write_requests": []},
        finalize=True,
    )
    payload = json.loads(raw)
    assert payload["staged"] is False
    candidate = json.loads(Path(payload["delivery_path"]).read_text(encoding="utf-8"))
    by_id = {n["node_id"]: n for n in candidate["nodes"]}
    assert by_id["n1"]["title"] == "源改动"
    assert by_id["n2"]["title"] == "续改动"


def test_old_action_fields_are_not_copied_into_stage(tmp_path):
    ctx = _ctx(tmp_path)
    gap_id = _register(ctx, "a-gap")
    tree = _tree(gap_ids=[gap_id])
    tree["gap_outcomes"] = [{"gap_id": gap_id, "outcome": "resolved", "reason": "historical"}]
    tree["write_requests"] = [{"key": "historical-w", "node_ids": ["n2"]}]
    plan_action, plan_task = _delegate(ctx, "a-plan-old", "plan")
    accepted = _accept(ctx, plan_action, plan_task, tree)
    assert accepted.next_step == "continue", accepted.error
    paths = _paths(ctx)
    stage = OutlineTreeStage(paths, "a-fresh", paths.directory / "outline" / "delivery.json")
    assert stage._candidate["gap_outcomes"] == []
    assert stage._candidate["user_decisions"] == []
    assert stage._candidate["removed_node_ids"] == []
    assert stage._candidate["write_requests"] == []
    assert stage._candidate["feedback_dispositions"] == []
    assert stage._candidate["draft_dispositions"] == []
    assert stage._write_requests_set is False


def test_revise_assembly_uses_staging_tool_not_write_file(tmp_path):
    ctx = _ctx(tmp_path)
    gap_id = _register(ctx, "a-gap")
    _accept_baseline(ctx, [gap_id])
    _, task = _delegate(ctx, "a-revise-assembly", "revise")

    @tool("read_file")
    def read_file(path: str) -> str:
        """Read a file."""
        return ""

    @tool("write_file")
    def write_file(path: str, content: str) -> str:
        """Write a file."""
        return ""

    config, tools, role = hooks.configure_report_subagent(
        task,
        state=ctx.state,
        available_tools=[read_file, write_file],
        subagent_config=get_subagent_config("sp-outline"),
    )
    names = {str(getattr(item, "name", "")) for item in tools}
    assert role == "outline"
    assert "report_update_outline_tree" in config.tools
    assert "write_file" not in config.tools
    assert "report_update_outline_tree" in names
