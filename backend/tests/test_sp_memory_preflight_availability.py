"""Memory-preflight guidance follows the native request's actual tool set."""

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from deerflow.sp.agent_tools import SPFinishAvailabilityMiddleware, build_sp_control_tools
from deerflow.sp.central.prompt import CENTRAL_AGENT_ACTION_PROMPT
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.prompt import PromptContextBuilder


class Request:
    def __init__(self, *, state, tools, messages):
        self.state, self.tools, self.messages = state, tools, messages

    def override(self, **updates):
        return Request(**{**vars(self), **updates})


@pytest.mark.parametrize("public_audit", [False, True])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_actual_next_request_keeps_normal_preflight_but_prioritizes_public_audit(public_audit, asynchronous):
    stack = TaskMemoryStack()
    state = {"sp_new_conversation": True, "sp_non_interactive": True, "sp_task_memory": stack.to_dict(), "sp_loop_run_id": "run"}
    if public_audit:
        state["sp_task_contract"] = {
            "delivery_mode": "artifact",
            "source_requirements": {"audit_agent": "reporter", "files": [{"path": "/mnt/user-data/uploads/document.txt", "total_lines": 1}], "document_ids": []},
        }
    context = PromptContextBuilder().build(stack, new_conversation=True, current_run_id="run", task_contract=state.get("sp_task_contract"))
    messages = [SystemMessage(content=CENTRAL_AGENT_ACTION_PROMPT), HumanMessage(content="Inspect the current task."), HumanMessage(content=context)]
    state["messages"] = messages
    original_tools = build_sp_control_tools()
    original_names = {tool.name for tool in original_tools}
    seen = []

    def model_boundary(request):
        names = {tool.name for tool in request.tools}
        seen.append(request)
        action = "sp_delegate" if public_audit else "sp_recall_memory"
        assert action in names
        return AIMessage(content="", tool_calls=[{"name": action, "id": "decision", "args": {}}])

    middleware = SPFinishAvailabilityMiddleware()
    request = Request(state=state, tools=original_tools, messages=messages)
    if asynchronous:

        async def handler(current):
            return model_boundary(current)

        result = asyncio.run(middleware.awrap_model_call(request, handler))
    else:
        result = middleware.wrap_model_call(request, model_boundary)

    actual = seen[0]
    names = {tool.name for tool in actual.tools}
    # Exercise the actual model-request wrapper, not just prompt constants:
    # the fix must neither expose a new tool nor relax the existing audit gate.
    assert {tool.name for tool in original_tools} == original_names
    assert names <= original_names
    assert result.tool_calls[0]["name"] in names
    assert state["sp_task_memory"] == stack.to_dict()
    preflight_rule = CENTRAL_AGENT_ACTION_PROMPT.split("Rules:", 1)[1].split("- Make every SP action", 1)[0]
    assert "first CentralAgent decision must be exactly one `sp_recall_memory` preflight" in preflight_rule
    assert "currently bound" in preflight_rule
    assert "public-source audit" in preflight_rule
    assert "currently bound" in context
    if public_audit:
        assert names == {"sp_delegate"}
        assert result.tool_calls[0]["name"] == "sp_delegate"
        note = actual.messages[-1]
        assert note.name == "sp_public_source_audit_required"
        assert "Memory preflight is deferred" in note.content
        assert "sp_recall_memory" in note.content
    else:
        assert "sp_recall_memory" in names
        assert result.tool_calls[0]["name"] == "sp_recall_memory"
        assert not any(message.name == "sp_public_source_audit_required" for message in actual.messages)


def test_tool_registry_keeps_the_existing_memory_action_name():
    names = [tool.name for tool in build_sp_control_tools()]
    assert names.count("sp_recall_memory") == 1
    assert len(names) == len(set(names))
