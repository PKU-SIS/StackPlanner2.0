"""Specialists compact growing tool histories without losing their contract."""

from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage

from deerflow.agents.middlewares.durable_context_middleware import DurableContextMiddleware
from deerflow.agents.middlewares.summarization_middleware import DeerFlowSummarizationMiddleware
from deerflow.agents.middlewares.tool_error_handling_middleware import build_subagent_runtime_middlewares
from deerflow.config.app_config import AppConfig


def build(*, enabled=True, window=73728, output_limit=8192):
    cfg = AppConfig(
        models=[
            {"name": "specialist", "model": "specialist", "use": "test", "context_window": window},
            {"name": "compressor", "model": "compressor", "use": "test", "context_window": 128000},
        ],
        sandbox={"use": "test"},
        summarization={"enabled": enabled, "model_name": "compressor"},
    )
    model = FakeMessagesListChatModel(responses=[AIMessage(content="Retained evidence: the scenario needs classification.")])
    with (
        patch("deerflow.agents.middlewares.tool_error_handling_middleware._build_runtime_middlewares", return_value=[]),
        patch("deerflow.agents.middlewares.summarization_middleware.create_chat_model", return_value=model),
    ):
        return build_subagent_runtime_middlewares(app_config=cfg, model_name="specialist", output_token_limit=output_limit)


def test_specialist_reserves_its_runtime_output_not_compressor_window():
    middlewares = build(window=32000, output_limit=16000)
    summary = next(item for item in middlewares if isinstance(item, DeerFlowSummarizationMiddleware))
    assert not summary._should_summarize([], 11000)
    assert summary._should_summarize([], 12000)


def test_specialist_compaction_preserves_system_contract_and_reinjects_summary():
    middlewares = build()
    summary = next(item for item in middlewares if isinstance(item, DeerFlowSummarizationMiddleware))
    durable = next(item for item in middlewares if isinstance(item, DurableContextMiddleware))
    contract = SystemMessage(content="SPECIALIST CONTRACT: return the native SP envelope and never invent evidence.", id="contract")
    history = [contract]
    for i in range(12):
        history.extend([HumanMessage(content=f"Evidence {i}: " + "合同条款" * 2000, id=f"evidence-{i}"), AIMessage(content="Recorded", id=f"answer-{i}")])
    update = summary._maybe_summarize({"messages": history}, SimpleNamespace(context={}))
    assert update is not None
    retained = [message for message in update["messages"] if not isinstance(message, RemoveMessage)]
    assert retained[0] is contract
    assert summary.token_counter(retained) < 73728 - 8192
    request = SimpleNamespace(messages=retained, state={"messages": retained, "summary_text": update["summary_text"]})
    request.override = lambda **changes: SimpleNamespace(**changes)
    injected = durable._inject(request)
    assert any("Retained evidence:" in str(message.content) for message in injected.messages)
    assert not any("Retained evidence:" in str(message.content) for message in retained)


def test_disabled_specialist_compaction_does_not_create_summary_provider():
    middlewares = build(enabled=False)
    assert not any(isinstance(item, DeerFlowSummarizationMiddleware) for item in middlewares)
