"""A trusted tool schema determines repeated-call identity, not tool-name guesses."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from deerflow.agents.middlewares.loop_detection_middleware import LoopDetectionMiddleware, _hash_tool_calls
from deerflow.sp.extensions import SPExecutionExtensions

FIELDS = {"read_file": ("file_path", "offset", "limit"), "project_search": ("query", "document_id", "offset", "limit")}


def call(**args):
    return [{"name": "read_file", "args": args, "id": "a", "type": "tool_call"}]


def test_default_key_behavior_and_native_schema_remain_unchanged():
    assert _hash_tool_calls(call(file_path="a", offset=0)) == _hash_tool_calls(call(file_path="b", offset=100))
    assert _hash_tool_calls(call(path="a", start_line=1, end_line=200)) != _hash_tool_calls(call(path="a", start_line=201, end_line=400))


def test_registered_character_windows_and_different_documents_have_distinct_keys():
    keys = {_hash_tool_calls(call(file_path=p, offset=o, limit=5000), tool_key_fields=FIELDS) for p in ["a", "b"] for o in [0, 5000, 10000]}
    assert len(keys) == 6


def test_repeated_identical_window_still_hits_original_hard_limit():
    guard = LoopDetectionMiddleware(tool_key_fields=FIELDS)
    runtime = SimpleNamespace(context={"thread_id": "t", "run_id": "r"})
    for i in range(guard.hard_limit):
        warning, stop = guard._track_and_check({"messages": [AIMessage(content="", tool_calls=call(file_path="a", offset=0, limit=5000))]}, runtime)
        assert stop is (i == guard.hard_limit - 1)


def test_legal_forward_windows_do_not_count_as_repeat():
    guard = LoopDetectionMiddleware(tool_key_fields=FIELDS)
    runtime = SimpleNamespace(context={"thread_id": "t", "run_id": "r"})
    for offset in range(0, 30000, 5000):
        assert guard._track_and_check({"messages": [AIMessage(content="", tool_calls=call(file_path="a", offset=offset, limit=5000))]}, runtime) == (None, False)


def test_opt_in_cannot_be_enabled_by_model_context():
    assert SPExecutionExtensions().tool_call_key_fields is None
    assert SPExecutionExtensions(tool_call_key_fields=FIELDS).tool_call_key_fields == FIELDS


@pytest.mark.parametrize("bad", [{"read_file": []}, {"": ("offset",)}, {"read_file": ("",)}, {"read_file": "offset"}])
def test_tool_identity_contract_is_typed(bad):
    with pytest.raises(ValueError):
        SPExecutionExtensions(tool_call_key_fields=bad)


@pytest.mark.parametrize("enabled", [False, True])
def test_actual_sp_executor_gets_identity_mapping_only_from_trusted_extension(monkeypatch, enabled):
    from deerflow.sp.runtime import DR2SPExecutorProvider
    from deerflow.sp.subagents import SPSubagentTask
    from deerflow.subagents.config import SubagentConfig

    captured = {}
    tool = SimpleNamespace(name="read_file")
    config = SubagentConfig(name="sp-researcher", description="research", internal=True)

    class Executor:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def execute(self, prompt):
            return SimpleNamespace(
                status="completed", result='{"summary":"done","artifact_type":"research_observation","artifact_content":"source","artifact_metadata":{"completion_status":"complete"}}', error=None, stop_reason=None, token_usage_records=[]
            )

    monkeypatch.setattr("deerflow.subagents.get_subagent_config", lambda *a, **kw: config)
    monkeypatch.setattr("deerflow.subagents.SubagentExecutor", Executor)
    ext = SPExecutionExtensions(tools=(tool,), tool_call_key_fields=FIELDS if enabled else None, image_tool_names=("view_image", "view_image_pair") if enabled else None)
    provider = DR2SPExecutorProvider(app_config=SimpleNamespace(), parent_model="m", runnable_config={"context": {"tool_call_key_fields": FIELDS}}, extensions=ext)
    provider({}, SimpleNamespace(context={"thread_id": "t", "run_id": "r", "tool_call_key_fields": FIELDS}, stream_writer=None)).execute(SPSubagentTask("a", "researcher", "read current facts", "read"))
    assert ("image_tool_names" in captured) is enabled
    if enabled:
        assert captured["image_tool_names"] == ("view_image", "view_image_pair")
    assert ("tool_call_key_fields" in captured) is enabled
    if enabled:
        assert captured["tool_call_key_fields"] == FIELDS
