from types import SimpleNamespace as NS

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.middlewares.subagent_limit_middleware import SubagentLimitMiddleware
from deerflow.agents.middlewares.tool_call_budget_middleware import ToolCallBudgetMiddleware
from deerflow.agents.thread_state import merge_sp_counter, merge_sp_idempotency_ledger, sp_action_count
from deerflow.config.loop_detection_config import LoopDetectionConfig
from deerflow.config.stream_bridge_config import StreamBridgeConfig
from deerflow.config.subagents_config import SubagentOverrideConfig
from deerflow.config.summarization_config import ContextSize
from deerflow.models.factory import _apply_stream_chunk_timeout_default
from deerflow.sp.actions import ActionType, SPAction, build_default_action_router
from deerflow.sp.subagents import SPSubagentTask
from deerflow.sp.subagents.dr2_adapter import _implementation_verification, normalize_dr2_subagent_result


def test_vllm_explicit_stream_timeout_must_survive_factory():
    params = {"stream_chunk_timeout": 240.0}
    _apply_stream_chunk_timeout_default("deerflow.models.vllm_provider:VllmChatModel", params)
    assert params.get("stream_chunk_timeout") == 240.0, params


def test_serial_delegation_configuration_is_respected():
    assert SubagentLimitMiddleware(max_concurrent=1).max_concurrent == 1


@pytest.mark.parametrize("params", [{"type": "fraction", "value": 1.5}, {"type": "tokens", "value": -1}, {"type": "messages", "value": 2.5}])
def test_invalid_context_thresholds_rejected(params):
    with pytest.raises(ValueError):
        ContextSize(**params)


def test_unreachable_loop_threshold_rejected():
    with pytest.raises(ValueError):
        LoopDetectionConfig(hard_limit=5, window_size=2)


def test_zero_stream_buffer_rejected():
    with pytest.raises(ValueError):
        StreamBridgeConfig(queue_maxsize=0)


def test_misspelled_agent_override_is_not_silently_discarded():
    with pytest.raises(ValueError):
        SubagentOverrideConfig(max_truns=10)


def test_server_recursion_ceiling_applies_to_default():
    from app.gateway.services import _clamp_recursion_limit

    assert _clamp_recursion_limit(None, 10) <= 10


def calls(start, count):
    return [{"id": str(i), "name": "read_file", "args": {"path": str(i)}, "type": "tool_call"} for i in range(start, start + count)]


def test_truncated_tool_batch_is_marked_capped_even_if_model_finishes_next():
    guard = ToolCallBudgetMiddleware(max_tool_calls=20)
    result = guard.after_model({"messages": [HumanMessage(content="work"), AIMessage(content="", tool_calls=calls(0, 19)), AIMessage(content="", tool_calls=calls(19, 3))]}, NS(context={"run_id": "audit"}))
    assert len(result["messages"][0].tool_calls) == 1
    assert guard.consume_stop_reason("audit") is not None


def test_parallel_central_actions_are_all_counted():
    router = build_default_action_router()
    state = {"sp_loop_iteration": 18, "sp_loop_run_id": "audit", "sp_max_loop_iterations": 20}
    a = router.execute(SPAction.create(ActionType.THINK, reason="a", task="a", stage="planning"), state=state, run_id="audit")
    b = router.execute(SPAction.create(ActionType.THINK, reason="b", task="b", stage="planning"), state=state, run_id="audit")
    counted = merge_sp_counter(a.state_update["sp_loop_iteration"], b.state_update["sp_loop_iteration"])
    ledger = merge_sp_idempotency_ledger(a.state_update["sp_idempotency_ledger"], b.state_update["sp_idempotency_ledger"])
    assert sp_action_count({**state, "sp_loop_iteration": counted, "sp_idempotency_ledger": ledger}, "audit") == 20


def trace(command, content, callid):
    return [{"type": "ai", "tool_calls": [{"id": callid, "name": "bash", "args": {"command": command}}]}, {"type": "tool", "name": "bash", "tool_call_id": callid, "status": "success", "content": content}]


def test_later_failed_check_invalidates_earlier_success():
    t = SPSubagentTask(action_id="verify", subagent_type="coder", task="Verify code", description="Verify", metadata={"stage": "verification", "verification_only": True, "requires_implementation": False})
    messages = trace("pytest tests/test_core.py", "1 passed\nExit Code: 0", "pass") + trace("pytest tests/test_core.py", "1 failed\nExit Code: 1", "fail")
    result = normalize_dr2_subagent_result(NS(status="completed", result="done", ai_messages=messages), task=t)
    assert result.completion_status == "partial", result.artifact_metadata


def test_typescript_spec_is_not_source_implementation():
    messages = [{"type": "ai", "tool_calls": [{"id": "edit", "name": "write_file", "args": {"path": "src/foo.spec.ts", "content": "test()"}}]}, {"type": "tool", "tool_call_id": "edit", "name": "write_file", "content": "OK"}]
    assert not _implementation_verification(messages)["passed"]


