"""SP subagent delegation adapters."""

from deerflow.sp.subagents.adapter import (
    A2A_DELEGATE_MESSAGE,
    A2A_PROGRESS_MESSAGE,
    A2A_PROTOCOL_VERSION,
    A2A_RESULT_MESSAGE,
    SPSubagentExecutorProtocol,
    SPSubagentResult,
    SPSubagentStatus,
    SPSubagentTask,
)
from deerflow.sp.subagents.dr2_adapter import DR2SubagentExecutorAdapter, DR2SubagentExecutorLike, normalize_dr2_subagent_result, render_sp_subagent_prompt

__all__ = [
    "DR2SubagentExecutorAdapter",
    "DR2SubagentExecutorLike",
    "A2A_DELEGATE_MESSAGE",
    "A2A_PROGRESS_MESSAGE",
    "A2A_PROTOCOL_VERSION",
    "A2A_RESULT_MESSAGE",
    "SPSubagentExecutorProtocol",
    "SPSubagentResult",
    "SPSubagentStatus",
    "SPSubagentTask",
    "normalize_dr2_subagent_result",
    "render_sp_subagent_prompt",
]
