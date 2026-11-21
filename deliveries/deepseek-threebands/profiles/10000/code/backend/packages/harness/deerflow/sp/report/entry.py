"""Route explicit user length contracts through the existing report entry."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from langchain_core.messages import AIMessage

from deerflow.sp.report.hooks import _message_id, _visible_human_messages, report_context
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError
from deerflow.sp.report.length import normalize_contract


def explicit_report_entry_action(state: Mapping[str, Any], runtime: Any) -> AIMessage | None:
    """Use native tools, without a Central classification call or direct writes.

    Only the latest visible user turn can opt in. An older report's contract
    must never convert a later ordinary question into another report.
    """
    messages = _visible_human_messages(state)
    if not messages:
        return None
    message = messages[-1]
    if "report_length" not in message.additional_kwargs:
        return None
    runtime_context = getattr(runtime, "context", None)
    runtime_context = runtime_context if isinstance(runtime_context, Mapping) else {}
    abort = runtime_context.get("__run_abort_event")
    if abort is not None and abort.is_set():
        return None
    contract = normalize_contract(message.additional_kwargs["report_length"])
    if contract is None:
        return None
    report = report_context(state)
    if report.status == "error":
        raise ReportValidationError(f"report entry cannot use an unavailable ledger: {report.error}")
    if report.is_active:
        return None
    if report.is_enabled:
        from deerflow.sp.report.store import ReportPaths

        ledger = ReportLedger.load(ReportPaths(report.outputs_path, report.report_id))
        if any(item.get("source_message_id") == _message_id(message) for item in ledger.inputs()):
            return None

    run_id = runtime_context.get("run_id")
    identity = hashlib.sha256(f"{run_id or ''}\0{_message_id(message)}".encode()).hexdigest()[:20]
    memory = state.get("sp_task_memory")
    entries = memory.get("entries", []) if isinstance(memory, Mapping) else []
    recalled = any(isinstance(item, Mapping) and item.get("action") == "recall_memory" and item.get("run_id") == run_id for item in entries)
    if state.get("sp_new_conversation") and not recalled:
        name = "sp_recall_memory"
        action_id = f"report-entry-recall-{identity}"
        args = {"query": str(message.content), "reason": "Preserve the native new-conversation memory preflight before explicit report entry", "stage": "perception"}
    else:
        name = "sp_delegate"
        action_id = f"report-entry-start-{identity}"
        # DelegateHandler owns report creation, contract binding, research
        # packets, tool policy and evidence capture exactly as in native entry.
        from deerflow.utils.messages import message_to_text

        args = {
            "target_agent": "researcher",
            "task": message_to_text(message),
            "reason": "The current user explicitly supplied a report length contract",
            "stage": "research",
            "expected_output": "Broad source discovery and a detailed cited research synthesis for the native report Outline",
            "metadata": {"report_action": "start"},
        }
    last = state.get("sp_last_handler_result")
    if isinstance(last, Mapping) and last.get("action_id") in {action_id, f"spact_{action_id}"}:
        raise ReportValidationError("native report entry did not advance; refusing an automatic entry retry")
    return AIMessage(content="", additional_kwargs={"stackplanner_generated": True}, tool_calls=[{"name": name, "args": args, "id": action_id, "type": "tool_call"}])
