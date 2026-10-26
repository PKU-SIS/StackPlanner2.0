"""Outline token-cap recovery uses the delegation's current delivery file."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import HumanMessage

from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.artifacts import SPArtifactAdapter
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.report import hooks
from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus, SPSubagentTask
from deerflow.sp.subagents.dr2_adapter import normalize_dr2_subagent_result


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


def test_rejected_candidate_is_referenced_but_not_prefilled(tmp_path):
    ctx = _context(tmp_path)
    action, task = _outline_delegate(ctx)
    accepted = hooks.handle_success(
        action,
        ctx,
        SPSubagentResult(
            status=SPSubagentStatus.COMPLETED,
            result="accepted base",
            artifact_content=json.dumps(_tree(), ensure_ascii=False),
            artifact_type="outline",
            artifact_metadata={"completion_status": "complete"},
        ),
        task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning"),
    )
    assert accepted.next_step == "continue"

    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    candidate = ledger.paths.directory / "outline" / "rejected-candidate.json"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(json.dumps(_tree(), ensure_ascii=False), encoding="utf-8")
    ledger.payload["rejected_outline"] = {
        "path": str(candidate),
        "virtual_path": ledger.paths.virtual(candidate),
        "chars": len(candidate.read_text(encoding="utf-8")),
        "action_id": "prior-outline",
        "errors": "token_capped",
    }
    ledger.save()

    revise = SPAction.create(
        ActionType.DELEGATE,
        action_id="a-outline-revise",
        reason="recover retained candidate",
        task="revise report",
        target_agent="outline",
        stage="planning",
        metadata={"report_task": "plan"},
    )
    task_metadata = {"stage": "planning"}
    refs: dict = {}
    hooks.prepare_delegation(revise, ctx, task_metadata, refs)
    packet = refs["report_packet"]
    delivery_path = Path(str(task_metadata["outline_delivery_path"]))

    assert not delivery_path.exists()
    assert packet["rejected_outline"]["virtual_path"] == ledger.paths.virtual(candidate)
    assert "Recovery basis" in packet["delivery"]["instruction"]
    assert str(packet["delivery"]["path"]) == ledger.paths.virtual(delivery_path)
    assert task_metadata["rejected_candidate_path"] == str(candidate)
    assert packet["tree"]["version"] == 1

    revise_task = SPSubagentTask(
        action_id=revise.action_id,
        subagent_type="outline",
        task="revise report",
        description="revise",
        metadata=task_metadata,
        context_refs=refs,
        stage="planning",
    )
    no_output = hooks.handle_success(
        revise,
        ctx,
        SPSubagentResult(
            status=SPSubagentStatus.COMPLETED,
            result="no output",
            artifact_content=None,
            artifact_type="outline",
            artifact_metadata={"completion_status": "complete"},
        ),
        revise_task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning"),
    )
    assert no_output.next_step == "error_recoverable"
    current_ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert current_ledger.payload["tree"]["version"] == 1


def test_incomplete_candidate_allows_corrected_delivery(tmp_path):
    ctx = _context(tmp_path)
    action, task = _outline_delegate(ctx)
    accepted = hooks.handle_success(
        action,
        ctx,
        SPSubagentResult(
            status=SPSubagentStatus.COMPLETED,
            result="accepted base",
            artifact_content=json.dumps(_tree(), ensure_ascii=False),
            artifact_type="outline",
            artifact_metadata={"completion_status": "complete"},
        ),
        task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning"),
    )
    assert accepted.next_step == "continue"

    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    candidate = ledger.paths.directory / "outline" / "rejected-candidate.json"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text('{"report_title":"partial","nodes":[', encoding="utf-8")
    ledger.payload["rejected_outline"] = {
        "path": str(candidate),
        "virtual_path": ledger.paths.virtual(candidate),
        "chars": len(candidate.read_text(encoding="utf-8")),
        "action_id": "prior-outline",
        "errors": "token_capped",
    }
    ledger.save()

    revise = SPAction.create(
        ActionType.DELEGATE,
        action_id="a-outline-revise-update",
        reason="recover retained candidate",
        task="revise report",
        target_agent="outline",
        stage="planning",
        metadata={"report_task": "revise"},
    )
    task_metadata = {"stage": "planning"}
    refs: dict = {}
    hooks.prepare_delegation(revise, ctx, task_metadata, refs)
    delivery_path = Path(str(task_metadata["outline_delivery_path"]))
    corrected = _tree()
    corrected["nodes"][0]["question"] = "recovered improved question"
    delivery_path.write_text(json.dumps(corrected, ensure_ascii=False), encoding="utf-8")
    revise_task = SPSubagentTask(
        action_id=revise.action_id,
        subagent_type="outline",
        task="revise report",
        description="revise",
        metadata=task_metadata,
        context_refs=refs,
        stage="planning",
    )
    result = hooks.handle_success(
        revise,
        ctx,
        SPSubagentResult(
            status=SPSubagentStatus.COMPLETED,
            result="corrected candidate",
            artifact_content=None,
            artifact_type="outline",
            artifact_metadata={"completion_status": "complete", "delivery_mode": "file", "tree_path": str(delivery_path)},
        ),
        revise_task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning"),
    )

    assert result.next_step == "continue", result.error
    current = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    accepted_tree = json.loads(Path(current.payload["tree"]["path"]).read_text(encoding="utf-8"))
    assert accepted_tree["nodes"][0]["question"] == "recovered improved question"


def test_recovered_tool_evidence_from_adapter_is_not_an_outline_candidate(tmp_path):
    ctx = _context(tmp_path)
    action, task = _outline_delegate(ctx)
    recovered = json.dumps(_tree(), ensure_ascii=False)
    raw = SimpleNamespace(
        status=SPSubagentStatus.COMPLETED,
        result=recovered,
        result_source="recovered_tool_evidence",
        artifact_content=None,
        artifact_type=None,
        artifact_metadata={},
        stop_reason=None,
        ai_messages=[{"response_metadata": {"finish_reason": "length"}}],
    )
    normalized = normalize_dr2_subagent_result(raw, task=task)
    assert normalized.artifact_content is None

    result = hooks.handle_success(
        action,
        ctx,
        normalized,
        task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning"),
    )

    assert result.next_step == "error_recoverable"
    assert "No tree candidate" in (result.error or "")
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert not ledger.payload.get("rejected_outline")


def test_diagnostic_text_is_not_saved_as_rejected_tree(tmp_path):
    ctx = _context(tmp_path)
    action, task = _outline_delegate(ctx)
    entry = ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning")
    result = _result()
    result.result = "[TOKEN BUDGET EXCEEDED] The total token usage has exceeded the safety limit."
    result.artifact_content = None

    rejected = hooks.handle_success(
        action,
        ctx,
        result,
        task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=entry,
    )

    assert rejected.next_step == "error_recoverable"
    assert "No tree candidate" in (rejected.error or "")
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert not ledger.payload.get("rejected_outline")


def test_incomplete_tree_is_retained_as_candidate_with_parse_error(tmp_path):
    ctx = _context(tmp_path)
    action, task = _outline_delegate(ctx)
    delivery = Path(str(task.metadata["outline_delivery_path"]))
    delivery.parent.mkdir(parents=True, exist_ok=True)
    delivery.write_text('{"report_title":"partial","nodes":[', encoding="utf-8")
    entry = ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning")

    rejected = hooks.handle_success(
        action,
        ctx,
        _result(),
        task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=entry,
    )

    assert rejected.next_step == "error_recoverable"
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    record = ledger.payload.get("rejected_outline")
    assert record is not None
    assert record["kind"] == "tree_candidate"
    assert record["parse_error"]


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
