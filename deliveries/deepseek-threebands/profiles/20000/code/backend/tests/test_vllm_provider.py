from __future__ import annotations

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from deerflow.models.vllm_provider import VllmChatModel


def _make_model() -> VllmChatModel:
    return VllmChatModel(
        model="Qwen/QwQ-32B",
        api_key="dummy",
        base_url="http://localhost:8000/v1",
    )


def test_vllm_provider_restores_reasoning_in_request_payload():
    model = _make_model()
    payload = model._get_request_payload(
        [
            AIMessage(
                content="",
                tool_calls=[{"name": "bash", "args": {"cmd": "pwd"}, "id": "tool-1", "type": "tool_call"}],
                additional_kwargs={"reasoning": "Need to inspect the workspace first."},
            ),
            HumanMessage(content="Continue"),
        ]
    )

    assistant_message = payload["messages"][0]
    assert assistant_message["role"] == "assistant"
    assert assistant_message["reasoning"] == "Need to inspect the workspace first."
    assert assistant_message["tool_calls"][0]["function"]["name"] == "bash"


def test_vllm_provider_normalizes_legacy_thinking_kwarg_to_enable_thinking():
    model = VllmChatModel(
        model="qwen3",
        api_key="dummy",
        base_url="http://localhost:8000/v1",
        extra_body={"chat_template_kwargs": {"thinking": True}},
    )

    payload = model._get_request_payload([HumanMessage(content="Hello")])

    assert payload["extra_body"]["chat_template_kwargs"] == {"enable_thinking": True}


def test_vllm_provider_preserves_explicit_enable_thinking_kwarg():
    model = VllmChatModel(
        model="qwen3",
        api_key="dummy",
        base_url="http://localhost:8000/v1",
        extra_body={"chat_template_kwargs": {"enable_thinking": False, "foo": "bar"}},
    )

    payload = model._get_request_payload([HumanMessage(content="Hello")])

    assert payload["extra_body"]["chat_template_kwargs"] == {
        "enable_thinking": False,
        "foo": "bar",
    }


def test_vllm_provider_preserves_reasoning_in_chat_result():
    model = _make_model()
    result = model._create_chat_result(
        {
            "model": "Qwen/QwQ-32B",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "42",
                        "reasoning": "I compared the two numbers directly.",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )

    message = result.generations[0].message
    assert message.additional_kwargs["reasoning"] == "I compared the two numbers directly."
    assert message.additional_kwargs["reasoning_content"] == "I compared the two numbers directly."


def test_vllm_provider_promotes_reasoning_only_answer_when_thinking_is_explicitly_disabled():
    model = VllmChatModel(
        model="Qwen3-32B",
        api_key="dummy",
        base_url="http://localhost:8000/v1",
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )

    result = model._create_chat_result(
        {
            "model": "Qwen3-32B",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "", "reasoning": "OK"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )

    message = result.generations[0].message
    assert message.content == "OK"
    assert "reasoning" not in message.additional_kwargs
    assert "reasoning_content" not in message.additional_kwargs
    assert message.additional_kwargs["reasoning_promoted_to_content"] is True


def test_vllm_provider_keeps_reasoning_only_output_hidden_when_thinking_is_enabled():
    model = VllmChatModel(
        model="Qwen3-32B",
        api_key="dummy",
        base_url="http://localhost:8000/v1",
        extra_body={"chat_template_kwargs": {"enable_thinking": True}},
    )

    result = model._create_chat_result(
        {
            "model": "Qwen3-32B",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "", "reasoning": "Internal thought"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )

    message = result.generations[0].message
    assert message.content == ""
    assert message.additional_kwargs["reasoning"] == "Internal thought"


def test_vllm_provider_recovers_qwen_json_tool_call_from_reasoning():
    model = _make_model()
    result = model._create_chat_result(
        {
            "model": "Qwen3-32B",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning": ('Need current evidence.\n<tool_call>{"name":"sp_delegate","arguments":{"target_agent":"researcher","task":"search prices"}}</tool_call>'),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )

    message = result.generations[0].message
    assert message.tool_calls == [
        {
            "name": "sp_delegate",
            "args": {"target_agent": "researcher", "task": "search prices"},
            "id": message.tool_calls[0]["id"],
            "type": "tool_call",
        }
    ]
    assert message.additional_kwargs["reasoning"] == "Need current evidence."
    assert message.additional_kwargs["reasoning_content"] == "Need current evidence."


def test_vllm_provider_recovers_qwen_json_tool_call_from_reasoning_content():
    """Qwen vLLM may put text-protocol tool calls in reasoning_content."""
    model = _make_model()
    result = model._create_chat_result(
        {
            "model": "Qwen3-32B",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "reasoning_content": ('<tool_call>{"name":"sp_delegate","arguments":{"target_agent":"coder","task":"inspect files"}}</tool_call>'),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )

    message = result.generations[0].message
    assert message.tool_calls[0]["name"] == "sp_delegate"
    assert message.tool_calls[0]["args"] == {"target_agent": "coder", "task": "inspect files"}
    assert message.additional_kwargs["reasoning_content"] == ""


def test_vllm_provider_recovers_multiple_qwen_tool_calls_from_content():
    model = _make_model()
    result = model._create_chat_result(
        {
            "model": "Qwen3-32B",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": (
                            '<tool_call>{"name":"sp_delegate","arguments":{"target_agent":"researcher","task":"A"}}</tool_call>'
                            '<tool_call>{"name":"sp_delegate","arguments":"{\\"target_agent\\":\\"researcher\\",\\"task\\":\\"B\\"}"}</tool_call>'
                        ),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )

    message = result.generations[0].message
    assert message.content == ""
    assert [call["args"]["task"] for call in message.tool_calls] == ["A", "B"]


def test_vllm_provider_preserves_reasoning_in_streaming_chunks():
    model = _make_model()
    chunk = model._convert_chunk_to_generation_chunk(
        {
            "model": "Qwen/QwQ-32B",
            "choices": [
                {
                    "delta": {
                        "role": "assistant",
                        "reasoning": "First, call the weather tool.",
                        "content": "Calling tool...",
                    },
                    "finish_reason": None,
                }
            ],
        },
        AIMessageChunk,
        {},
    )

    assert chunk is not None
    assert chunk.message.additional_kwargs["reasoning"] == "First, call the weather tool."
    assert chunk.message.additional_kwargs["reasoning_content"] == "First, call the weather tool."
    assert chunk.message.content == "Calling tool..."


def test_vllm_provider_preserves_empty_reasoning_values_in_streaming_chunks():
    model = _make_model()
    chunk = model._convert_chunk_to_generation_chunk(
        {
            "model": "Qwen/QwQ-32B",
            "choices": [
                {
                    "delta": {
                        "role": "assistant",
                        "reasoning": "",
                        "content": "Still replying...",
                    },
                    "finish_reason": None,
                }
            ],
        },
        AIMessageChunk,
        {},
    )

    assert chunk is not None
    assert "reasoning" in chunk.message.additional_kwargs
    assert chunk.message.additional_kwargs["reasoning"] == ""
    assert "reasoning_content" not in chunk.message.additional_kwargs
    assert chunk.message.content == "Still replying..."
