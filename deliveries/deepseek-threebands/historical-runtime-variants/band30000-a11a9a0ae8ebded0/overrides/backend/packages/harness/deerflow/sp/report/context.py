"""Central control-context middleware for active report runs.

The ledger remains the only persisted business state.  This middleware derives
a request-only control summary immediately before every Central model call, so
first calls, HITL continuations, new runs, and post-compression calls all see
the current gap/dispatch state without a tool round-trip.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from threading import Event
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse, hook_config
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from deerflow.agents.thread_state import ThreadState
from deerflow.sp.report.hooks import REPORT_CONTEXT_MESSAGE_NAME, control_context, sync_user_inputs


class SPReportContextMiddleware(AgentMiddleware[ThreadState]):
    """Inject report control state and synchronize new user inputs."""

    state_schema = ThreadState

    def __init__(self, *, executor_provider=None, max_writers=3):
        super().__init__()
        self.executor_provider = executor_provider
        self.max_writers = max(1, int(max_writers))

    def _after_writing(self, state, update, runtime=None):
        from deerflow.sp.report.acceptance import next_acceptance_action
        from deerflow.sp.report.hooks import report_context

        context = getattr(runtime, "context", None)
        abort = context.get("__run_abort_event") if isinstance(context, Mapping) else None
        if report_context(state).is_active and abort is not None and abort.is_set():
            return {**(update or {}), "jump_to": "end"}
        recovery = _writer_failure_action({**state, **(update or {})}, context.get("run_id") if isinstance(context, Mapping) else None)
        if recovery is not None:
            return {**(update or {}), "jump_to": "tools", "messages": [recovery]}
        action = next_acceptance_action({**state, **(update or {})})
        if action is None:
            return update
        return {**(update or {}), "jump_to": "tools", "messages": [action]}

    @override
    def before_agent(self, state: ThreadState, runtime: Any) -> dict[str, Any] | None:
        del runtime
        sync_user_inputs(state)
        return None

    @override
    async def abefore_agent(self, state: ThreadState, runtime: Any) -> dict[str, Any] | None:
        return self.before_agent(state, runtime)

    @hook_config(can_jump_to=["tools", "end"])
    def before_model(self, state: ThreadState, runtime: Any) -> dict[str, Any] | None:
        """Drain ready writing, then hand off through native fixed acceptance."""
        from deerflow.sp.report.writer_pipeline import run_writer_pipeline

        update = run_writer_pipeline(state, executor_provider=self.executor_provider, runtime=runtime, max_workers=self.max_writers) if self.executor_provider is not None else None
        return self._after_writing(state, update, runtime)

    @hook_config(can_jump_to=["tools", "end"])
    async def abefore_model(self, state: ThreadState, runtime: Any) -> dict[str, Any] | None:
        if self.executor_provider is None:
            return self._after_writing(state, None, runtime)
        from deerflow.sp.report.writer_pipeline import run_writer_pipeline

        stop = Event()
        task = asyncio.create_task(asyncio.to_thread(run_writer_pipeline, state, executor_provider=self.executor_provider, runtime=runtime, max_workers=self.max_writers, stop_event=stop))
        try:
            update = await asyncio.shield(task)
        except asyncio.CancelledError:
            stop.set()
            # The existing executor cancellation guard sees the shared event;
            # queued nodes never start after this run has been cancelled.
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except (TimeoutError, asyncio.CancelledError):
                pass
            raise
        return self._after_writing(state, update, runtime)

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


def _writer_failure_action(state: Mapping[str, Any], run_id: str | None) -> AIMessage | None:
    """Repair managed Writer failures once, then ask the user if still blocked.

    This host-owned handoff runs at the existing report boundary. It neither
    retries a failed write key nor changes provider, tool, token, or loop caps.
    """
    from deerflow.sp.report import store
    from deerflow.sp.report.hooks import report_context
    from deerflow.sp.report.ledger import ReportLedger

    report = report_context(state)
    if not report.is_active or not report.outputs_path or not report.report_id:
        return None
    paths = store.ReportPaths(report.outputs_path, report.report_id)
    with store.report_lock(paths):
        try:
            ledger = ReportLedger.load(paths)
        except (FileNotFoundError, ValueError, OSError):
            return None
        if ledger.payload.get("delivered") or ledger.payload.get("status") in {"abandoned", "closed"}:
            return None
        requests = [item for item in ledger.payload.get("write_requests") or [] if isinstance(item, Mapping)]
        failed = [item for item in requests if str(item.get("status") or "") == "technical_failed"]
        if not failed or any(str(item.get("status") or "") == "in_flight" for item in requests):
            return None
        summary = ledger.control_summary()
        if summary.get("gaps") or summary.get("unapplied_input_ids") or summary.get("research_actions_in_flight") or summary.get("draft_review_due") or summary.get("feedback_open"):
            return None

        failure_keys = sorted(str(item.get("key") or "") for item in failed)
        failure_nodes = sorted({str(node) for item in failed for node in item.get("node_ids") or []})
        if not failure_keys or not failure_nodes:
            return None
        marker = ledger.payload.get("writer_recovery")
        if not isinstance(marker, dict) or str(marker.get("run_id") or "") != str(run_id or ""):
            marker = {"run_id": str(run_id or ""), "attempts": 0}
            ledger.payload["writer_recovery"] = marker
        attempts = int(marker.get("attempts") or 0)
        identity = hashlib.sha256((str(run_id or "") + "\0" + "\0".join(failure_keys)).encode()).hexdigest()[:20]
        if attempts == 0:
            action_id = f"spact-writer-repair-{identity}"
            marker.update(attempts=1, action_id=action_id, failure_keys=failure_keys, node_ids=failure_nodes, status="outline_repair_requested")
            ledger.save()
            args = {
                "target_agent": "outline",
                "reason": "Bounded recovery for technical Writer failures in the managed report pipeline",
                "task": (
                    "Repair the failed report writing plan for exactly these nodes: " + ", ".join(failure_nodes) + ". "
                    "Use the current accepted tree, saved materials, current drafts, and failed write requests in report_packet. "
                    "Preserve every unaffected node and accepted draft. For each failed node, issue a valid new write request "
                    "with a new key/round after checking its evidence locators and node word budget. Do not reuse the failed "
                    "key, add an automatic research round, or perform integration review. If no safe repair is possible, "
                    "return the unchanged accepted tree and explain the concrete blocker in the normal Outline result."
                ),
                "stage": "planning",
                "expected_output": "A valid Outline revision with new write requests only for the failed nodes, or a clear blocker.",
                "metadata": {"report_task": "revise", "report_writer_recovery": True, "report_writer_failure_keys": failure_keys},
            }
            return AIMessage(
                content="",
                additional_kwargs={"stackplanner_generated": True},
                tool_calls=[{"name": "sp_delegate", "args": args, "id": action_id, "type": "tool_call"}],
            )
        if attempts == 1:
            action_id = f"spact-writer-blocked-{identity}"
            marker.update(attempts=2, status="blocked_user", pending_action_id=action_id)
            ledger.save()
            args = {
                "question": (
                    "报告的章节写作在一次受限自动修复后仍未完成。失败章节："
                    + ", ".join(failure_nodes)
                    + "。已保留其草稿和诊断记录；其他章节及研究材料也已保留。请选择：接受当前不完整材料作为阶段稿、稍后重试失败章节，或调整这些章节的范围/补充材料。"
                ),
                "reason": "Managed report has unresolved technical Writer failures after one bounded Outline repair",
                "interaction_type": "choice",
                "options": ["稍后重试失败章节", "调整章节范围或补充材料", "接受当前阶段稿"],
                "metadata": {"report_writer_recovery": True, "report_writer_failure_keys": failure_keys},
            }
            return AIMessage(
                content="",
                additional_kwargs={"stackplanner_generated": True},
                tool_calls=[{"name": "sp_ask_human", "args": args, "id": action_id, "type": "tool_call"}],
            )
        return None


def _insert_before_last_human(messages: list[BaseMessage], context_message: HumanMessage) -> list[BaseMessage]:
    for index in reversed(range(len(messages))):
        if isinstance(messages[index], HumanMessage):
            return [*messages[:index], context_message, *messages[index:]]
    return [*messages, context_message]
