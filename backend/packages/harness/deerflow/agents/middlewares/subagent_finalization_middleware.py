"""Opt-in budget-aware finalization for scoped subagents."""

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

    def __init__(self, *, after_tool_calls: int, token_limit: int | None = None, output_format: str = "json", keep_tools: list[str] | None = None, token_fraction: float = 0.7):
        super().__init__()
        self.after_tool_calls = after_tool_calls
        self.token_limit = token_limit
        self.trigger_reason: str | None = None
        self.output_format = output_format
        self.keep_tools = keep_tools
        self.token_fraction = token_fraction

    def _prepare(self, request: Any) -> Any:
        messages = list(request.messages)
        tools_used = sum(isinstance(message, ToolMessage) for message in messages)
        tokens_used = sum(int((message.usage_metadata or {}).get("total_tokens", 0)) for message in messages if isinstance(message, AIMessage))
        reason = "tool_calls" if tools_used >= self.after_tool_calls else "token_reserve" if self.token_limit and tokens_used >= self.token_limit * self.token_fraction else None
        if reason is None or not request.tools:
            return request
        self.trigger_reason = self.trigger_reason or reason
        instruction = _FINALIZE
        selected_tools = []
        if self.keep_tools is not None:
            selected_tools = [item for item in request.tools if (item.get("name") if isinstance(item, dict) else getattr(item, "name", "")) in self.keep_tools]
            instruction = (
                "Evidence collection is closed to reserve enough budget for delivery. Do not open new material reads or searches. "
                "Use the evidence and synthesis already available. Prefer report_submit_outline with the complete tree object now. "
                "Alternatively write the complete tree JSON at the host-assigned delivery path. "
                "Read the current delivery file before each modification; append chunks if needed. Preserve planned user requirements. "
                "Reuse registered gap IDs; register only outstanding needs for the fixed supplementary round. "
                "Return the short normal JSON envelope with delivery_mode=file and the tree_path after the file is complete. "
                "Do not paste a full tree into the final envelope or promise to finish later."
            )
        if self.output_format == "markdown":
            from deerflow.sp.report.research_delivery import MARKDOWN_OUTPUT_INSTRUCTION

            instruction = "Evidence collection is closed. No further tools are available.\n" + MARKDOWN_OUTPUT_INSTRUCTION
        if messages and isinstance(messages[0], SystemMessage):
            messages[0] = SystemMessage(content=f"{messages[0].content}\n\n{instruction}")
        else:
            messages.insert(0, SystemMessage(content=instruction))
        return request.override(messages=messages, tools=selected_tools)

    def wrap_model_call(self, request, handler):
        return handler(self._prepare(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._prepare(request))
