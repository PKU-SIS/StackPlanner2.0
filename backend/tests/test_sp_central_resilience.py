from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langchain.agents.middleware.types import ModelRequest
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.sp.central_resilience import SPCentralResilienceMiddleware


def _request(*, stage: str = "perception", run_id: str = "run-1", current_action=None) -> ModelRequest:
    runtime = SimpleNamespace(context={"run_id": run_id})
    return ModelRequest(
        model=SimpleNamespace(),
        messages=[HumanMessage(content="task")],
        state={
            "sp_current_stage": stage,
            "sp_current_action": current_action,
            "sp_active_delegate_id": None,
            "sp_loop_run_id": run_id,
        },
        runtime=runtime,
        model_settings={"max_tokens": 8192},
    )


def test_stalled_initial_call_retries_with_bounded_tokens_then_recovers(monkeypatch):
    middleware = SPCentralResilienceMiddleware(initial_timeout_seconds=0.01, retry_timeout_seconds=0.01, retry_max_tokens=128)
    calls = []

    async def handler(request):
        calls.append(request.model_settings)
        await asyncio.sleep(1)

    async def run():
        return await middleware.awrap_model_call(_request(), handler)

    result = asyncio.run(run())
    assert result.result[0].tool_calls[0]["name"] == "sp_think"
    assert calls == [{"max_tokens": 8192}, {"max_tokens": 128}]


def test_central_resilience_does_not_retry_later_or_child_calls():
    middleware = SPCentralResilienceMiddleware(initial_timeout_seconds=0.01, retry_timeout_seconds=0.01)
    calls = 0

    async def handler(_request):
        nonlocal calls
        calls += 1
        return ModelResponse(result=[AIMessage(content="continue")])

    async def run():
        await middleware.awrap_model_call(_request(stage="implementation", current_action={"action_type": "DELEGATE"}), handler)
        await middleware.awrap_model_call(_request(stage="research"), handler)

    asyncio.run(run())
    assert calls == 2


def test_central_resilience_allows_only_one_fallback_per_run():
    middleware = SPCentralResilienceMiddleware(initial_timeout_seconds=0.01, retry_timeout_seconds=0.01)

    async def handler(_request):
        await asyncio.sleep(1)

    async def run():
        await middleware.awrap_model_call(_request(), handler)
        try:
            await middleware.awrap_model_call(_request(), handler)
        except TimeoutError as exc:
            return str(exc)
        return None

    assert "stalled after retry" in asyncio.run(run())


def test_central_resilience_protects_post_delegate_decision_separately():
    middleware = SPCentralResilienceMiddleware(initial_timeout_seconds=0.01, retry_timeout_seconds=0.01)

    async def handler(_request):
        await asyncio.sleep(1)

    async def run():
        # Consume the initial-phase recovery.
        await middleware.awrap_model_call(_request(stage="perception"), handler)
        # A later decision in implementation gets its own bounded recovery.
        request = _request(stage="implementation", run_id="run-1")
        result = await middleware.awrap_model_call(request, handler)
        return result.result[0].tool_calls[0]["name"]

    assert asyncio.run(run()) == "sp_think"


def test_empty_central_response_retries_then_accepts_control_action():
    middleware = SPCentralResilienceMiddleware(initial_timeout_seconds=1, retry_timeout_seconds=1, retry_max_tokens=128)
    calls = []

    async def handler(request):
        calls.append(request.model_settings)
        if len(calls) == 1:
            return ModelResponse(result=[AIMessage(content="", response_metadata={"finish_reason": "length"})])
        return ModelResponse(
            result=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "sp_delegate", "args": {"task": "inspect"}, "id": "delegate-1", "type": "tool_call"}],
                )
            ]
        )

    result = asyncio.run(middleware.awrap_model_call(_request(), handler))

    assert result.result[0].tool_calls[0]["name"] == "sp_delegate"
    assert calls == [{"max_tokens": 8192}, {"max_tokens": 8192}]


def test_two_empty_central_responses_become_bounded_think_action():
    middleware = SPCentralResilienceMiddleware(initial_timeout_seconds=1, retry_timeout_seconds=1)

    async def handler(_request):
        return ModelResponse(result=[AIMessage(content="")])

    result = asyncio.run(middleware.awrap_model_call(_request(), handler))

    assert result.result[0].tool_calls[0]["name"] == "sp_think"


def test_nonempty_free_form_central_response_remains_valid_implicit_think():
    middleware = SPCentralResilienceMiddleware(initial_timeout_seconds=1, retry_timeout_seconds=1)
    expected = ModelResponse(result=[AIMessage(content="Need to inspect the repository first.")])

    async def handler(_request):
        return expected

    assert asyncio.run(middleware.awrap_model_call(_request(), handler)) is expected


def test_truncated_sp_tool_call_retries_with_expanded_token_budget():
    middleware = SPCentralResilienceMiddleware(
        initial_timeout_seconds=1,
        retry_timeout_seconds=1,
        retry_max_tokens=2048,
    )
    calls = []
    request = _request().override(model_settings={"max_tokens": 512})

    async def handler(request):
        calls.append(request.model_settings)
        if len(calls) == 1:
            return ModelResponse(
                result=[
                    AIMessage(
                        content="I will delegate the repository repair.",
                        invalid_tool_calls=[
                            {
                                "name": "sp_delegate",
                                "args": '{"target_agent":"coder","task":"truncated',
                                "id": "bad-delegate",
                                "error": "invalid JSON",
                                "type": "invalid_tool_call",
                            }
                        ],
                        response_metadata={"finish_reason": "tool_calls"},
                    )
                ]
            )
        return ModelResponse(
            result=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "sp_delegate",
                            "args": {"target_agent": "coder", "task": "Implement and test the fix"},
                            "id": "good-delegate",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        )

    result = asyncio.run(middleware.awrap_model_call(request, handler))

    assert result.result[0].tool_calls[0]["id"] == "good-delegate"
    assert calls == [{"max_tokens": 512}, {"max_tokens": 2048}]


def test_length_limited_empty_response_expands_retry_budget():
    middleware = SPCentralResilienceMiddleware(
        initial_timeout_seconds=1,
        retry_timeout_seconds=1,
        retry_max_tokens=1024,
    )
    request = _request()
    request = request.override(model_settings={"max_tokens": 512})
    calls = []

    async def handler(current):
        calls.append(current.model_settings)
        if len(calls) == 1:
            return ModelResponse(result=[AIMessage(content="", response_metadata={"finish_reason": "length"})])
        return ModelResponse(result=[AIMessage(content="Recovered decision")])

    result = asyncio.run(middleware.awrap_model_call(request, handler))

    assert result.result[0].content == "Recovered decision"
    assert calls == [{"max_tokens": 512}, {"max_tokens": 1024}]
