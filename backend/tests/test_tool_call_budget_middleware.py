"""Exercise tool ceilings through a real LangChain execution graph."""

from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from deerflow.agents.middlewares.tool_call_budget_middleware import ToolCallBudgetMiddleware


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def calls(start, count):
    return [{"id": f"call-{i}", "name": "record", "args": {"value": i}, "type": "tool_call"} for i in range(start, start + count)]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("batch_size", [1, 4, 25])
def test_graph_executes_exactly_twenty_calls_and_preserves_message_pairing(asynchronous, batch_size):
    import asyncio

    executed = []

    @tool
    def record(value: int) -> str:
        """Record an execution."""
        executed.append(value)
        return str(value)

    responses = [AIMessage(content="", tool_calls=calls(i, batch_size)) for i in range(0, 25 + batch_size, batch_size)]
    guard = ToolCallBudgetMiddleware(max_tool_calls=20)
    agent = create_agent(ToolModel(responses=responses), tools=[record], middleware=[guard])
    config = {"recursion_limit": 150}
    initial = {"messages": [HumanMessage(content="Record the requested values")]}
    if asynchronous:
        result = asyncio.run(agent.ainvoke(initial, config))
    else:
        result = agent.invoke(initial, config)
    assert len(executed) == 20
    assert set(executed) == set(range(20))
    issued = [c["id"] for m in result["messages"] if isinstance(m, AIMessage) for c in m.tool_calls]
    answered = [m.tool_call_id for m in result["messages"] if isinstance(m, ToolMessage)]
    assert sorted(issued) == sorted(answered)
    assert "budget exhausted" in result["messages"][-1].content.lower()


def test_budget_is_local_to_delegation_and_resets_at_next_user_turn():
    guard = ToolCallBudgetMiddleware(max_tool_calls=2)
    runtime = SimpleNamespace(context={"run_id": "run"})
    history = [HumanMessage(content="task"), AIMessage(content="", tool_calls=calls(0, 2))]
    result = guard.after_model({"messages": [*history, AIMessage(content="", tool_calls=calls(2, 1))]}, runtime)
    assert result["messages"][0].tool_calls == []
    assert guard.consume_stop_reason("run") == "loop_capped"
    assert guard.consume_stop_reason("run") is None
    assert guard.after_model({"messages": [*history, HumanMessage(content="next"), AIMessage(content="", tool_calls=calls(3, 1))]}, runtime) is None


def test_budget_admits_remaining_prefix_without_losing_last_slot():
    guard = ToolCallBudgetMiddleware(max_tool_calls=3)
    state = {"messages": [HumanMessage(content="task"), AIMessage(content="", tool_calls=calls(0, 2)), AIMessage(content="", tool_calls=calls(2, 4))]}
    result = guard.after_model(state, SimpleNamespace(context={"run_id": "run"}))
    assert [c["id"] for c in result["messages"][0].tool_calls] == ["call-2"]


def test_subagent_budget_remains_enabled_without_loop_detection(monkeypatch):
    from deerflow.agents.middlewares.token_budget_middleware import TokenBudgetMiddleware
    from deerflow.agents.middlewares.tool_error_handling_middleware import build_subagent_runtime_middlewares
    from deerflow.agents.middlewares.web_search_budget_middleware import WebSearchBudgetMiddleware
    from deerflow.config.app_config import AppConfig
    from deerflow.config.loop_detection_config import LoopDetectionConfig

    config = AppConfig(models=[], sandbox={"use": "test"}, loop_detection=LoopDetectionConfig(enabled=False))
    middlewares = build_subagent_runtime_middlewares(app_config=config, budgets={"max_tool_calls": 3, "max_searches": 2, "max_tokens": 500})
    assert next(m for m in middlewares if isinstance(m, ToolCallBudgetMiddleware)).max_tool_calls == 3
    assert next(m for m in middlewares if isinstance(m, WebSearchBudgetMiddleware)).max_searches == 2
    assert next(m for m in middlewares if isinstance(m, TokenBudgetMiddleware))._config.max_tokens == 500


def test_sp_provider_enforces_timeout_turn_budget_and_forwards_runtime_limits(monkeypatch):
    import deerflow.subagents as subagents
    from deerflow.config.app_config import AppConfig
    from deerflow.sp.runtime import DR2SPExecutorProvider
    from deerflow.sp.subagents.adapter import SPSubagentTask

    captured = []

    class Executor:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        def execute(self, prompt):
            return SimpleNamespace(status="completed", result="data checked", ai_messages=[])

    monkeypatch.setattr(subagents, "SubagentExecutor", Executor)
    monkeypatch.setattr(DR2SPExecutorProvider, "_available_tools", lambda self: [])
    config = AppConfig(models=[], sandbox={"use": "test"})
    provider = DR2SPExecutorProvider(app_config=config, parent_model="test", runnable_config={})
    adapter = provider({}, SimpleNamespace(context={}, stream_writer=None))
    adapter.execute(SPSubagentTask(action_id="budget", subagent_type="researcher", task="Fetch evidence", description="evidence", budgets={"max_turns": 7, "timeout_seconds": 5, "max_searches": 2, "max_tool_calls": 4, "max_tokens": 1000}))
    assert captured[0]["config"].max_turns == 7
    assert captured[0]["config"].timeout_seconds == 5
    assert captured[0]["budgets"]["max_searches"] == 2
    assert captured[0]["budgets"]["max_tool_calls"] == 4
    assert captured[0]["budgets"]["max_tokens"] == 1000
