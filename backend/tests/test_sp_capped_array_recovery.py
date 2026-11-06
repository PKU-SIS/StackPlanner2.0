"""Capped artifact arrays get model-side reconstruction guidance, without repairs."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

from deerflow.sp.agent_tools import SPFinishAvailabilityMiddleware, build_sp_control_tools
from deerflow.sp.memory import TaskMemoryStack


def _state(*, delivery_mode="artifact", schema_type="array", stop_reason="token_capped", run_id="run", status="partial"):
    stack = TaskMemoryStack()
    stack.append_delegate("Audit", run_id=run_id, metadata={"target_agent": "reporter"})
    stack.append_observe("Unverified truncated draft", actor="reporter", run_id=run_id, metadata={"completion_status": status, "stop_reason": stop_reason, "evidence_gaps": ["Invalid incomplete JSON"]})
    return {
        "sp_loop_run_id": "run",
        "sp_non_interactive": True,
        "sp_task_memory": stack.to_dict(),
        "sp_task_contract": {"delivery_mode": delivery_mode, "output_parse_mode": "json", "output_schema": {"type": schema_type}, "source_requirements": {"audit_agent": "reporter", "files": []}},
        "messages": [HumanMessage(content="Audit original public sources.")],
    }


def test_capped_array_recovery_prompt_preserves_all_distinct_issues_and_complete_verbatim_evidence():
    from deerflow.sp.recovery import artifact_array_token_cap_instruction

    instruction = artifact_array_token_cap_instruction(_state())
    assert "each distinct supported issue exactly once" in instruction
    assert "complete verbatim original sentences or passages" in instruction
    assert "one to three sentences" in instruction
    assert "complete, closed, valid JSON array" in instruction
    assert "omit a distinct supported issue to save tokens" in instruction
    assert "partial" in instruction


@pytest.mark.parametrize("change", [{"delivery_mode": "inline"}, {"schema_type": "object"}, {"stop_reason": "loop_capped"}, {"stop_reason": None}, {"run_id": "old-run"}, {"status": "complete"}])
def test_capped_array_instruction_is_strictly_scoped(change):
    from deerflow.sp.recovery import artifact_array_token_cap_instruction

    assert artifact_array_token_cap_instruction(_state(**change)) == ""


def test_newer_reporter_result_does_not_inherit_stale_token_cap_guidance():
    from deerflow.sp.recovery import artifact_array_token_cap_instruction

    state = _state()
    stack = TaskMemoryStack.from_dict(state["sp_task_memory"])
    stack.append_observe("A new format gap", actor="reporter", run_id="run", metadata={"completion_status": "partial", "evidence_gaps": ["Malformed JSON"]})
    state["sp_task_memory"] = stack.to_dict()
    assert artifact_array_token_cap_instruction(state) == ""


def test_capped_array_guidance_reaches_both_central_recovery_prompts():
    state = _state()
    assert "each distinct supported issue exactly once" in SPFinishAvailabilityMiddleware._artifact_recovery_instruction(state)

    class Request:
        tools = build_sp_control_tools()
        messages = state["messages"]

        def override(self, **kwargs):
            return SimpleNamespace(**kwargs)

    filtered = SPFinishAvailabilityMiddleware()._public_source_audit_request(Request(), state)
    assert "each distinct supported issue exactly once" in filtered.messages[-1].content


@pytest.mark.parametrize("role,mode,kind,expected", [("reporter", "artifact", "array", True), ("reporter", "inline", "array", False), ("reporter", "artifact", "object", False), ("researcher", "artifact", "array", False)])
def test_capped_array_guidance_is_in_the_actual_next_reporter_task_even_if_central_omits_it(role, mode, kind, expected):
    from deerflow.sp.actions import SPAction
    from deerflow.sp.actions.handlers.base import HandlerContext
    from deerflow.sp.actions.handlers.delegate import DelegateHandler
    from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus

    state = _state(delivery_mode=mode, schema_type=kind)
    captured = []

    def execute(task):
        captured.append(task)
        return SPSubagentResult(status=SPSubagentStatus.FAILED, error="No service in unit test")

    action = SPAction.create("DELEGATE", target_agent=role, task="Preserve the previous draft and fix its JSON", reason="Recover", stage="revision" if role == "reporter" else "research")
    DelegateHandler(executor=SimpleNamespace(execute=execute)).handle(action, HandlerContext(state=state, stack=TaskMemoryStack.from_dict(state["sp_task_memory"]), thread_id="thread", run_id="run"))
    assert len(captured) == 1
    assert captured[0].task.startswith(action.task)
    assert ("each distinct supported issue exactly once" in captured[0].task) is expected
    if not expected:
        assert captured[0].task == action.task
    assert captured[0].budgets == {}
