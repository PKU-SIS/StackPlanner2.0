"""Outline token-cap recovery uses the delegation's current delivery file."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import HumanMessage

from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.artifacts import SPArtifactAdapter
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.report import hooks
from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus, SPSubagentTask


def _context(tmp_path: Path) -> HandlerContext:
    state = {
        "thread_data": {"outputs_path": str(tmp_path)},
        "messages": [HumanMessage(content="写一份简报", id="m1")],
    }
    ctx = HandlerContext(state=state, stack=TaskMemoryStack(), thread_id="t1", run_id="run1")
    hooks.start_report(ctx, SPAction.create(ActionType.REPLAN, reason="start", task="start", metadata={"report_action": "start"}))
    return ctx


def _tree() -> dict:
    return {
        "report_title": "恢复候选",
        "requirements": [{"id": "R1", "input_id": "u1", "text": "写一份简报", "hard": True}],
        "nodes": [
            {
                "node_id": "n1",
                "parent_id": None,
                "kind": "writable",
                "title": "简报正文",
                "question": "任务要求是什么？",
                "argument": ["覆盖用户要求"],
                "conclusion": "给出简报。",
                "evidence": [],
                "constraints": {"single_source_only": True},
                "requirement_ids": ["R1"],
                "relations": [],
                "gap_ids": [],
                "status": "planned",
            }
        ],
        "removed_node_ids": [],
        "write_requests": [{"key": "w-n1", "node_ids": ["n1"]}],
        "gap_outcomes": [],
        "user_decisions": [],
        "feedback_dispositions": [],
        "draft_dispositions": [],
    }


def _outline_delegate(ctx: HandlerContext) -> tuple[SPAction, SPSubagentTask]:
    action = SPAction.create(
        ActionType.DELEGATE,
        action_id="a-outline-recovery",
        reason="plan report",
        task="plan report",
        target_agent="outline",
        stage="planning",
        metadata={"report_task": "plan"},
    )
    task_metadata = {"stage": "planning"}
    refs: dict = {}
    hooks.prepare_delegation(action, ctx, task_metadata, refs)
    task = SPSubagentTask(
        action_id=action.action_id,
        subagent_type="outline",
        task="plan report",
        description="plan",
        metadata=task_metadata,
        context_refs=refs,
        stage="planning",
    )
    return action, task


def _result() -> SPSubagentResult:
    return SPSubagentResult(
        status=SPSubagentStatus.COMPLETED,
        result="candidate",
        stop_reason="token_capped",
        finish_reason="stop",
        artifact_type="outline",
        artifact_metadata={"completion_status": "partial"},
    )


def test_token_capped_current_delivery_file_recovers_through_full_acceptance(tmp_path):
    ctx = _context(tmp_path)
    action, task = _outline_delegate(ctx)
    delivery = Path(str(task.metadata["outline_delivery_path"]))
    delivery.parent.mkdir(parents=True, exist_ok=True)
    delivery.write_text(json.dumps(_tree(), ensure_ascii=False), encoding="utf-8")
    entry = ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning")

    result = hooks.handle_success(
        action,
        ctx,
        _result(),
        task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=entry,
    )

    assert result.next_step == "continue"
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.payload["tree"]["version"] == 1
    accepted_file = Path(str(ledger.payload["tree"]["path"]))
    accepted = json.loads(accepted_file.read_text(encoding="utf-8"))
    assert accepted["accepted_with_stop_reason"] == "token_capped"
    events = [event for event in result.run_events if event.get("event_type") == "sp.report.outline_accepted"]
    assert events and events[0]["payload"]["accepted_with_stop_reason"] == "token_capped"


def test_token_capped_without_current_file_still_uses_technical_rejection(tmp_path):
    ctx = _context(tmp_path)
    action, task = _outline_delegate(ctx)
    entry = ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning")

    result = hooks.handle_success(
        action,
        ctx,
        _result(),
        task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=entry,
    )

    assert result.error
    assert "token_capped" in result.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert not ledger.payload.get("tree")


def test_token_capped_invalid_current_file_is_not_accepted(tmp_path):
    ctx = _context(tmp_path)
    action, task = _outline_delegate(ctx)
    delivery = Path(str(task.metadata["outline_delivery_path"]))
    delivery.parent.mkdir(parents=True, exist_ok=True)
    delivery.write_text("{not valid json", encoding="utf-8")
    entry = ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning")

    result = hooks.handle_success(
        action,
        ctx,
        _result(),
        task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=entry,
    )

    assert "invalid_report_tree" in (result.error or "") or "JSON syntax error" in (result.error or "")
    assert "Do not reduce node count" in (result.error or "")
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert not ledger.payload.get("tree")