def test_length_retry_must_not_shrink_bound_model_output_limit():
    from deerflow.sp.central_resilience import SPCentralResilienceMiddleware

    request = NS(model=NS(max_tokens=8192), model_settings={}, override=lambda **kw: NS(**kw))
    retried = SPCentralResilienceMiddleware(retry_max_tokens=2048)._retry_request(request, expand_tokens=True)
    assert retried.model_settings["max_tokens"] >= request.model.max_tokens


def test_fractional_compaction_uses_main_model_window_not_summary_model_window():
    from unittest.mock import patch

    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from deerflow.agents.middlewares.summarization_middleware import create_summarization_middleware
    from deerflow.config.app_config import AppConfig

    cfg = AppConfig(
        models=[{"name": "main-32k", "use": "langchain_openai:ChatOpenAI", "model": "main", "profile": {"max_input_tokens": 32000}}, {"name": "summary-128k", "use": "langchain_openai:ChatOpenAI", "model": "summary"}],
        sandbox={"use": "test"},
        summarization={"enabled": True, "model_name": "summary-128k", "trigger": {"type": "fraction", "value": 0.8}},
    )
    summary = FakeMessagesListChatModel(responses=[AIMessage(content="summary")], profile={"max_input_tokens": 128000})
    with patch("deerflow.agents.middlewares.summarization_middleware.create_chat_model", return_value=summary):
        middleware = create_summarization_middleware(app_config=cfg)
    assert middleware._should_summarize([], 33000)


def test_compaction_uses_selected_main_profile_without_explicit_compressor():
    from unittest.mock import patch

    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from deerflow.agents.middlewares.summarization_middleware import create_summarization_middleware
    from deerflow.config.app_config import AppConfig

    cfg = AppConfig(
        models=[{"name": name, "use": "test", "model": name} for name in ("default-128k", "selected-32k")],
        sandbox={"use": "test"},
        summarization={"enabled": True},
    )

    def model_for(**kwargs):
        window = 32000 if kwargs.get("name") == "selected-32k" else 128000
        return FakeMessagesListChatModel(responses=[AIMessage(content="summary")], profile={"max_input_tokens": window})

    with patch("deerflow.agents.middlewares.summarization_middleware.create_chat_model", side_effect=model_for):
        middleware = create_summarization_middleware(app_config=cfg, main_model_name="selected-32k")
    assert not middleware._should_summarize([], 18000)
    assert middleware._should_summarize([], 19000)


def test_large_main_window_preserves_sixty_four_thousand_token_trigger():
    from unittest.mock import patch

    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from deerflow.agents.middlewares.summarization_middleware import create_summarization_middleware
    from deerflow.config.app_config import AppConfig

    cfg = AppConfig(models=[{"name": "main", "model": "main", "use": "test", "context_window": 128000}], sandbox={"use": "test"}, summarization={"enabled": True})
    model = FakeMessagesListChatModel(responses=[AIMessage(content="summary")])
    with patch("deerflow.agents.middlewares.summarization_middleware.create_chat_model", return_value=model):
        middleware = create_summarization_middleware(app_config=cfg)
    assert not middleware._should_summarize([], 63999)
    assert middleware._should_summarize([], 64000)


def test_compressor_reserves_its_own_output_space():
    from unittest.mock import patch

    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from deerflow.agents.middlewares.summarization_middleware import create_summarization_middleware
    from deerflow.config.app_config import AppConfig

    cfg = AppConfig(
        models=[
            {"name": "main", "model": "main", "use": "test", "context_window": 128000},
            {"name": "summary", "model": "summary", "use": "test", "context_window": 32000, "max_tokens": 16000},
        ],
        sandbox={"use": "test"},
        summarization={"enabled": True, "model_name": "summary"},
    )
    model = FakeMessagesListChatModel(responses=[AIMessage(content="summary")])
    with patch("deerflow.agents.middlewares.summarization_middleware.create_chat_model", return_value=model):
        middleware = create_summarization_middleware(app_config=cfg)
    assert middleware.trim_tokens_to_summarize + 16000 + 2048 < 32000


def test_null_summary_trim_really_disables_trimming():
    from unittest.mock import patch

    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from deerflow.agents.middlewares.summarization_middleware import create_summarization_middleware
    from deerflow.config.app_config import AppConfig

    cfg = AppConfig(models=[], sandbox={"use": "test"}, summarization={"enabled": True, "trim_tokens_to_summarize": None})
    model = FakeMessagesListChatModel(responses=[AIMessage(content="summary")])
    with patch("deerflow.agents.middlewares.summarization_middleware.create_chat_model", return_value=model):
        middleware = create_summarization_middleware(app_config=cfg)
    assert middleware.trim_tokens_to_summarize is None
