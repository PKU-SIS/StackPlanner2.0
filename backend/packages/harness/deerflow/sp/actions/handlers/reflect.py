"""REFLECT action handler."""

from __future__ import annotations

from deerflow.sp.actions.events import make_sp_event
from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.schema import HandlerResult, SPAction


class ReflectHandler:
    def handle(self, action: SPAction, context: HandlerContext) -> HandlerResult:
        failure_note = action.metadata.get("failure_note") or action.reason
        target_entry_ids = [str(entry_id) for entry_id in action.metadata.get("target_entry_ids", [])]
        active_target_entry_ids = target_entry_ids
        inactive_target_entry_ids: list[str] = []
        missing_target_entry_ids: list[str] = []
        if target_entry_ids:
            by_id = {entry.id: entry for entry in context.stack.entries}
            missing_target_entry_ids = [entry_id for entry_id in target_entry_ids if entry_id not in by_id]
            # A model can occasionally reproduce a stale opaque ID after the
            # stack has been compacted or after a prior action rewrote the
            # context. Treat that as a failed target resolution, not as a
            # recoverable exception that can trap CentralAgent in a retry loop.
            # The reflection is still recorded, but no memory entry is
            # backtracked unless every requested target resolves safely.
            if missing_target_entry_ids:
                active_target_entry_ids = []
            protected = [entry_id for entry_id in target_entry_ids if entry_id in by_id and (by_id[entry_id].status == "pinned" or by_id[entry_id].priority == "critical")]
            if protected:
                raise ValueError(f"REFLECT cannot backtrack pinned or critical memory: {', '.join(protected)}")
            human_authored = [entry_id for entry_id in target_entry_ids if entry_id in by_id and (by_id[entry_id].actor == "human" or by_id[entry_id].action in {"user_request", "feedback"})]
            if human_authored:
                raise ValueError("REFLECT cannot backtrack human-authored task memory: " + ", ".join(human_authored))
            inactive_target_entry_ids = [entry_id for entry_id in target_entry_ids if entry_id in by_id and by_id[entry_id].status != "active"]
            if not missing_target_entry_ids:
                active_target_entry_ids = [entry_id for entry_id in target_entry_ids if by_id[entry_id].status == "active"]

        reflection = context.stack.append_reflect(
            action.task or action.reason,
            thread_id=context.thread_id,
            run_id=context.run_id,
            stage=action.stage,
            priority=action.priority,
            failure_note=str(failure_note),
            promotion_candidate=bool(action.metadata.get("promotion_candidate", False)),
            metadata={
                "action_id": action.action_id,
                **action.metadata,
                **(
                    {
                        "missing_target_entry_ids": missing_target_entry_ids,
                        "backtrack_applied": False,
                    }
                    if missing_target_entry_ids
                    else {}
                ),
                **({"skipped_inactive_target_ids": inactive_target_entry_ids} if inactive_target_entry_ids else {}),
            },
        )
        state_update = {}
        memory_entries = [reflection]
        events = [
            *(
                [
                    make_sp_event(
                        "sp.reflect.targets_already_inactive",
                        action_id=action.action_id,
                        run_id=context.run_id,
                        target_type="entry",
                        source_entry_ids=inactive_target_entry_ids,
                    )
                ]
                if inactive_target_entry_ids
                else []
            ),
            *(
                [
                    make_sp_event(
                        "sp.reflect.targets_missing",
                        action_id=action.action_id,
                        run_id=context.run_id,
                        target_type="entry",
                        source_entry_ids=missing_target_entry_ids,
                        backtrack_applied=False,
                    )
                ]
                if missing_target_entry_ids
                else []
            ),
        ]
        if active_target_entry_ids:
            backtrack = context.stack.mark_backtracked(
                active_target_entry_ids,
                str(failure_note),
                thread_id=context.thread_id,
                run_id=context.run_id,
                stage=action.stage,
                priority=action.priority,
                metadata={
                    "action_id": action.action_id,
                    "trigger": "reflect",
                    "rollback_scope": "memory_only",
                },
            )
            memory_entries.append(backtrack)
            state_update["sp_last_run_summary"] = str(failure_note)
            events.append(
                make_sp_event(
                    "sp.memory.backtracked",
                    action_id=action.action_id,
                    run_id=context.run_id,
                    target_type="entry",
                    target_id=active_target_entry_ids[0],
                    source_entry_ids=active_target_entry_ids,
                    triggered_by="reflect",
                )
            )
        if action.stage:
            state_update["sp_current_stage"] = action.stage
        return HandlerResult(
            next_step="continue",
            state_update=state_update,
            memory_entries=memory_entries,
            idempotency_key=action.idempotency_key,
            run_events=events,
        )
