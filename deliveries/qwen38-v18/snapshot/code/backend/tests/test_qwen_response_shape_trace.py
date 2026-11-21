"""Distinguish server-empty responses from local answer/tool conversion loss."""

import json
import sys
from pathlib import Path

import pytest

EXPERIMENT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(EXPERIMENT))

from qwen_adapter import QwenSPChatModel  # noqa: E402 - experiment adapter is outside the package roots.


@pytest.mark.parametrize(("content", "reasoning", "finish_reason", "parsed_chars", "parsed_calls"), [
    (None, "SYNTHETIC_PRIVATE_MARKER", "length", 0, 0),
    ("SYNTHETIC_PUBLIC_MARKER", None, "stop", 23, 0),
    ('<tool_call>{"name":"read_file","arguments":{"path":"SYNTHETIC_PATH"}}</tool_call>', None, "stop", 0, 1),
])
def test_shape_trace_preserves_results_without_recording_text(tmp_path, monkeypatch, content, reasoning, finish_reason, parsed_chars, parsed_calls):
    monkeypatch.setenv("QWEN_ADAPTER_TRACE_DIR", str(tmp_path))
    model = QwenSPChatModel(model="qwen-test", api_key="unused-local-test-key", base_url="http://127.0.0.1:1/v1")
    message = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    response = {
        "id": "synthetic-response", "object": "chat.completion", "created": 0, "model": "qwen-test",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }
    # Parse a synthetic response directly; no HTTP/model request is made.
    result = model._create_chat_result(response)
    log = (tmp_path / "transport.jsonl").read_text()
    assert all(marker not in log for marker in ("SYNTHETIC_PRIVATE_MARKER", "SYNTHETIC_PUBLIC_MARKER", "SYNTHETIC_PATH", "unused-local-test-key"))
    records = {row["kind"]: row for row in map(json.loads, log.splitlines()) if row["kind"] in {"provider_response_shape", "parsed_response_shape"}}
    raw, parsed = records["provider_response_shape"]["choices"][0], records["parsed_response_shape"]["choices"][0]
    assert raw["visible_text_chars"] == len(content or "")
    assert parsed["visible_text_chars"] == parsed_chars
    assert parsed["tool_call_count"] == parsed_calls
    assert raw["reasoning_field_present"] is (reasoning is not None)
    assert result.generations[0].message.usage_metadata["total_tokens"] == 30
    assert result.generations[0].generation_info["finish_reason"] == finish_reason
    if content == "SYNTHETIC_PUBLIC_MARKER":
        assert result.generations[0].message.content == content
