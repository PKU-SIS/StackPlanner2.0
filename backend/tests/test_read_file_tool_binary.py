"""read_file tool behaviour on binary files.

``read_file`` decodes with UTF-8. Binary uploads (``.xlsx``, images, ...) raise
``UnicodeDecodeError`` deep in the sandbox layer, which previously surfaced to
the model as a vague ``Unexpected error reading file`` message. The model could
not tell that the file was binary, so it retried ``read_file`` instead of
switching to ``bash`` + pandas/openpyxl — burning LLM round-trips. These tests
pin the actionable error contract and guard the normal text path.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from deerflow.sandbox.local.local_sandbox import LocalSandbox
from deerflow.sandbox.tools import read_file_tool


def _local_runtime(tmp_path: Path) -> SimpleNamespace:
    for sub in ("workspace", "uploads", "outputs"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    thread_data = {
        "workspace_path": str(tmp_path / "workspace"),
        "uploads_path": str(tmp_path / "uploads"),
        "outputs_path": str(tmp_path / "outputs"),
    }
    return SimpleNamespace(
        state={"sandbox": {"sandbox_id": "local:t1"}, "thread_data": thread_data},
        context={"thread_id": "t1"},
    )


def test_read_file_tool_binary_file_returns_actionable_hint(tmp_path, monkeypatch) -> None:
    runtime = _local_runtime(tmp_path)
    # .xlsx is a zip container: header bytes PK\x03\x04 plus a non-UTF-8 byte 0x82
    # that makes strict UTF-8 decoding fail (the exact byte seen in the field logs).
    (tmp_path / "uploads" / "data.xlsx").write_bytes(b"PK\x03\x04\x14\x00\x00\x00\x08\x00\x82\x6a\xb1\x55")
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_sandbox_initialized", lambda runtime: LocalSandbox("t1"))
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_thread_directories_exist", lambda runtime: None)

    result = read_file_tool.func(
        runtime=runtime,
        description="read uploaded excel",
        path="/mnt/user-data/uploads/data.xlsx",
    )

    assert "Unexpected error" not in result, result
    assert "binary" in result.lower(), result
    # The model must be steered to bash + pandas/openpyxl, not another read_file.
    assert "bash" in result.lower(), result


def test_read_file_tool_text_file_unaffected(tmp_path, monkeypatch) -> None:
    runtime = _local_runtime(tmp_path)
    (tmp_path / "uploads" / "notes.txt").write_text("hello 你好\nsecond line", encoding="utf-8")
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_sandbox_initialized", lambda runtime: LocalSandbox("t1"))
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_thread_directories_exist", lambda runtime: None)

    result = read_file_tool.func(
        runtime=runtime,
        description="read notes",
        path="/mnt/user-data/uploads/notes.txt",
    )

    assert "hello 你好" in result, result
    assert "binary" not in result.lower(), result


def test_read_file_line_numbers_are_explicit_and_default_stays_unchanged(tmp_path, monkeypatch) -> None:
    runtime = _local_runtime(tmp_path)
    (tmp_path / "uploads" / "notes.txt").write_text("alpha\nbeta\ngamma", encoding="utf-8")
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_sandbox_initialized", lambda runtime: LocalSandbox("t1"))
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_thread_directories_exist", lambda runtime: None)

    default = read_file_tool.func(
        runtime=runtime,
        description="read default",
        path="/mnt/user-data/uploads/notes.txt",
        start_line=2,
        end_line=3,
    )
    numbered = read_file_tool.func(
        runtime=runtime,
        description="read numbered evidence",
        path="/mnt/user-data/uploads/notes.txt",
        start_line=2,
        end_line=3,
        line_numbers=True,
    )

    assert default == "beta\ngamma"
    assert numbered == "2: beta\n3: gamma"

    start_only = read_file_tool.func(
        runtime=runtime,
        description="read from start",
        path="/mnt/user-data/uploads/notes.txt",
        start_line=2,
    )
    end_only = read_file_tool.func(
        runtime=runtime,
        description="read through end",
        path="/mnt/user-data/uploads/notes.txt",
        end_line=2,
    )
    numbered_start_only = read_file_tool.func(
        runtime=runtime,
        description="read numbered tail",
        path="/mnt/user-data/uploads/notes.txt",
        start_line=2,
        line_numbers=True,
    )

    assert start_only == "beta\ngamma"
    assert end_only == "alpha\nbeta"
    assert numbered_start_only == "2: beta\n3: gamma"


def test_read_file_line_numbers_match_async_path(tmp_path, monkeypatch) -> None:
    runtime = _local_runtime(tmp_path)
    (tmp_path / "uploads" / "notes.txt").write_text("alpha\nbeta", encoding="utf-8")
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_sandbox_initialized", lambda runtime: LocalSandbox("t1"))
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_thread_directories_exist", lambda runtime: None)
    async def _direct_async(func, runtime, *args):
        return func(runtime, *args)

    monkeypatch.setattr(
        "deerflow.sandbox.tools._run_sync_tool_after_async_sandbox_init",
        _direct_async,
    )

    result = asyncio.run(
        read_file_tool.coroutine(
            runtime=runtime,
            description="read numbered evidence async",
            path="/mnt/user-data/uploads/notes.txt",
            start_line=1,
            end_line=2,
            line_numbers=True,
        )
    )

    assert result == "1: alpha\n2: beta"

    async_start_only = asyncio.run(
        read_file_tool.coroutine(
            runtime=runtime,
            description="async tail",
            path="/mnt/user-data/uploads/notes.txt",
            start_line=2,
        )
    )
    async_end_only = asyncio.run(
        read_file_tool.coroutine(
            runtime=runtime,
            description="async head",
            path="/mnt/user-data/uploads/notes.txt",
            end_line=1,
        )
    )
    assert async_start_only == "beta"
    assert async_end_only == "alpha"
