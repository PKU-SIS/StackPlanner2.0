"""Record the saved Writer completion without another model call.

Outline granularity and first-draft budgets control routine generation. The
existing whole-report acceptance gate owns any required length revision.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END
from langgraph.types import Command

from deerflow.sp.report import store
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError
from deerflow.sp.report.length import bound_writer_draft, estimate_words, node_budget, writer_generation_budget


class ReportWriterLengthMiddleware(AgentMiddleware):
    """Measure a host-bound completed draft and persist its actual estimate."""

    def __init__(self, *, binding: dict[str, str], output_limit: int = 32768, aborted: Callable[[], bool] | None = None):
        super().__init__()
        self.binding = dict(binding)
        self.paths = store.ReportPaths(Path(binding["outputs_path"]), binding["report_id"])
        self.identity = {key: binding[key] for key in ("node_id", "write_key", "draft_id", "action_id")}
        self.output_limit = output_limit
        self.aborted = aborted or (lambda: False)
        self._stop_reason = None

    def consume_stop_reason(self, run_id=None):
        reason, self._stop_reason = self._stop_reason, None
        return reason

    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime):
        return {"jump_to": "end"} if self._stop_reason else None

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):
        return {"jump_to": "end"} if self._stop_reason else None

    def _submission_error(self, call, payload, *, terminal=False):
        message = ToolMessage(content=json.dumps(payload, ensure_ascii=False), name="write_file", tool_call_id=call["id"], status="error")
        if terminal:
            self._stop_reason = "writer_length_submission_failed"
            return Command(update={"messages": [message]}, goto=END)
        return message

    def _check_submission(self, request):
        call = request.tool_call
        if call.get("name") != "write_file":
            return None
        with store.report_lock(self.paths):
            ledger = ReportLedger.load(self.paths)
            contract = ledger.payload.get("length_contract") or {}
            if not contract.get("writer_submission_limit"):
                return None
            try:
                identity = self.identity
                pending = next(r for r in ledger.payload["write_requests"] if r.get("key") == identity["write_key"])
                if self.aborted() or pending.get("status") != "in_flight" or pending.get("last_action_id") != identity["action_id"] or pending.get("last_draft_id") != identity["draft_id"] or pending.get("node_ids") != [identity["node_id"]]:
                    raise ReportValidationError("writer_not_in_flight")
                attempt = next(a for a in pending["attempts"] if a.get("action_id") == identity["action_id"])
                path = self.paths.draft_file(identity["node_id"], identity["draft_id"])
                args = call.get("args") or {}
                if args.get("path") not in {str(path), self.paths.virtual(path)} or path.is_symlink() or not path.resolve().is_relative_to(self.paths.directory.resolve() / "drafts"):
                    raise ReportValidationError("invalid_draft_path")
                tree_text = store.read_text(Path(ledger.payload["tree"]["path"]))
                if store.sha256_text(tree_text) != ledger.payload["tree"]["sha256"]:
                    raise ReportValidationError("invalid_tree_binding")
                node = next(n for n in json.loads(tree_text)["nodes"] if n["node_id"] == identity["node_id"])
                budget = writer_generation_budget(node, contract) or node_budget(node)
                assigned_max = budget["max_words"]
                tolerance = contract.get("writer_submission_tolerance_percent", 0)
                budget["max_words"] = assigned_max * (100 + tolerance) // 100
                proposals = attempt.setdefault("length_submissions", [])
                if len(proposals) >= 2:
                    return self._submission_error(call, {"error": "submission_limit_exhausted"}, terminal=True)
                content = args.get("content")
                if not isinstance(content, str):
                    raise ReportValidationError("complete_chapter_content_required")
                actual = estimate_words(content)
                valid = bool(content.strip()) and not args.get("append", False) and actual <= budget["max_words"]
                proposals.append({"estimated_words": actual, "assigned_max_words": assigned_max, "max_words": budget["max_words"], "tolerance_percent": tolerance, "sha256": store.sha256_text(content), "within_limit": valid})
                ledger.save()
                if valid:
                    return None
                return self._submission_error(
                    call,
                    {
                        "error": "complete_chapter_exceeds_limit_or_append_used",
                        "estimated_words": actual,
                        **budget,
                        "assigned_max_words": assigned_max,
                        "tolerance_percent": tolerance,
                        "remove_words_to_target": max(0, actual - budget["target_words"]),
                        "remaining_resubmissions": 2 - len(proposals),
                        "instruction": (
                            "Submit the entire complete chapter once more with append=false. Aim at target_words and stay below max_words. "
                            "Shorten explanations and examples within the assigned scope; preserve evidence, necessary qualifications, and the conclusion. "
                            "Do not truncate or submit fragments. Nothing from this rejected proposal was saved."
                        ),
                    },
                    terminal=len(proposals) == 2,
                )
            except (ReportValidationError, OSError, ValueError, KeyError, StopIteration) as exc:
                return self._submission_error(call, {"error": "invalid_writer_submission_binding", "detail": str(exc)}, terminal=True)

    def wrap_tool_call(self, request, handler):
        result = self._check_submission(request)
        return result if result is not None else handler(request)

    async def awrap_tool_call(self, request, handler):
        result = await asyncio.to_thread(self._check_submission, request)
        return result if result is not None else await handler(request)

    def _after(self, state):
        messages = state.get("messages", [])
        last = messages[-1] if messages else None
        if not isinstance(last, AIMessage) or last.tool_calls or last.invalid_tool_calls:
            return None
        if self.aborted() or any(marker in str(last.content) for marker in ("FORCED STOP", "TOKEN BUDGET EXCEEDED")):
            return None
        if last.response_metadata.get("finish_reason") in {"length", "max_tokens"} or int((last.usage_metadata or {}).get("output_tokens", 0)) >= self.output_limit:
            return None
        with store.report_lock(self.paths):
            try:
                ledger = ReportLedger.load(self.paths)
                attempt, _, measurement = bound_writer_draft(ledger, **self.identity)
            except (ReportValidationError, OSError, ValueError, KeyError, StopIteration):
                return None  # Native Writer acceptance handles invalid drafts.
            attempt["length_completion_measurement"] = measurement
            ledger.save()
        return None

    def after_model(self, state, runtime):
        return self._after(state)

    async def aafter_model(self, state, runtime):
        return await asyncio.to_thread(self._after, state)
