"""Cross-turn and causal-evidence regressions for the execution harness."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from deerflow.agents.middlewares.loop_detection_middleware import LoopDetectionMiddleware
from deerflow.sp.subagents.dr2_adapter import _execution_verification, _test_execution_verification


def _runtime(run_id):
    return SimpleNamespace(context={"thread_id": "same-thread", "run_id": run_id})


def _state(index):
    return {"messages": [AIMessage(content="", tool_calls=[{"id": str(index), "name": "bash", "args": {"command": f"echo {index}"}, "type": "tool_call"}])]}


def test_loop_frequency_does_not_carry_into_next_run():
    guard = LoopDetectionMiddleware(tool_freq_warn=2, tool_freq_hard_limit=3)
    first = _runtime("first")
    guard.before_agent({}, first)
    for index in range(2):
        assert guard.after_model(_state(index), first) is None
    guard.after_agent({}, first)
    second = _runtime("second")
    guard.before_agent({}, second)
    assert guard.after_model(_state(2), second) is None


@pytest.mark.parametrize("verify", [_execution_verification, _test_execution_verification])
def test_sibling_check_cannot_verify_parallel_source_edit(verify):
    messages = [
        {
            "type": "ai",
            "tool_calls": [
                {"id": "edit", "name": "write_file", "args": {"path": "app.py", "content": "fixed"}},
                {"id": "test", "name": "bash", "args": {"command": "pytest tests/test_core.py"}},
            ],
        },
        {"type": "tool", "name": "write_file", "tool_call_id": "edit", "content": "OK"},
        {"type": "tool", "name": "bash", "tool_call_id": "test", "content": "1 passed\nExit Code: 0"},
    ]
    assert not verify(messages)["passed"]


@pytest.mark.parametrize("command", ["pytest tests/test_core.py; true", "pytest tests/test_core.py || echo recovered", "pytest tests/test_core.py\ntrue", "pytest # ignored\ntrue", "pytest |& tail", "pytest &\n"])
def test_shell_suffix_cannot_mask_test_failure(command):
    messages = [
        {"type": "ai", "tool_calls": [{"id": "test", "name": "bash", "args": {"command": command}}]},
        {"type": "tool", "name": "bash", "tool_call_id": "test", "content": "Exit Code: 0"},
    ]
    assert not _test_execution_verification(messages)["passed"]


@pytest.mark.parametrize("command", ["cd /workspace\npytest tests/test_core.py", "cd /workspace # setup\npytest", "python -c 'assert 1 | 2 == 3'", "pytest &&\necho done", "python - <<'PY'\nassert 1 | 2 == 3\nPY"])
def test_real_multiline_or_bitwise_checks_are_recognized(command):
    messages = [
        {"type": "ai", "tool_calls": [{"id": "test", "name": "bash", "args": {"command": command}}]},
        {"type": "tool", "name": "bash", "tool_call_id": "test", "content": "Exit Code: 0"},
    ]
    assert _test_execution_verification(messages)["passed"]
