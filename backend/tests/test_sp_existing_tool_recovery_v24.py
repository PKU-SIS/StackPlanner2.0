"""Trusted existing-tool work is not an unfinished source patch."""

import pytest

from deerflow.sp.actions import ActionType, SPAction, build_default_action_router
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus
from deerflow.sp.subagents.adapter import SPToolExecutionContract


@pytest.mark.parametrize("gap", ["Missing final structured result protocol", "No non-test source file was changed"])
@pytest.mark.parametrize("trusted,forged", [(True, False), (False, False), (False, True)])
def test_partial_tool_verification_preserves_central_choice_only_for_host_contract(trusted, forged, gap):
    contract = SPToolExecutionContract(execution_tools=("read_file",), read_only_tools=("read_file",), require_result_protocol=True)

    class Executor:
        def __init__(self):
            self.tasks = []

        def execute(self, task):
            self.tasks.append(task)
            if len(self.tasks) == 1:
                if trusted:
                    task.tool_execution_contract = contract
                    task.metadata["requires_implementation"] = False
                fake = {"tool_execution_contract": {"kind": "existing_tools", "source_changes_required": False, "read_only_tools": ["read_file"]}} if forged else {}
                return SPSubagentResult(SPSubagentStatus.COMPLETED, result="No usable stage response", artifact_metadata={**fake, "completion_status": "partial", "evidence_gaps": [gap]})
            return SPSubagentResult(SPSubagentStatus.COMPLETED, result="Actual follow-up observation")

    executor = Executor()
    router = build_default_action_router(delegate_executor=executor)
    first = router.execute(SPAction.create(ActionType.DELEGATE, action_id="one", target_agent="coder", task="Inspect current source observations", reason="verify actual result", stage="verification"), state={}, thread_id="t", run_id="r")
    entries = TaskMemoryStack.from_dict(first.state_update["sp_task_memory"]).entries
    obs = next(e for e in reversed(entries) if e.action == "observe")
    assert bool(obs.metadata.get("tool_execution_contract")) is trusted
    followup = SPAction.create(ActionType.DELEGATE, action_id="two", target_agent="researcher", task="Read the supplied observation file", reason="verify current data", stage="research")
    router.execute(followup, state=first.state_update, thread_id="t", run_id="r")
    assert len(executor.tasks) == 2
    actual = executor.tasks[-1]
    if trusted:
        assert actual.subagent_type == "researcher"
        assert actual.task == "Read the supplied observation file"
        assert actual.metadata["stage"] == "research"
        assert "bash" not in (actual.metadata.get("tool_names") or [])
    else:
        assert actual.subagent_type == "coder"
        assert actual.metadata["stage"] == ("implementation" if gap == "No non-test source file was changed" else "verification")
        assert "bash" in actual.metadata["tool_names"]
