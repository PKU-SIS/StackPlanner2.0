"""The real next scoped RAG auditor receives native recovery facts."""

from types import SimpleNamespace

import pytest

from deerflow.sp.actions import SPAction
from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.handlers.delegate import DelegateHandler
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus


@pytest.mark.parametrize("previous_run,previous_role,expected", [("run", "researcher", True), ("other-run", "researcher", False), ("run", "reporter", False)])
def test_actual_rag_auditor_task_retains_previous_gap_and_ref_if_central_omits_them(previous_run, previous_role, expected):
    stack = TaskMemoryStack()
    stack.append_observe(
        "An incomplete native audit", actor=previous_role, run_id=previous_run, result_ref="spart_actual_native_draft", metadata={"completion_status": "partial", "evidence_gaps": ["Previous auditor reported missing pages 2-3."]}
    )
    state = {"sp_loop_run_id": "run", "sp_task_memory": stack.to_dict(), "sp_task_contract": {"source_requirements": {"audit_agent": "researcher", "files": [], "document_ids": ["scoped-document"]}}}
    captured = []

    def execute(task):
        captured.append(task)
        return SPSubagentResult(status=SPSubagentStatus.FAILED, error="No service in unit test")

    action = SPAction.create("DELEGATE", target_agent="researcher", task="Read and audit the original sources", stage="research", reason="Recover the audit")
    DelegateHandler(executor=SimpleNamespace(execute=execute)).handle(action, HandlerContext(state=state, stack=stack, thread_id="thread", run_id="run"))
    assert len(captured) == 1
    task = captured[0]
    assert ("Previous auditor reported missing pages 2-3." in task.task) is expected
    assert ("spart_actual_native_draft" in task.input_refs) is expected
    if expected:
        assert "materially different recovery method" in task.task
        assert "total_pages" in task.task and "out_of_range" in task.task
        assert "not missing source pages" in task.task
        assert "not proof that the substantive audit is complete" in task.task
        assert "For a file-only audit" not in task.task
    assert task.budgets == {}
    assert stack.entries[0].metadata["completion_status"] == "partial"
