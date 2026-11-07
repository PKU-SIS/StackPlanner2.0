"""Aggregate budgets include Central, every child, failures and retries."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from deerflow.agents.middlewares.tool_call_budget_middleware import RunToolBudgetMiddleware, ToolCallBudgetMiddleware
from deerflow.runtime.tool_budget import RUN_TOOL_BUDGET_KEY, RunToolBudget, resolve_run_tool_budget


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, *args, **kwargs):
        return self


def test_atomic_counter_admits_exactly_two_hundred_attempts():
    budget = RunToolBudget()
    with ThreadPoolExecutor(max_workers=16) as pool:
        admitted = list(pool.map(lambda _: budget.admit(), range(500)))
    assert sum(admitted) == budget.used == 200


def test_separate_run_contexts_have_independent_budgets():
    first, second = {}, {}
    first_budget = resolve_run_tool_budget(first)
    first_budget.admit()
    assert resolve_run_tool_budget(first) is first_budget
    assert resolve_run_tool_budget(second).used == 0


def test_failures_and_retries_consume_global_slots():
    budget = RunToolBudget(2)
    runtime = SimpleNamespace(context={"run_id": "run", RUN_TOOL_BUDGET_KEY: budget})
    request = SimpleNamespace(runtime=runtime, tool_call={"id": "attempt", "name": "bash"})
    guard = RunToolBudgetMiddleware()

    def fail(request):
        raise ValueError("failed tool")

    with pytest.raises(ValueError):
        guard.wrap_tool_call(request, fail)
    expected = ToolMessage(content="ok", tool_call_id="attempt", name="bash")
    assert guard.wrap_tool_call(request, lambda _: expected) is expected
    result = guard.wrap_tool_call(request, lambda _: pytest.fail("budget must block this attempt"))
    assert result.status == "error"
    assert budget.used == 2
    assert guard.consume_stop_reason("run") == "loop_capped"


@pytest.mark.asyncio
async def test_parallel_children_share_two_hundred_slots_including_central():
    budget = RunToolBudget()
    context = {"run_id": "shared", RUN_TOOL_BUDGET_KEY: budget}
    root_guard = RunToolBudgetMiddleware()
    for i in range(5):
        request = SimpleNamespace(runtime=SimpleNamespace(context=context), tool_call={"id": f"central-{i}", "name": "sp_delegate"})
        root_guard.wrap_tool_call(request, lambda req: ToolMessage(content="delegated", name="sp_delegate", tool_call_id=req.tool_call["id"]))
    executed = []

    @tool
    def record(child: int, value: int) -> str:
        """Record a child execution."""
        executed.append((child, value))
        return "ok"

    agents = []
    guards = []
    for child in range(4):
        calls = [{"id": f"{child}-{i}", "name": "record", "args": {"child": child, "value": i}, "type": "tool_call"} for i in range(50)]
        guard = RunToolBudgetMiddleware()
        guards.append(guard)
        agents.append(create_agent(ToolModel(responses=[AIMessage(content="", tool_calls=calls), AIMessage(content="done")]), tools=[record], middleware=[guard, ToolCallBudgetMiddleware()]))
    await asyncio.gather(*(agent.ainvoke({"messages": [HumanMessage(content="work")]}, context=context, config={"recursion_limit": 400}) for agent in agents))
    assert len(executed) == 195
    assert budget.used == 200
    assert all(sum(c == child for c, _ in executed) <= 50 for child in range(4))
    assert any(guard.consume_stop_reason("shared") == "loop_capped" for guard in guards)
    # A corrective delegation cannot reset the same run's counter.
    result = await agents[0].ainvoke({"messages": [HumanMessage(content="retry")]}, context=context, config={"recursion_limit": 400})
    assert len(executed) == 195
    assert "budget" in result["messages"][-1].content


@pytest.mark.asyncio
async def test_full_child_chain_allows_fifty_sequential_calls_with_vision():
    from deerflow.agents.middlewares.tool_error_handling_middleware import build_subagent_runtime_middlewares
    from deerflow.config.app_config import AppConfig

    # Executor reserves frequency slot 51 for the independent 50-call guard
    # (covered by test_subagent_executor); use that effective config here.
    config = AppConfig(models=[{"name": "fake", "model": "fake", "use": "test", "supports_vision": True}], sandbox={"use": "test"}, loop_detection={"tool_freq_hard_limit": 51})
    middlewares = build_subagent_runtime_middlewares(app_config=config)
    executed = []

    @tool
    def record(value: int) -> str:
        """Record one execution."""
        executed.append(value)
        return "ok"

    responses = [AIMessage(content="", tool_calls=[{"id": f"call-{i}", "name": "record", "args": {"value": i}, "type": "tool_call"}]) for i in range(51)]
    budget = RunToolBudget()
    agent = create_agent(ToolModel(responses=responses), tools=[record], middleware=middlewares)
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content="work")]},
        context={"thread_id": "child", "run_id": "child", "app_config": config, RUN_TOOL_BUDGET_KEY: budget},
        config={"recursion_limit": 480},
    )
    assert executed == list(range(50))
    assert budget.used == 50
    assert not result["messages"][-1].tool_calls
    assert any(m.consume_stop_reason("child") == "loop_capped" for m in middlewares if callable(getattr(m, "consume_stop_reason", None)))
