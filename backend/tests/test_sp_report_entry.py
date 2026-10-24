"""Entry compatibility: report start is carried by the first delegation."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import HumanMessage

from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.handlers.delegate import DelegateHandler
from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.prompt import PromptContextBuilder
from deerflow.sp.report import report_context
from deerflow.sp.report import store as report_store
from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.report.presentation import derive_report_view
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus, SPSubagentTask
from deerflow.sp.subagents.dr2_adapter import _merge_research_continuations


class _CapturingExecutor:
    def __init__(self) -> None:
        self.task: SPSubagentTask | None = None

    def execute(self, task: SPSubagentTask) -> SPSubagentResult:
        self.task = task
        return SPSubagentResult(
            status=SPSubagentStatus.COMPLETED,
            result="pre-research summary",
            artifact_content="pre-research summary",
            artifact_type="research_observation",
            artifact_metadata={"completion_status": "complete", "gap_attempts": []},
        )


class _MergedResearchExecutor:
    def __init__(self, result: SPSubagentResult) -> None:
        self.result = result

    def execute(self, _task: SPSubagentTask) -> SPSubagentResult:
        return self.result


def test_merged_research_latest_state_is_consumed_by_delegate_observe(tmp_path):
    first = SPSubagentResult(
        status=SPSubagentStatus.COMPLETED,
        result="first pass: A2A comparison still missing",
        artifact_content="first pass body",
        artifact_type="research_observation",
        artifact_metadata={
            "completion_status": "partial",
            "evidence_gaps": ["A2A comparison still missing"],
            "central_evidence_preview": "first preview only",
        },
    )
    second = SPSubagentResult(
        status=SPSubagentStatus.COMPLETED,
        result="second pass: A2A comparison found",
        artifact_content="second pass body",
        artifact_type="research_observation",
        artifact_metadata={
            "completion_status": "complete",
            "evidence_gaps": [],
            "central_evidence_preview": "second preview with A2A comparison",
        },
    )
    merged = _merge_research_continuations([first, second])
    assert merged.result == "second pass: A2A comparison found"
    assert merged.artifact_metadata["evidence_gaps"] == []
    assert merged.artifact_metadata["central_evidence_preview"] == "second preview with A2A comparison"
    assert "first pass body" in (merged.artifact_content or "")
    assert "second pass body" in (merged.artifact_content or "")

    ctx = HandlerContext(state=_state(tmp_path), stack=TaskMemoryStack(), thread_id="t1", run_id="run1")
    result = DelegateHandler(executor=_MergedResearchExecutor(merged)).handle(_action(), ctx)

    assert result.next_step == "continue"
    observed = str(ctx.stack.entries[-1].content)
    assert "second preview with A2A comparison" in observed
    assert "second pass: A2A comparison found" in observed
    assert "first preview only" not in observed
    assert "A2A comparison still missing" not in observed


def test_downstream_observe_keeps_all_research_evidence_and_drops_old_gap(tmp_path):
    passes = [
        ("CHN", 12951.1782397043, "IND and BRA remain."),
        ("IND", 2434.45111237626, "BRA remains."),
        ("BRA", 10377.5892792557, None),
    ]
    results = [
        SPSubagentResult(
            status=SPSubagentStatus.COMPLETED,
            result=f"Fetched {country}={value}.",
            artifact_content=json.dumps({"country": country, "value": value}),
            artifact_type="research_observation",
            artifact_metadata={
                "completion_status": "complete" if gap is None else "partial",
                "evidence_gaps": [] if gap is None else [gap],
            },
        )
        for country, value, gap in passes
    ]
    merged = _merge_research_continuations(results)
    ctx = HandlerContext(state=_state(tmp_path), stack=TaskMemoryStack(), thread_id="t1", run_id="run1")

    result = DelegateHandler(executor=_MergedResearchExecutor(merged)).handle(_action(), ctx)

    assert result.next_step == "continue"
    observed = str(ctx.stack.entries[-1].content)
    for value in ("12951.1782397043", "2434.45111237626", "10377.5892792557"):
        assert value in observed
    assert "IND and BRA remain." not in observed
    assert "BRA remains." not in observed


def _state(tmp_path: Path) -> dict:
    return {
        "thread_data": {"outputs_path": str(tmp_path)},
        "messages": [HumanMessage(content="请写一份示例公司的研究报告。", id="m1")],
    }


def _action(*, metadata: dict | None = None, target: str = "researcher") -> SPAction:
    return SPAction.create(
        ActionType.DELEGATE,
        action_id="a-1",
        reason="test",
        task="pre-research",
        target_agent=target,
        stage="research",
        metadata=metadata or {},
    )


def test_start_metadata_starts_and_binds_same_delegation(tmp_path):
    ctx = HandlerContext(state=_state(tmp_path), stack=TaskMemoryStack(), thread_id="t1", run_id="run1")
    executor = _CapturingExecutor()
    result = DelegateHandler(executor=executor).handle(
        _action(metadata={"report_action": "start"}),
        ctx,
    )

    assert result.next_step == "continue"
    context = report_context(ctx.state)
    assert context.is_active
    assert executor.task is not None
    assert executor.task.metadata["report_role"] == "researcher"
    assert executor.task.metadata["report_id"] == context.report_id
    assert executor.task.run_id == "run1"


def test_old_delegation_without_metadata_keeps_report_disabled(tmp_path):
    ctx = HandlerContext(state=_state(tmp_path), stack=TaskMemoryStack(), thread_id="t1", run_id="run1")
    executor = _CapturingExecutor()
    result = DelegateHandler(executor=executor).handle(_action(), ctx)

    assert result.next_step == "continue"
    assert report_context(ctx.state).status == "disabled"
    assert executor.task is not None
    assert "report_role" not in executor.task.metadata
    assert not report_store.pointer_path(tmp_path).exists()


def test_start_metadata_rejects_non_report_target(tmp_path):
    ctx = HandlerContext(state=_state(tmp_path), stack=TaskMemoryStack(), thread_id="t1", run_id="run1")
    result = DelegateHandler(executor=_CapturingExecutor()).handle(
        _action(metadata={"report_action": "start"}, target="coder"),
        ctx,
    )
    assert result.next_step == "error_recoverable"
    assert "outline or researcher" in (result.error or "")
    assert report_context(ctx.state).status == "disabled"


def test_report_outline_continuation_registers_current_run_without_resume(tmp_path):
    ctx = HandlerContext(state=_state(tmp_path), stack=TaskMemoryStack(), thread_id="t1", run_id="run-start")
    start = DelegateHandler(executor=_CapturingExecutor()).handle(
        _action(metadata={"report_action": "start"}),
        ctx,
    )
    assert start.next_step == "continue"
    assert report_context(ctx.state).is_active

    continuation = HandlerContext(
        state=ctx.state,
        stack=TaskMemoryStack(),
        thread_id="t1",
        run_id="run-outline-continuation",
    )
    from deerflow.sp.report import hooks

    action = SPAction.create(
        ActionType.DELEGATE,
        action_id="a-outline",
        reason="continue outline",
        task="Revise the outline.",
        target_agent="outline",
        stage="planning",
        metadata={"report_task": "revise"},
    )
    task_metadata: dict = {"report_task": "revise"}
    hooks.prepare_delegation(action, continuation, task_metadata, {})

    active = report_context(ctx.state)
    ledger = ReportLedger.load_for_context(active)
    assert "run-outline-continuation" in ledger.payload["run_ids"]
    view = derive_report_view(
        ctx.state,
        run_status="success",
        run_id="run-outline-continuation",
    )
    assert view is not None and view["status"] != "not_report"
    ordinary = derive_report_view(ctx.state, run_status="success", run_id="run-ordinary")
    assert ordinary is not None and ordinary["status"] == "not_report"


def test_registered_existing_file_is_hidden_only_in_report_context():
    builder = PromptContextBuilder()
    refs = {
        "node_draft": {
            "artifact_id": "spart_report",
            "type": "node_draft",
            "virtual_path": "/mnt/user-data/outputs/report/r1/drafts/n1/d1.md",
            "metadata": {"report_id": "r1", "registered_existing_file": True},
        },
        "ordinary": {
            "artifact_id": "spart_ordinary",
            "type": "generated_file",
            "virtual_path": "/mnt/user-data/outputs/ordinary.txt",
            "metadata": {"registered_existing_file": True},
        },
    }
    context = builder.build(TaskMemoryStack(), artifact_refs=refs, current_run_id="run1")
    assert "spart_report" in context
    assert "spart_ordinary" in context
    assert context.count("registered_existing_file") == 1