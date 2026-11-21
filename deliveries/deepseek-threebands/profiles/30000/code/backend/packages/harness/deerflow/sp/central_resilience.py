"""Bounded recovery for stalled StackPlanner CentralAgent model calls."""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)


class SPCentralModelTimeout(TimeoutError):
    """Raised only when both the initial Central request and its retry stall."""


class SPCentralEmptyResponse(RuntimeError):
    """Raised when Central returns neither text nor an SP control action."""

    def __init__(self, message: str, *, requires_more_tokens: bool = False) -> None:
        super().__init__(message)
        self.requires_more_tokens = requires_more_tokens


class SPCentralInvalidToolCall(RuntimeError):
    """Raised when Central attempted an SP action whose JSON was truncated."""

    requires_more_tokens = True


class SPCentralResilienceMiddleware(AgentMiddleware[AgentState]):
    """Prevent one stalled Central request from consuming the whole run.

    The middleware is installed only on the SP CentralAgent. It never adds an
    ordinary tool and never changes the subagent execution path. After one
    failed retry it emits one synthetic ``sp_think`` control action, allowing
    the existing SP loop to make another decision without an unbounded retry.
    """

    state_schema = AgentState

    def __init__(
        self,
        *,
        initial_timeout_seconds: float = 90.0,
        retry_timeout_seconds: float = 60.0,
        retry_max_tokens: int = 2048,
    ) -> None:
        super().__init__()
        if initial_timeout_seconds <= 0 or retry_timeout_seconds <= 0:
            raise ValueError("Central request timeouts must be positive")
        if retry_max_tokens <= 0:
            raise ValueError("Central retry max tokens must be positive")
        self.initial_timeout_seconds = float(initial_timeout_seconds)
        self.retry_timeout_seconds = float(retry_timeout_seconds)
        self.retry_max_tokens = int(retry_max_tokens)
        # Keep one bounded recovery checkpoint for the initial decision and
        # one for a later decision after delegated work.  A single global
        # flag used to make the first recovery consume the only fallback and
        # left post-delegation Central calls unprotected.
        self._fallback_runs: set[tuple[str, str]] = set()
        self._lock = threading.Lock()

    @staticmethod
    def _context(request: ModelRequest) -> Mapping[str, Any]:
        runtime = getattr(request, "runtime", None)
        context = getattr(runtime, "context", None)
        return context if isinstance(context, Mapping) else {}

    def _is_initial_central_call(self, request: ModelRequest) -> bool:
        state = request.state if isinstance(request.state, Mapping) else {}
        stage = str(state.get("sp_current_stage") or "").strip().lower()
        current_action = state.get("sp_current_action")
        active_delegate = state.get("sp_active_delegate_id")
        loop_iteration = int(state.get("sp_loop_iteration") or 0)
        last_action = state.get("sp_last_action_id")
        # Perception/planning are the expensive first-decision boundary. Do
        # not impose this retry policy on later Central decisions after a child
        # result, where the normal LLM error middleware is sufficient.
        return stage in {"perception", "planning"} and current_action is None and not active_delegate and loop_iteration == 0 and not last_action

    @staticmethod
    def _is_central_decision_call(request: ModelRequest) -> bool:
        state = request.state if isinstance(request.state, Mapping) else {}
        stage = str(state.get("sp_current_stage") or "").strip().lower()
        return stage in {"perception", "planning", "research", "implementation", "verification", "reporting", "revision"} and state.get("sp_current_action") is None and not state.get("sp_active_delegate_id")

    def _run_key(self, request: ModelRequest) -> str:
        context = self._context(request)
        state = request.state if isinstance(request.state, Mapping) else {}
        return str(context.get("run_id") or state.get("sp_loop_run_id") or "anonymous")

    def _emit(self, payload: dict[str, Any]) -> None:
        try:
            from langgraph.config import get_stream_writer

            get_stream_writer()(payload)
        except Exception:
            logger.debug("Failed to emit Central resilience event", exc_info=True)

    def _retry_request(self, request: ModelRequest, *, expand_tokens: bool = False) -> ModelRequest:
        settings = dict(getattr(request, "model_settings", {}) or {})
        current_max_tokens = settings.get("max_tokens")
        if isinstance(current_max_tokens, int) and current_max_tokens > 0:
            settings["max_tokens"] = max(current_max_tokens, self.retry_max_tokens) if expand_tokens else min(current_max_tokens, self.retry_max_tokens)
        else:
            settings["max_tokens"] = self.retry_max_tokens
        return request.override(model_settings=settings)

    def _mark_fallback_used(self, run_key: str, phase: str) -> bool:
        key = (run_key, phase)
        with self._lock:
            if key in self._fallback_runs:
                return False
            self._fallback_runs.add(key)
            if len(self._fallback_runs) > 4096:
                self._fallback_runs = set(list(self._fallback_runs)[-2048:])
            return True

    def _fallback_response(self, request: ModelRequest, *, run_key: str) -> ModelResponse:
        call_id = f"sp-timeout-{uuid.uuid4().hex[:12]}"
        stage = str((request.state or {}).get("sp_current_stage") or "perception")
        self._emit(
            {
                "type": "sp_central_timeout_fallback",
                "run_id": run_key,
                "stage": stage,
                "message": "Central model request timed out twice; issuing one bounded THINK checkpoint.",
            }
        )
        return ModelResponse(
            result=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "sp_think",
                            "args": {
                                "task": "The Central model request stalled. Reassess the current task and choose the next SP action using the available context.",
                                "reason": "Bounded Central request recovery after two model timeouts.",
                                "stage": stage,
                            },
                            "id": call_id,
                            "type": "tool_call",
                        }
                    ],
                    additional_kwargs={"sp_central_timeout_recovery": True},
                )
            ]
        )

    @staticmethod
    def _result_failure(result: ModelCallResult) -> RuntimeError | None:
        """Reject silent model completions before the agent loop can terminate.

        Central may use free-form text as an implicit THINK action, so any
        non-empty content is valid.  Native tool calls are also valid.  An
        empty message with no calls, however, cannot advance the SP protocol
        and would otherwise look like a successful terminal answer.
        """
        messages = getattr(result, "result", None)
        if isinstance(result, AIMessage):
            messages = [result]
        if not isinstance(messages, list) or not messages:
            return SPCentralEmptyResponse("CentralAgent returned no model messages")
        saw_length_limit = False
        for message in messages:
            if not isinstance(message, AIMessage):
                continue
            if message.tool_calls:
                return None
            invalid_sp_calls = [call for call in (message.invalid_tool_calls or []) if str(call.get("name") or "").startswith("sp_")]
            if invalid_sp_calls:
                names = ", ".join(sorted({str(call.get("name")) for call in invalid_sp_calls}))
                return SPCentralInvalidToolCall(f"CentralAgent emitted invalid or truncated SP tool arguments: {names}")
            saw_length_limit = saw_length_limit or str(message.response_metadata.get("finish_reason") or "") == "length"
            content = message.content
            if isinstance(content, str) and content.strip():
                return None
            if isinstance(content, list) and any(str(item).strip() for item in content):
                return None
        return SPCentralEmptyResponse(
            "CentralAgent returned empty content with no SP action",
            requires_more_tokens=saw_length_limit,
        )

    async def _invoke_checked(
        self,
        handler: Callable[[ModelRequest], Awaitable[ModelCallResult]],
        request: ModelRequest,
        *,
        timeout_seconds: float,
    ) -> ModelCallResult:
        result = await asyncio.wait_for(handler(request), timeout=timeout_seconds)
        failure = self._result_failure(result)
        if failure is not None:
            raise failure
        return result

    def _emit_attempt_failure(
        self,
        request: ModelRequest,
        *,
        run_key: str,
        phase: str,
        attempt: int,
        timeout_seconds: float,
        error: BaseException,
    ) -> None:
        is_invalid_tool = isinstance(error, SPCentralInvalidToolCall)
        is_empty = isinstance(error, SPCentralEmptyResponse)
        self._emit(
            {
                "type": ("sp_central_invalid_tool_call" if is_invalid_tool else "sp_central_empty_response" if is_empty else "sp_central_model_timeout"),
                "run_id": run_key,
                "attempt": attempt,
                "timeout_seconds": timeout_seconds,
                "phase": phase,
                "stage": str((request.state or {}).get("sp_current_stage") or ""),
                "reason": str(error),
            }
        )

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelCallResult]],
    ) -> ModelCallResult:
        if not self._is_central_decision_call(request):
            return await handler(request)

        run_key = self._run_key(request)
        initial = self._is_initial_central_call(request)
        phase = "initial" if initial else "post_delegate"
        first_timeout = self.initial_timeout_seconds if initial else self.retry_timeout_seconds
        try:
            return await self._invoke_checked(
                handler,
                request,
                timeout_seconds=first_timeout,
            )
        except (TimeoutError, SPCentralEmptyResponse, SPCentralInvalidToolCall) as first_exc:
            self._emit_attempt_failure(
                request,
                run_key=run_key,
                phase=phase,
                attempt=1,
                timeout_seconds=first_timeout,
                error=first_exc,
            )
            try:
                return await self._invoke_checked(
                    handler,
                    self._retry_request(
                        request,
                        expand_tokens=bool(getattr(first_exc, "requires_more_tokens", False)),
                    ),
                    timeout_seconds=self.retry_timeout_seconds,
                )
            except (TimeoutError, SPCentralEmptyResponse, SPCentralInvalidToolCall) as second_exc:
                self._emit_attempt_failure(
                    request,
                    run_key=run_key,
                    phase=phase,
                    attempt=2,
                    timeout_seconds=self.retry_timeout_seconds,
                    error=second_exc,
                )
                if self._mark_fallback_used(run_key, phase):
                    return self._fallback_response(request, run_key=run_key)
                if isinstance(second_exc, (SPCentralEmptyResponse, SPCentralInvalidToolCall)):
                    raise SPCentralEmptyResponse("CentralAgent returned an unusable response after retry") from second_exc
                raise SPCentralModelTimeout(f"CentralAgent model stalled after retry: {self.initial_timeout_seconds}s + {self.retry_timeout_seconds}s") from second_exc
            except Exception:
                raise
        except Exception:
            raise

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelCallResult],
    ) -> ModelCallResult:
        # LangGraph's SP production path is async. Keep sync callers fully
        # compatible; a sync request cannot be safely interrupted without
        # killing its worker thread, so the existing error middleware handles
        # sync provider failures.
        return handler(request)
