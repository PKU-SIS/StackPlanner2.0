"""A rejected FINISH exposes actual control errors without selecting or repairing outputs."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.sp.actions import SPAction
from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.handlers.finish import FinishHandler
from deerflow.sp.agent_tools import SPFinishAvailabilityMiddleware, build_sp_control_tools
from deerflow.sp.artifacts import SPArtifactAdapter
from deerflow.sp.memory import TaskMemoryStack


class Request:
    def __init__(self, *, state, messages, tools=None):
        self.state = state
        self.messages = messages
        self.tools = build_sp_control_tools() if tools is None else tools

    def override(self, **changes):
        return Request(state=self.state, messages=changes.get("messages", self.messages), tools=changes.get("tools", self.tools))


@pytest.mark.parametrize("bad_payload", [False, True])
def test_real_finish_rejection_feedback_carries_error_and_real_ref_without_bypassing_native_checks(tmp_path, bad_payload):
    state = {"sp_loop_run_id": "run", "thread_data": {"outputs_path": str(tmp_path)}, "sp_task_contract": {"delivery_mode": "artifact", "output_schema": {"type": "array"}}}
    artifact = SPArtifactAdapter().write_text_artifact("[]", artifact_type="report_revision", state=state, run_id="run", metadata={"completion_status": "complete"})
    state.update(artifact.state_update)
    context = HandlerContext(state=state, stack=TaskMemoryStack(), run_id="run")
    rejected = SPAction.create("FINISH", task="Deliver the actual report", reason="Deliver", metadata={"required_artifact_type": "report", "final_artifact_ref": "invented-reference"})
    result = FinishHandler().handle(rejected, context)
    assert result.next_step == "error_recoverable"
    state["sp_last_handler_result"] = {"action_type": "FINISH", "next_step": result.next_step, "error": result.error}
    messages = [HumanMessage(content="Deliver the public JSON report."), AIMessage(content="", tool_calls=[{"id": "rejected", "name": "sp_finish", "args": {"final_artifact_ref": "invented-reference", "required_artifact_type": "report"}}])]
    request = Request(state=state, messages=messages)
    filtered = SPFinishAvailabilityMiddleware()._filter_request(request)
    note = next(message for message in filtered.messages if message.name == "sp_rejected_finish_control")
    assert result.error in note.content
    assert artifact.metadata.artifact_id in note.content
    assert "invented-reference" in note.content
    assert "underlying work" in note.content
    assert "sp_delegate" in [tool.name for tool in filtered.tools]
    assert request.messages == messages
    assert all(not isinstance(message, AIMessage) or message.tool_calls == messages[1].tool_calls for message in filtered.messages)

    if bad_payload:
        from pathlib import Path

        Path(artifact.metadata.workspace_path).write_text("[invalid original draft")
    corrected = SPAction.create("FINISH", task="Deliver the actual report", reason="Deliver", metadata={"required_artifact_type": "report", "final_artifact_ref": artifact.metadata.artifact_id})
    outcome = FinishHandler().handle(corrected, context)
    assert outcome.next_step == ("error_recoverable" if bad_payload else "finish")
    if bad_payload:
        assert "valid JSON" in outcome.error


@pytest.mark.parametrize("action_type,next_step", [("FINISH", "finish"), ("DELEGATE", "error_recoverable"), ("THINK", "continue")])
def test_finish_control_feedback_requires_an_actual_rejected_finish(action_type, next_step):
    state = {"sp_last_handler_result": {"action_type": action_type, "next_step": next_step}, "messages": [HumanMessage(content="Hello")]}
    filtered = SPFinishAvailabilityMiddleware()._filter_request(Request(state=state, messages=state["messages"]))
    assert not any(message.name == "sp_rejected_finish_control" for message in filtered.messages)
