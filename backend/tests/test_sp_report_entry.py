"""Entry compatibility: report start is carried by the first delegation."""

from __future__ import annotations

from pathlib import Path

from langchain_core.messages import HumanMessage

from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.handlers.delegate import DelegateHandler
from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.report import report_context
from deerflow.sp.report import store as report_store
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus, SPSubagentTask


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
