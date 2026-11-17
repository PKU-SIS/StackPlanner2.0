"""JSON-mode beta SDK events must preserve real terminal observations."""

import asyncio
import json

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage
from test_collusion_reporter_v25 import model


def sse(delta, finish=None, usage=None):
    value = {"id": "provider-test", "object": "chat.completion.chunk", "created": 1, "model": "qwen3.8-27b", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], "usage": usage}
    return "data: " + json.dumps(value) + "\n\n"


@pytest.mark.parametrize("nested", [False, True])
def test_real_terminal_fields_survive_both_sdk_event_shapes(tmp_path, nested):
    m, client, context, value = model(tmp_path, b"")
    raw = {"id": "actual-provider-id", "model": "qwen3.8-27b", "choices": [{"index": 0, "delta": {"content": "{}"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 17, "completion_tokens": 2, "total_tokens": 19}}
    chunk = {"type": "chunk", "chunk": raw, "snapshot": {}} if nested else raw
    converted = m._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, {})
    observed = converted.message.additional_kwargs["_delivery_observation"]
    assert observed["provider_request_id"] == "actual-provider-id"
    assert observed["finish_reason"] == "stop"
    assert observed["usage"] == raw["usage"]
    asyncio.run(client.aclose())


def test_real_sdk_json_mode_stream_finishes_without_repairing_native_content(tmp_path):
    text = '{"summary":"已写报告","artifact_type":"report","artifact_content":"","artifact_metadata":{"completion_status":"complete"}}'
    chunks = (sse({"role": "assistant"}) + sse({"content": text[:20]}) + sse({"content": text[20:]}) + sse({}, "stop", {"prompt_tokens": 19, "completion_tokens": 31, "total_tokens": 50}) + "data: [DONE]\n\n").encode()
    m, client, context, value = model(tmp_path, chunks)

    async def run():
        token = context.set(value)
        try:
            result = await m.bind(response_format={"type": "json_object"}).ainvoke([HumanMessage("Return native JSON only")])
            assert result.content == text
            assert result.tool_calls == []
        finally:
            context.reset(token)
            await client.aclose()

    asyncio.run(run())
    statuses = [json.loads(p.read_text()) for p in tmp_path.rglob("*.status.json")]
    assert len(statuses) == 1
    assert statuses[0]["status"] == "completed" and statuses[0]["finish_reason"] == "stop"
    assert statuses[0]["provider_request_id"] == "provider-test"
    assert statuses[0]["usage"]["total_tokens"] == 50


def test_json_mode_complete_looking_text_without_terminal_reason_still_fails(tmp_path):
    from app.experiments.collusion_audit.reporter_stream import IncompleteReporterStream

    text = '{"summary":"looks complete"}'
    chunks = (sse({"role": "assistant"}) + sse({"content": text}) + "data: [DONE]\n\n").encode()
    m, client, context, value = model(tmp_path, chunks)

    async def run():
        token = context.set(value)
        try:
            with pytest.raises(IncompleteReporterStream):
                await m.bind(response_format={"type": "json_object"}).ainvoke([HumanMessage("Return JSON only")])
        finally:
            context.reset(token)
            await client.aclose()

    asyncio.run(run())
    assert [p.read_text() for p in tmp_path.rglob("*.content.partial.txt")] == [text]
