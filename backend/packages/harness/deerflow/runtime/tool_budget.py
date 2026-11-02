"""Thread-safe tool admission shared by every agent in one application run."""

from threading import Lock
from typing import Any

RUN_TOOL_BUDGET_KEY = "__run_tool_budget"
DEFAULT_RUN_TOOL_CALLS = 200
_context_lock = Lock()


class RunToolBudget:
    def __init__(self, max_calls: int = DEFAULT_RUN_TOOL_CALLS):
        if isinstance(max_calls, bool) or not isinstance(max_calls, int) or max_calls < 1:
            raise ValueError("Run tool budget must be a positive integer")
        self.max_calls = max_calls
        self._used = 0
        self._lock = Lock()

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    def admit(self) -> bool:
        """Count an attempted dispatch; failed tools and retries still consume it."""
        with self._lock:
            if self._used >= self.max_calls:
                return False
            self._used += 1
            return True


def resolve_run_tool_budget(context: Any, default_max_calls: int = DEFAULT_RUN_TOOL_CALLS) -> RunToolBudget | None:
    if not isinstance(context, dict):
        return None
    with _context_lock:
        current = context.get(RUN_TOOL_BUDGET_KEY)
        if isinstance(current, RunToolBudget):
            return current
        app_config = context.get("app_config")
        maximum = getattr(app_config, "max_tool_calls_per_run", default_max_calls)
        budget = RunToolBudget(maximum)
        context[RUN_TOOL_BUDGET_KEY] = budget
        return budget
