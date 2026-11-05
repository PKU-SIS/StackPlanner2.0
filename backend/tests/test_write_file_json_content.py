"""Structured JSON uses the normal file tool and preserves values verbatim."""

import asyncio
import json

import pytest
from pydantic import ValidationError
from test_read_file_tool_binary import _local_runtime

from deerflow.sandbox import tools as tools_module
from deerflow.sandbox.local.local_sandbox import LocalSandbox
from deerflow.sandbox.tools import write_file_tool


@pytest.fixture
def local_json_runtime(tmp_path, monkeypatch):
    runtime = _local_runtime(tmp_path)
    sandbox = LocalSandbox("t1")
    monkeypatch.setattr(tools_module, "ensure_sandbox_initialized", lambda runtime: sandbox)
    monkeypatch.setattr(tools_module, "ensure_thread_directories_exist", lambda runtime: None)

    async def ensure_async(runtime):
        return sandbox

    monkeypatch.setattr(tools_module, "ensure_sandbox_initialized_async", ensure_async)
    return runtime


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("as_object", [False, True])
def test_real_write_tool_schema_serializes_complex_json_values(tmp_path, local_json_runtime, mode, as_object):
    value = [{"text": 'Quoted "clause"\nsecond line\\path\\end', "explanation": "中文 café\tvalue", "nested": {"items": [1, True, None, {"backslash": "\\", "quotes": '"'}]}}]
    if as_object:
        value = {"findings": value, "other_field": "preserve me"}
    payload = {"description": "Write structured JSON", "path": "/mnt/user-data/outputs/report.json", "json_content": value}
    validated = write_file_tool.tool_call_schema.model_validate_json(json.dumps(payload)).model_dump(exclude_unset=True)
    assert "content" not in validated
    assert "json_content" in write_file_tool.tool_call_schema.model_json_schema()["properties"]
    if mode == "sync":
        result = write_file_tool.func(runtime=local_json_runtime, **validated)
    else:
        result = asyncio.run(write_file_tool.coroutine(runtime=local_json_runtime, **validated))
    assert result == "OK"
    saved = (tmp_path / "outputs/report.json").read_text()
    assert json.loads(saved) == value
    assert "中文" in saved


@pytest.mark.parametrize(
    "kwargs",
    [
        {"content": "[]", "json_content": []},
        {"content": "", "json_content": {}},
        {},
        {"json_content": [], "append": True},
        {"json_content": [], "path": "/mnt/user-data/outputs/report.jsonl"},
        {"json_content": [], "path": "/mnt/user-data/outputs/report.md"},
        {"json_content": {"bad_number": float("nan")}},
    ],
)
def test_structured_json_rejects_ambiguous_or_inapplicable_requests_before_writing(tmp_path, local_json_runtime, kwargs):
    arguments = {"runtime": local_json_runtime, "description": "Write JSON", "path": "/mnt/user-data/outputs/report.json", **kwargs}
    result = write_file_tool.func(**arguments)
    assert result.startswith("Error:")
    assert list((tmp_path / "outputs").iterdir()) == []


def test_structured_json_schema_rejects_string_content():
    with pytest.raises(ValidationError):
        write_file_tool.tool_call_schema.model_validate({"description": "Write JSON", "path": "/mnt/user-data/outputs/report.json", "json_content": "[]"})


def test_structured_json_retains_existing_size_limit(tmp_path, local_json_runtime):
    result = write_file_tool.func(runtime=local_json_runtime, description="Write large JSON", path="/mnt/user-data/outputs/report.json", json_content=[{"text": "x" * (90 * 1024)}])
    assert result.startswith("Error: write_file content")
    assert not (tmp_path / "outputs/report.json").exists()


def test_legacy_empty_string_content_remains_supported(tmp_path, local_json_runtime):
    result = write_file_tool.func(local_json_runtime, "Write empty text", "/mnt/user-data/outputs/report.txt", "", False)
    assert result == "OK"
    assert (tmp_path / "outputs/report.txt").read_bytes() == b""
