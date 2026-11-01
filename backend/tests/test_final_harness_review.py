"""Final regressions for model budgets and verification-only preflight calls."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.middlewares.summarization_middleware import create_summarization_middleware
from deerflow.agents.middlewares.token_budget_middleware import TokenBudgetMiddleware
from deerflow.config.app_config import AppConfig
from deerflow.config.token_budget_config import TokenBudgetConfig
from deerflow.sp.subagents.dr2_adapter import _test_execution_verification


@pytest.mark.parametrize("command", ["pytest --collect-only", "python -m pytest --version", "pytest --help", "npm test -- --listTests", "go test -list .", "cargo test -- --list"])
def test_preflight_is_not_test_execution(command):
    messages = [
        {"type": "ai", "tool_calls": [{"id": "check", "name": "bash", "args": {"command": command}}]},
        {"type": "tool", "tool_call_id": "check", "name": "bash", "content": "Exit Code: 0"},
    ]
    assert not _test_execution_verification(messages)["passed"]


def test_compaction_reserves_larger_thinking_output_cap():
    cfg = AppConfig(models=[{"name": "main", "use": "test", "model": "main", "context_window": 32000, "supports_thinking": True, "when_thinking_enabled": {"max_tokens": 16384}}], sandbox={"use": "test"}, summarization={"enabled": True})
    model = FakeMessagesListChatModel(responses=[AIMessage(content="summary")])
    with patch("deerflow.agents.middlewares.summarization_middleware.create_chat_model", return_value=model):
        middleware = create_summarization_middleware(app_config=cfg)
    assert middleware._should_summarize([], 12000)


def test_compaction_reserves_compressor_disabled_thinking_output_cap():
    cfg = AppConfig(models=[{"name": "main", "use": "test", "model": "main", "context_window": 32000, "when_thinking_disabled": {"max_tokens": 16000}}], sandbox={"use": "test"}, summarization={"enabled": True})
    model = FakeMessagesListChatModel(responses=[AIMessage(content="summary")])
    with patch("deerflow.agents.middlewares.summarization_middleware.create_chat_model", return_value=model):
        middleware = create_summarization_middleware(app_config=cfg)
    assert middleware.trim_tokens_to_summarize + 16000 + 2048 < 32000


def test_chinese_context_is_not_estimated_at_four_characters_per_token():
    cfg = AppConfig(models=[], sandbox={"use": "test"}, summarization={"enabled": True})
    model = FakeMessagesListChatModel(responses=[AIMessage(content="summary")])
    with patch("deerflow.agents.middlewares.summarization_middleware.create_chat_model", return_value=model):
        middleware = create_summarization_middleware(app_config=cfg)
    assert middleware.token_counter([HumanMessage(content="中文数据" * 1000)]) >= 4000


def test_unmetered_chinese_input_engages_token_backstop():
    guard = TokenBudgetMiddleware(TokenBudgetConfig(enabled=True, max_tokens=10000, max_input_tokens=1000))
    latest = AIMessage(content="", tool_calls=[{"id": "call", "name": "bash", "args": {}, "type": "tool_call"}])
    update = guard.after_model({"messages": [HumanMessage(content="中文数据" * 500), latest]}, SimpleNamespace(context={"run_id": "cjk-budget"}))
    assert update is not None and not update["messages"][0].tool_calls


def test_runtime_output_override_is_reserved_by_main_compaction():
    from deerflow.agents.lead_agent.agent import build_middlewares
    from deerflow.agents.middlewares.summarization_middleware import DeerFlowSummarizationMiddleware

    cfg = AppConfig(models=[{"name": "main", "use": "test", "model": "main", "context_window": 32000}], sandbox={"use": "test"}, summarization={"enabled": True})
    model = FakeMessagesListChatModel(responses=[AIMessage(content="summary")])
    with patch("deerflow.agents.middlewares.summarization_middleware.create_chat_model", return_value=model):
        middlewares = build_middlewares({"configurable": {"sp_central_max_tokens": 16384}}, model_name="main", app_config=cfg, control_plane_only=True)
    summary = next(item for item in middlewares if isinstance(item, DeerFlowSummarizationMiddleware))
    assert summary._should_summarize([], 12000)


def test_token_estimate_preserves_ascii_and_ignores_image_payload_bytes():
    from langchain_core.messages.utils import count_tokens_approximately

    from deerflow.utils.token_estimation import estimate_message_tokens

    plain = [HumanMessage(content="ordinary English and source code" * 100)]
    assert estimate_message_tokens(plain) == count_tokens_approximately(plain)
    image = HumanMessage(content=[{"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 100000}}])
    assert estimate_message_tokens([image]) < 200
