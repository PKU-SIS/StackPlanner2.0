"""A per-delegation tool-call ceiling independent of loop detection."""

from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

from deerflow.agents.middlewares._bounded_dict import BoundedDict
from deerflow.agents.middlewares.web_search_budget_middleware import _real_user_turn_start

MAX_SUBAGENT_TOOL_CALLS = 50


class ToolCallBudgetMiddleware(AgentMiddleware):
    """Admit only the remaining prefix of a parallel batch before execution.

    Counts come from graph messages, so parallel execution order and middleware
    reuse cannot reset or race the counter. Each subagent has its own history.
    """

    def __init__(self, max_tool_calls: int = MAX_SUBAGENT_TOOL_CALLS):
        if isinstance(max_tool_calls, bool) or not isinstance(max_tool_calls, int) or max_tool_calls < 1:
            raise ValueError("max_tool_calls must be a positive integer")
        self.max_tool_calls = min(MAX_SUBAGENT_TOOL_CALLS, max_tool_calls)
        self._stop_reasons: BoundedDict[str, str] = BoundedDict(1000)

    def consume_stop_reason(self, run_id: str | None) -> str | None:
        return self._stop_reasons.pop(str(run_id), None)

    def _apply(self, state: Any, runtime: Any) -> dict | None:
        messages = list(state.get("messages", []))
        if not messages or not isinstance(messages[-1], AIMessage) or not messages[-1].tool_calls:
            return None
        latest = messages[-1]
        used = sum(len(m.tool_calls) for m in messages[_real_user_turn_start(messages) : -1] if isinstance(m, AIMessage))
        remaining = max(0, self.max_tool_calls - used)
        if len(latest.tool_calls) <= remaining:
            return None
        admitted = latest.tool_calls[:remaining]
        # Keep provider-specific raw function calls consistent with canonical calls.
        kwargs = dict(latest.additional_kwargs or {})
        kwargs.pop("tool_calls", None)
        kwargs.pop("function_call", None)
        update: dict[str, Any] = {"tool_calls": admitted, "invalid_tool_calls": [], "additional_kwargs": kwargs}
        run_id = str((runtime.context or {}).get("run_id"))
        self._stop_reasons[run_id] = "loop_capped"
        if not admitted:
            update["content"] = "Tool-call budget exhausted. Return the evidence collected so far and identify any unfinished work."
            metadata = dict(latest.response_metadata or {})
            metadata["finish_reason"] = "stop"
            update["response_metadata"] = metadata
        return {"messages": [latest.model_copy(update=update)]}

    @override
    def after_model(self, state: Any, runtime: Any) -> dict | None:
        return self._apply(state, runtime)

    @override
    async def aafter_model(self, state: Any, runtime: Any) -> dict | None:
        return self._apply(state, runtime)


class RunToolBudgetMiddleware(AgentMiddleware):
    """Enforce one atomic admission counter for Central, children and retries."""

    def __init__(self, max_calls: int = 200):
        from deerflow.runtime.tool_budget import RunToolBudget

        self._max_calls = max_calls
        self._anonymous_budget = RunToolBudget(max_calls)
        self._stop_reasons: BoundedDict[str, str] = BoundedDict(1000)

    def before_agent(self, state: Any, runtime: Any) -> None:
        from deerflow.runtime.tool_budget import RunToolBudget, resolve_run_tool_budget

        if resolve_run_tool_budget(runtime.context, self._max_calls) is None:
            # Application entrypoints always supply a distinct run context.
            # Keep standalone, sequential invoke() calls bounded as well.
            self._anonymous_budget = RunToolBudget(self._max_calls)

    async def abefore_agent(self, state: Any, runtime: Any) -> None:
        self.before_agent(state, runtime)

    def _budget(self, runtime):
        from deerflow.runtime.tool_budget import resolve_run_tool_budget

        return resolve_run_tool_budget(runtime.context, self._max_calls) or self._anonymous_budget

    def consume_stop_reason(self, run_id: str | None) -> str | None:
        return self._stop_reasons.pop(str(run_id), None)

    def _mark_capped(self, runtime):
        self._stop_reasons[str((runtime.context or {}).get("run_id"))] = "loop_capped"

    def _deny(self, request):
        import json

        from langchain_core.messages import ToolMessage

        self._mark_capped(request.runtime)
        budget = self._budget(request.runtime)
        return ToolMessage(
            content=json.dumps({"error": "RUN_TOOL_BUDGET_EXHAUSTED", "limit": budget.max_calls, "used": budget.used, "retryable": False}),
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
            status="error",
        )

    def wrap_tool_call(self, request, handler):
        return handler(request) if self._budget(request.runtime).admit() else self._deny(request)

    async def awrap_tool_call(self, request, handler):
        return await handler(request) if self._budget(request.runtime).admit() else self._deny(request)

    def after_model(self, state: Any, runtime: Any) -> dict | None:
        budget = self._budget(runtime)
        messages = state.get("messages", [])
        if budget.used < budget.max_calls or not messages or not isinstance(messages[-1], AIMessage) or not messages[-1].tool_calls:
            return None
        self._mark_capped(runtime)
        latest = messages[-1]
        kwargs = dict(latest.additional_kwargs or {})
        kwargs.pop("tool_calls", None)
        kwargs.pop("function_call", None)
        return {
            "messages": [
                latest.model_copy(
                    update={
                        "tool_calls": [],
                        "invalid_tool_calls": [],
                        "additional_kwargs": kwargs,
                        "response_metadata": {**latest.response_metadata, "finish_reason": "stop"},
                        "content": f"The run's {budget.max_calls}-call tool budget is exhausted. Remaining work is unverified.",
                    }
                )
            ]
        }

    async def aafter_model(self, state: Any, runtime: Any) -> dict | None:
        return self.after_model(state, runtime)
