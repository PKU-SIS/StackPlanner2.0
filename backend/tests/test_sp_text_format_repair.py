"""Text-mode correction describes text rather than its JSON serialization."""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.sp.agent_tools import SPFinishAvailabilityMiddleware, SPThinkLabelMiddleware
from deerflow.sp.memory import TaskMemoryStack


def _state(schema, mode):
    stack = TaskMemoryStack()
    stack.append_delegate("Audit original public sources", run_id="run", metadata={"target_agent": "researcher"})
    stack.append_observe("Audit complete", actor="researcher", run_id="run", metadata={"completion_status": "complete", "evidence_gaps": []})
    return {
        "sp_loop_run_id": "run",
        "sp_non_interactive": True,
        "sp_task_memory": stack.to_dict(),
        "sp_task_contract": {"delivery_mode": "inline", "output_parse_mode": mode, "output_schema": schema, "source_requirements": {"audit_agent": "researcher", "files": []}},
        "messages": [HumanMessage(content="Use the complete public audit and return the value required by the original schema.")],
    }


@pytest.mark.parametrize(
    "mode,schema,first,final",
    [
        ("text", {"type": "string", "enum": ["Accepted", "Needs review"]}, json.dumps("Needs review"), "Needs review"),
        ("text", {"type": "string", "pattern": '^"A quoted phrase"$'}, "A quoted phrase", '"A quoted phrase"'),
        ("text", {"type": "string", "pattern": "^First line\\nSecond line$"}, "One line", "First line\nSecond line"),
        ("json", {"type": "string", "enum": ["Accepted", "Needs review"]}, "Needs review", json.dumps("Needs review")),
    ],
)
def test_native_correction_follows_parse_mode_without_host_changing_the_answer(mode, schema, first, final):
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from pydantic import PrivateAttr

    from deerflow.agents.thread_state import ThreadState

    class Model(FakeMessagesListChatModel):
        _seen: list = PrivateAttr(default_factory=list)

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self._seen.append(list(messages))
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    state = _state(schema, mode)
    original_memory = state["sp_task_memory"]
    model = Model(responses=[AIMessage(content=first), AIMessage(content=final)])
    graph = create_agent(model=model, tools=[], middleware=[SPFinishAvailabilityMiddleware(), SPThinkLabelMiddleware()], state_schema=ThreadState)
    result = graph.invoke(state)
    assert len(model._seen) == 2
    assert [message.content for message in result["messages"] if isinstance(message, AIMessage)] == [first, final]
    assert result["messages"][-1].content == final
    assert result["sp_task_memory"]["entries"][: len(original_memory["entries"])] == original_memory["entries"]
    note = model._seen[1][-1].content
    assert "JSON/text" not in note
    if mode == "text":
        assert "plain-text" in note and "Do not JSON-serialize" in note
        assert "literal quotation marks" in note and "line breaks" in note
        assert "describes allowed plain text" in note
        assert "single-layer JSON encoding" not in note
    else:
        assert "valid JSON value" in note
        assert "required serialization quotes" in note
        assert "Do not JSON-serialize" not in note
