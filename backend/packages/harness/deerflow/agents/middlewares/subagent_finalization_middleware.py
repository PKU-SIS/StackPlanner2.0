"""Opt-in tool-free finalization for staged research evaluations."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

_FINALIZE = """
The evidence-collection phase is now closed. No further tools are available.
Return your normal final JSON envelope now. For research, artifact_content must
be the complete theme-organized Markdown synthesis with canonical [S<n>]
citations to saved materials, source URLs and useful evidence locators. Preserve
the useful information already collected. Do not return tool transcripts,
FORCED STOP markers, a mere source catalog, or a promise to continue later.
Use only the evidence visible in this conversation; state real limitations
without inventing facts, sources or identities. Keep the summary field compact.
""".strip()


class SubagentFinalizationMiddleware(AgentMiddleware):
    """Close collection before hard limits and let the model finish normally.

    Enabled only by an explicit SubagentConfig option. It adds no persisted
    report state, no evaluator model and no extra delegation.
    """

    def __init__(self, *, after_tool_calls: int, token_limit: int | None = None):
        super().__init__()
        self.after_tool_calls = after_tool_calls
        self.token_limit = token_limit
        self.trigger_reason: str | None = None

    def _prepare(self, request: Any) -> Any:
        messages = list(request.messages)
        tools_used = sum(isinstance(message, ToolMessage) for message in messages)
        tokens_used = sum(int((message.usage_metadata or {}).get("total_tokens", 0)) for message in messages if isinstance(message, AIMessage))
        reason = "tool_calls" if tools_used >= self.after_tool_calls else "token_reserve" if self.token_limit and tokens_used >= self.token_limit * 0.7 else None
        if reason is None or not request.tools:
            return request
        self.trigger_reason = self.trigger_reason or reason
        if messages and isinstance(messages[0], SystemMessage):
            messages[0] = SystemMessage(content=f"{messages[0].content}\n\n{_FINALIZE}")
        else:
            messages.insert(0, SystemMessage(content=_FINALIZE))
        return request.override(messages=messages, tools=[])

    def wrap_model_call(self, request, handler):
        return handler(self._prepare(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._prepare(request))
