from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI

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


_WRITE_TOOL = {
    "type": "function",
    "function": {"name": "write_file", "description": "Write a file.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
}
_BAD_ENVELOPE = '<tool_call>{name: "write_file", arguments: {}}</tool_call>'


def _protocol_result(message: AIMessage, tokens: int = 2) -> ChatResult:
    message.usage_metadata = {"input_tokens": tokens, "output_tokens": 1, "total_tokens": tokens + 1, "output_token_details": {"reasoning": 1}}
    return ChatResult(generations=[ChatGeneration(message=message)], llm_output={"token_usage": {"prompt_tokens": tokens, "completion_tokens": 1, "total_tokens": tokens + 1, "completion_tokens_details": {"reasoning_tokens": 1}}})


@pytest.mark.parametrize("mode", ["sync", "async", "stream", "astream"])
def test_vllm_provider_reasks_once_with_original_tools_and_combines_usage(monkeypatch, mode):
    model = _make_model()
    messages = [HumanMessage(content="Write the requested file.")]
    calls = []
    replies = [
        _protocol_result(AIMessage(content=_BAD_ENVELOPE)),
        _protocol_result(AIMessage(content="", tool_calls=[{"name": "write_file", "args": {"path": "answer.json"}, "id": "call-1", "type": "tool_call"}]), tokens=3),
    ]

    def generate(_self, supplied_messages, stop=None, run_manager=None, **kwargs):
        calls.append((supplied_messages, stop, kwargs))
        return replies[len(calls) - 1]

    async def agenerate(*args, **kwargs):
        return generate(*args, **kwargs)

    monkeypatch.setattr(ChatOpenAI, "_generate", generate)
    monkeypatch.setattr(ChatOpenAI, "_agenerate", agenerate)
    if mode == "sync":
        result = model._generate(messages, tools=[_WRITE_TOOL], stop=["END"], max_tokens=123)
        message = result.generations[0].message
        assert result.llm_output["token_usage"] == {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7, "completion_tokens_details": {"reasoning_tokens": 2}}
    elif mode == "async":
        result = asyncio.run(model._agenerate(messages, tools=[_WRITE_TOOL], stop=["END"], max_tokens=123))
        message = result.generations[0].message
        assert result.llm_output["token_usage"]["total_tokens"] == 7
    elif mode == "stream":
        message = list(model.bind_tools([_WRITE_TOOL]).stream(messages, stop=["END"], max_tokens=123))[0]
    else:

        async def collect():
            return [chunk async for chunk in model.bind_tools([_WRITE_TOOL]).astream(messages, stop=["END"], max_tokens=123)]

        message = asyncio.run(collect())[0]
    assert len(calls) == 2
    assert calls[0][0] == messages
    assert calls[1][0][:-1] == messages
    assert isinstance(calls[1][0][-1], HumanMessage)
    assert _BAD_ENVELOPE not in calls[1][0][-1].content
    assert calls[0][1:] == calls[1][1:]
    assert calls[1][2]["tools"][0]["function"]["name"] == "write_file"
    assert calls[1][2]["max_tokens"] == 123
    assert messages == [HumanMessage(content="Write the requested file.")]
    assert message.tool_calls[0]["name"] == "write_file"
    assert message.usage_metadata == {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7, "output_token_details": {"reasoning": 2}}
    assert message.additional_kwargs["tool_protocol_recovery_attempts"] == 1
    assert not message.additional_kwargs.get("tool_protocol_recovery_exhausted")


@pytest.mark.parametrize(
    "message,tools",
    [
        (AIMessage(content='[{"text":"literal <tool_call>","explanation":"ordinary JSON"}]'), [_WRITE_TOOL]),
        (AIMessage(content="Use <tool_call> only for tools."), [_WRITE_TOOL]),
        (AIMessage(content="", tool_calls=[{"name": "write_file", "args": {}, "id": "call-1", "type": "tool_call"}]), [_WRITE_TOOL]),
        (AIMessage(content=_BAD_ENVELOPE), []),
    ],
)
def test_vllm_provider_does_not_retry_ordinary_content_or_native_calls(monkeypatch, message, tools):
    calls = []

    def generate(*args, **kwargs):
        calls.append(kwargs)
        return _protocol_result(message)

    monkeypatch.setattr(ChatOpenAI, "_generate", generate)
    result = _make_model()._generate([HumanMessage(content="Do the task.")], tools=tools)
    assert len(calls) == 1
    assert result.generations[0].message is message
    assert "tool_protocol_recovery_attempts" not in message.additional_kwargs


@pytest.mark.parametrize("invalid_native", [False, True])
def test_vllm_provider_exhausts_after_one_correction_without_executing_bad_text(monkeypatch, invalid_native):
    calls = []

    def generate(*args, **kwargs):
        calls.append(kwargs)
        message = AIMessage(content=_BAD_ENVELOPE)
        if invalid_native:
            message = AIMessage(content="", invalid_tool_calls=[{"name": "write_file", "args": "{bad", "id": "bad-1", "error": "invalid JSON", "type": "invalid_tool_call"}])
        return _protocol_result(message)

    monkeypatch.setattr(ChatOpenAI, "_generate", generate)
    result = _make_model()._generate([HumanMessage(content="Do the task.")], tools=[_WRITE_TOOL])
    message = result.generations[0].message
    assert len(calls) == 2
    assert not message.tool_calls
    assert message.additional_kwargs["tool_protocol_recovery_attempts"] == 1
    assert message.additional_kwargs["tool_protocol_recovery_exhausted"] is True
    assert message.usage_metadata["total_tokens"] == 6


def test_vllm_provider_keeps_known_usage_details_when_retry_omits_them(monkeypatch):
    first = _protocol_result(AIMessage(content=_BAD_ENVELOPE))
    second = _protocol_result(AIMessage(content="ordinary final content"))
    second.llm_output["token_usage"]["completion_tokens_details"] = None
    replies = iter([first, second])
    monkeypatch.setattr(ChatOpenAI, "_generate", lambda *args, **kwargs: next(replies))
    result = _make_model()._generate([HumanMessage(content="Do the task.")], tools=[_WRITE_TOOL])
    assert result.llm_output["token_usage"]["completion_tokens_details"] == {"reasoning_tokens": 1}
