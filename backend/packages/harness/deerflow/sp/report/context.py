"""Central control-context middleware for active report runs.

The ledger remains the only persisted business state.  This middleware derives
a request-only control summary immediately before every Central model call, so
first calls, HITL continuations, new runs, and post-compression calls all see
the current gap/dispatch state without a tool round-trip.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import BaseMessage, HumanMessage

from deerflow.agents.thread_state import ThreadState
from deerflow.sp.report.hooks import REPORT_CONTEXT_MESSAGE_NAME, control_context, sync_user_inputs


class SPReportContextMiddleware(AgentMiddleware[ThreadState]):
    """Inject report control state and synchronize new user inputs."""

    state_schema = ThreadState

    @override
    def before_agent(self, state: ThreadState, runtime: Any) -> dict[str, Any] | None:
        del runtime
        sync_user_inputs(state)
        return None

    @override
    async def abefore_agent(self, state: ThreadState, runtime: Any) -> dict[str, Any] | None:
        return self.before_agent(state, runtime)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        return handler(self._augment_request(request))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        return await handler(self._augment_request(request))

    def _augment_request(self, request: ModelRequest) -> ModelRequest:
        messages = getattr(request, "messages", None)
        if not isinstance(messages, list):
            return request
        state = _request_state(request)
        context = control_context(state)
        if context is None:
            return request
        messages = [message for message in messages if getattr(message, "name", None) != REPORT_CONTEXT_MESSAGE_NAME]
        content = json.dumps(context, ensure_ascii=False, indent=2)
        context_message = HumanMessage(
            content=f"<sp-report-context>\n{content}\n</sp-report-context>",
            name=REPORT_CONTEXT_MESSAGE_NAME,
            additional_kwargs={"hide_from_ui": True},
        )
        return request.override(messages=_insert_before_last_human(messages, context_message))


def _request_state(request: ModelRequest) -> Mapping[str, Any]:
    state = getattr(request, "state", None)
    if isinstance(state, Mapping):
        return state
    runtime = getattr(request, "runtime", None)
    runtime_state = getattr(runtime, "state", None)
    return runtime_state if isinstance(runtime_state, Mapping) else {}


def _insert_before_last_human(messages: list[BaseMessage], context_message: HumanMessage) -> list[BaseMessage]:
    for index in reversed(range(len(messages))):
        if isinstance(messages[index], HumanMessage):
            return [*messages[:index], context_message, *messages[index:]]
    return [*messages, context_message]
