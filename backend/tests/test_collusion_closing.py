"""Budget closing requests a real final message; it never repairs JSON."""

from langchain_core.messages import ToolMessage

from app.experiments.collusion_audit.closing import BudgetClosingMiddleware


def test_bounded_closing_only_removes_tools_after_budget():
    seen = []

    class Request:
        def __init__(self, n):
            self.messages = [ToolMessage("done", tool_call_id=str(i)) for i in range(n)]

        def override(self, **kwargs):
            seen.append(kwargs)
            return self

    m = BudgetClosingMiddleware()
    r = Request(2)
    m.wrap_model_call(r, lambda req: req)
    assert not seen
    r = Request(16)
    m.wrap_model_call(r, lambda req: req)
    assert seen[-1]["tools"] == []
    assert "partial" in seen[-1]["messages"][-1].content
    assert len(r.messages) == 16


def test_quality_closing_counts_actual_calls_across_projected_history_windows():
    from types import SimpleNamespace

    class Request:
        def __init__(self, ids, state=None):
            self.messages = [ToolMessage("fact", tool_call_id=str(i)) for i in ids]
            self.state = state or {}

        def override(self, **kw):
            return SimpleNamespace(**kw)

    guard = BudgetClosingMiddleware(compact_results=True)
    assert guard.project(Request(range(8))).messages
    result = guard.project(Request(range(4, 16)))
    assert result.tools == []
    # The threshold is still 16, replayed history is not 16 new calls.
    replay = BudgetClosingMiddleware(compact_results=True)
    for _ in range(3):
        result = replay.project(Request(range(8)))
        assert not hasattr(result, "tools")
    fresh = BudgetClosingMiddleware(compact_results=True)
    assert not hasattr(fresh.project(Request(range(4, 16))), "tools")


def test_quality_closing_uses_full_state_when_projection_hides_tool_results():
    from types import SimpleNamespace

    full_messages = [ToolMessage("fact", tool_call_id=str(i)) for i in range(16)]

    class Request:
        state = {"messages": full_messages}
        messages = full_messages[-4:]

        def override(self, **kw):
            return SimpleNamespace(**kw)

    result = BudgetClosingMiddleware(compact_results=True).project(Request())
    assert result.tools == []
