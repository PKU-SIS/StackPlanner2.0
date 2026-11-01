"""Ranges must never silently re-read a truncated document prefix."""

import pytest
from test_read_file_tool_binary import _local_runtime

from deerflow.sandbox.local.local_sandbox import LocalSandbox
from deerflow.sandbox.tools import read_file_tool


@pytest.mark.parametrize(
    "start,end,expected",
    [
        (None, None, "first\nsecond\nthird\n"),
        (2, None, "second\nthird"),
        (None, 2, "first\nsecond"),
        (2, 2, "second"),
        (4, None, "(empty: requested range starts after end of file; total_lines=3)"),
    ],
)
def test_independent_inclusive_range_bounds(tmp_path, monkeypatch, start, end, expected):
    runtime = _local_runtime(tmp_path)
    (tmp_path / "uploads/document.txt").write_text("first\nsecond\nthird\n")
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_sandbox_initialized", lambda runtime: LocalSandbox("t1"))
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_thread_directories_exist", lambda runtime: None)
    result = read_file_tool.func(runtime, "Read remaining document", "/mnt/user-data/uploads/document.txt", start, end)
    assert result == expected


@pytest.mark.parametrize("start,end", [(0, None), (-1, 3), (None, 0), (3, 2)])
def test_invalid_ranges_are_actionable_errors(tmp_path, monkeypatch, start, end):
    runtime = _local_runtime(tmp_path)
    monkeypatch.setattr("deerflow.sandbox.tools.read_current_file_content", lambda *_: "first\nsecond\nthird")
    result = read_file_tool.func(runtime, "Read document", "/mnt/user-data/uploads/document.txt", start, end)
    assert result.startswith("Error: invalid line range")
