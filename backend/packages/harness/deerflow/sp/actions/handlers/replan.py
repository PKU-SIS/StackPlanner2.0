"""REPLAN action handler."""

from __future__ import annotations

import json

from deerflow.sp.actions.events import make_sp_event
from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.schema import HandlerResult, SPAction


class ReplanHandler:
    def handle(self, action: SPAction, context: HandlerContext) -> HandlerResult:
        report_action = str(action.metadata.get("report_action") or "").strip().lower()
        if report_action:
            return self._handle_report_lifecycle(action, context, report_action)
        entry = context.stack.append_replan(
            action.task or action.reason,
            thread_id=context.thread_id,
            run_id=context.run_id,
            stage=action.stage,
            priority=action.priority,
            metadata={"action_id": action.action_id, **action.metadata},
        )
        state_update = {"sp_active_delegate_id": None}
        if action.stage:
            state_update["sp_current_stage"] = action.stage
        return HandlerResult(next_step="continue", state_update=state_update, memory_entries=[entry], idempotency_key=action.idempotency_key)

    def _handle_report_lifecycle(self, action: SPAction, context: HandlerContext, report_action: str) -> HandlerResult:
        from deerflow.sp.report import close_report, resume_report, start_report
        from deerflow.sp.report.ledger import ReportValidationError

        try:
            if report_action == "start":
                result = start_report(context, action)
                stage = "planning"
                event_type = "sp.report.started"
            elif report_action == "resume":
                result = resume_report(context, action)
                stage = "planning"
                event_type = "sp.report.resumed"
            elif report_action == "close":
                reason = str(action.metadata.get("report_close_reason") or action.reason or "").strip()
                result = close_report(context, action, reason=reason)
                stage = "finished"
                event_type = "sp.report.closed"
            else:
                raise ReportValidationError("metadata.report_action must be start, resume, or close")
        except ReportValidationError as exc:
            return HandlerResult(
                next_step="error_recoverable",
                state_update={"sp_active_delegate_id": None},
                idempotency_key=action.idempotency_key,
                error=f"Report lifecycle action rejected: {exc}",
                run_events=[make_sp_event("sp.report.lifecycle_rejected", action_id=action.action_id, run_id=context.run_id, report_action=report_action, reason=str(exc))],
            )

        entry = context.stack.append_replan(
            json.dumps(result, ensure_ascii=False, sort_keys=True),
            thread_id=context.thread_id,
            run_id=context.run_id,
            stage=stage,
            priority=action.priority,
            metadata={
                "action_id": action.action_id,
                "report_action": report_action,
                "report_id": result.get("report_id"),
                **dict(action.metadata),
            },
        )
        return HandlerResult(
            next_step="continue",
            state_update={"sp_active_delegate_id": None, "sp_current_stage": stage},
            memory_entries=[entry],
            idempotency_key=action.idempotency_key,
            run_events=[
                make_sp_event(
                    event_type,
                    action_id=action.action_id,
                    run_id=context.run_id,
                    report_id=result.get("report_id"),
                    report_action=report_action,
                )
            ],
        )
