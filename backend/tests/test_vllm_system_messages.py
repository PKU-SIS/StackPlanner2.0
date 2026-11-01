"""Qwen templates accept a single system message at the start of the request."""

from copy import deepcopy

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from deerflow.models.vllm_provider import VllmChatModel


def model():
    return VllmChatModel(model="qwen3.8-27b", api_key="dummy", base_url="http://localhost:8000/v1")


def test_system_reminders_merge_without_breaking_reasoning_or_tool_pairing():
    messages = [
        SystemMessage(content="Agent instructions."),
        SystemMessage(content="Current date."),
        HumanMessage(content="Review the document."),
        AIMessage(content="", tool_calls=[{"name": "read_file", "args": {"path": "/document.txt"}, "id": "call-1"}], additional_kwargs={"reasoning": "Read the evidence."}),
        ToolMessage(content="Contract text.", tool_call_id="call-1"),
        SystemMessage(content="Budget reminder."),
    ]
    original = deepcopy(messages)
    payload = model()._get_request_payload(messages)["messages"]
    assert [item["role"] for item in payload] == ["system", "user", "assistant", "tool"]
    assert payload[0]["content"] == "Agent instructions.\n\nCurrent date.\n\nBudget reminder."
    assert payload[2]["reasoning"] == "Read the evidence."
    assert payload[2]["tool_calls"][0]["id"] == payload[3]["tool_call_id"] == "call-1"
    assert messages == original


def test_single_system_after_user_moves_to_beginning():
    payload = model()._get_request_payload([HumanMessage(content="Question"), SystemMessage(content="Reminder")])["messages"]
    assert [item["role"] for item in payload] == ["system", "user"]
    assert payload[0]["content"] == "Reminder"


def test_mixed_system_text_blocks_are_preserved():
    messages = [SystemMessage(content="Instructions"), SystemMessage(content=[{"type": "text", "text": "Context"}]), HumanMessage(content="Question")]
    payload = model()._get_request_payload(messages)["messages"]
    assert [item["role"] for item in payload] == ["system", "user"]
    assert payload[0]["content"] == [{"type": "text", "text": "Instructions"}, {"type": "text", "text": "Context"}]


def test_already_compatible_payload_keeps_message_order():
    payload = model()._get_request_payload([SystemMessage(content="Instructions"), HumanMessage(content="Question"), AIMessage(content="Answer")])["messages"]
    assert [item["role"] for item in payload] == ["system", "user", "assistant"]
    assert [item["content"] for item in payload] == ["Instructions", "Question", "Answer"]
