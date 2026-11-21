"""One opt-in, same-run correction for a failed or missing structured submission."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from deerflow.config.token_budget_config import TokenBudgetConfig

_REPAIR = (
    "[HOST TOOL ARGUMENT REPAIR] The previous report_submit_outline call never executed because its arguments were invalid JSON. "
    "Correct only that submission once. Preserve all requirements, nodes, evidence, locators and gaps; do not re-plan, shorten the tree, "
    "read materials, search, or register new research. Return exactly one report_submit_outline tool call with valid JSON arguments. "
    "The host will validate it normally; do not claim a file was written before the tool succeeds."
)
_MISSING_SUBMISSION = (
    "[HOST MISSING OUTLINE SUBMISSION] This structured consolidation has not submitted a validated outline. "
    "Your completed response did not deliver a tree. Continue this SAME task once, using the accepted tree, "
    "returned research and exact evidence already in this conversation. Do not restart planning, read materials, "
    "search, or register new research. Submit the complete consolidated tree with exactly one report_submit_outline "
    "tool call, preserving requirements, nodes, evidence, locators and required gap outcomes. The host will perform "
    "all normal validation; do not claim a file exists or a gap is resolved before validation succeeds. "
    "Then return the normal compact file-delivery envelope using the tool's actual returned tree_path."
)
_SOURCE_FIX = (
    "[HOST SOURCE CORRECTION] Correct the reported material/source identifiers once, using the existing catalog and candidate. "
    "No reads, searches or re-planning. Preserve nodes and useful content; never invent material IDs or line ranges. "
    "Submit one complete report_submit_outline call. A second identifier failure ends this correction; do not repeat it."
)
_FINISH = "[HOST SUBMISSION ACK] No further tools are available. Return the compact normal file-delivery JSON envelope using the actual submission result. Do not re-plan or collect evidence."
_SCHEMA_FIX = (
    "[HOST SUBMISSION VALIDATION] The corrective submission reached the normal tool, which returned tree validation errors. "
    "For a budget-only error with candidate_budget_patch feedback, use report_patch_outline_budget with the offered hashes and node_budgets. "
    "Otherwise correct only the reported fields and resubmit report_submit_outline. Preserve node IDs, requirements, facts and evidence. "
    "Do not re-plan, read materials, search, register research, or invent sources/locators. Existing run budgets still apply. "
    "After validation succeeds, return the compact file-delivery envelope."
)


class InvalidToolCallRepairMiddleware(AgentMiddleware):
    """Keep original invalid calls in state; sanitize only provider-bound copies.

    A jump re-enters the existing model node, so usage, graph steps, cancellation
    and guard middleware stay in the same run. No parser guesses or tool replay.
    """

    def __init__(self, *, budget: TokenBudgetConfig | None = None, output_limit: int = 32768, tool_limit: int = 50, aborted: Callable[[], bool] | None = None, bound_source_correction: bool = False, require_outline_submission: bool = False):
        super().__init__()
        self.budget = budget
        self.output_limit = output_limit
        self.tool_limit = tool_limit
        self.aborted = aborted or (lambda: False)
        self.bound_source_correction = bound_source_correction
        self.require_outline_submission = require_outline_submission
        self.validated_outline_submission = False
        self.repair_kind = "invalid_arguments"
        self.repair_instruction = _REPAIR
        self.source_corrections = 0
        self.source_fix = False
        self.used = False
        self.pending = False
        self.correcting = False
        self.finish_only = False
        self.schema_fix = False
        self.candidate_budget_patch = False
        self.failed = False
        self.events: list[dict[str, Any]] = []

    def _budget_allows(self, messages, last):
        if sum(isinstance(item, ToolMessage) for item in messages) >= self.tool_limit:
            return False
        if not self.budget or not self.budget.enabled:
            return True
        usage = {key: sum(int((item.usage_metadata or {}).get(key, 0)) for item in messages if isinstance(item, AIMessage)) for key in ("input_tokens", "output_tokens", "total_tokens")}
        last_usage = last.usage_metadata or {}
        # Reserve one full corrective completion and the previous context plus
        # its failed output. This is conservative; never reset the guard budget.
        next_input = int(last_usage.get("input_tokens", 0)) + int(last_usage.get("output_tokens", 0)) + 2048
        limits = [("total_tokens", self.budget.max_tokens, 2 * next_input + 3 * self.output_limit)]
        if self.budget.max_input_tokens:
            limits.append(("input_tokens", self.budget.max_input_tokens, 2 * next_input + self.output_limit))
        if self.budget.max_output_tokens:
            limits.append(("output_tokens", self.budget.max_output_tokens, 2 * self.output_limit))
        return all(usage[key] + reserve < limit * self.budget.hard_stop_threshold for key, limit, reserve in limits)

    def _failed_reply(self, last, reason):
        self.failed = True
        self.finish_only = True
        self.events.append({"status": reason})
        if not last.tool_calls:
            return None
        # A correction is not allowed to trigger any additional business tools.
        extra = {key: value for key, value in last.additional_kwargs.items() if key not in {"tool_calls", "function_call"}}
        blocked = last.model_copy(update={"content": "[TOOL ARGUMENT REPAIR FAILED] " + reason, "tool_calls": [], "additional_kwargs": extra})
        return {"messages": [blocked]}

    def _after(self, state):
        messages = state.get("messages", [])
        last = messages[-1] if messages else None
        if not isinstance(last, AIMessage):
            return None
        if "FORCED STOP" in str(last.content) or "TOKEN BUDGET EXCEEDED" in str(last.content):
            self.correcting = False
            self.events.append({"status": "stopped_existing_guard"})
            return None
        if self.correcting:
            self.correcting = False
            self.finish_only = True
            if last.invalid_tool_calls or len(last.tool_calls) != 1 or last.tool_calls[0]["name"] != "report_submit_outline":
                return self._failed_reply(last, "invalid_correction")
            self.events.append({"status": "valid_call_generated"})
            return None
        if self.finish_only and last.tool_calls:
            return self._failed_reply(last, "unexpected_tool_after_correction")
        if self.schema_fix and self.used:
            if last.invalid_tool_calls:
                return self._failed_reply(last, "invalid_arguments_after_correction")
            allowed = {"report_submit_outline"} | ({"report_patch_outline_budget"} if self.candidate_budget_patch else set())
            if last.tool_calls and (len(last.tool_calls) != 1 or last.tool_calls[0]["name"] not in allowed):
                return self._failed_reply(last, "unexpected_tool_during_validation")
            return None
        if self.used or last.tool_calls:
            return None
        missing = self.require_outline_submission and not self.validated_outline_submission and not last.invalid_tool_calls
        if missing:
            try:
                final = json.loads(last.content)
            except (TypeError, ValueError):
                final = None
            # Real failure/HITL/guard envelopes retain their ordinary handling.
            # A generic non-delivery reply (as in the observed proceed/notes
            # response) is a premature completion, not evidence of a saved tree.
            if isinstance(final, dict) and (final.get("status") not in (None, "completed") or final.get("stop_reason")):
                return None
        if missing:
            invalid = None
        elif len(last.invalid_tool_calls) == 1:
            invalid = last.invalid_tool_calls[0]
            if invalid.get("name") != "report_submit_outline" or not invalid.get("args"):
                return None
        else:
            return None
        if self.aborted():
            return None
        finish = last.response_metadata.get("finish_reason")
        if finish in {"length", "max_tokens"} or int((last.usage_metadata or {}).get("output_tokens", 0)) >= self.output_limit:
            self.events.append({"status": "skipped_output_limit"})
            return None
        if "FORCED STOP" in str(last.content) or not self._budget_allows(messages, last):
            self.events.append({"status": "skipped_budget"})
            return None
        event = {"status": "correction_requested", "tool": "report_submit_outline"}
        if missing:
            self.repair_kind = "missing_outline_submission"
            self.repair_instruction = _MISSING_SUBMISSION
            event["reason"] = self.repair_kind
        else:
            event["tool_call_id"] = invalid.get("id")
            try:
                json.loads(invalid["args"])
            except json.JSONDecodeError as exc:
                event.update(error=exc.msg, line=exc.lineno, column=exc.colno, position=exc.pos)
            except (TypeError, ValueError):
                event["error"] = "Arguments must be one valid JSON object"
        self.events.append(event)
        self.used = self.pending = True
        diagnostic = {key: value for key, value in event.items() if key not in {"status", "tool_call_id"}}
        return {"messages": [HumanMessage(content=self.repair_instruction + "\nHost diagnostic: " + json.dumps(diagnostic, ensure_ascii=False))], "jump_to": "model"}

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime):
        return self._after(state)

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime):
        return self._after(state)

    def prepare_request(self, request):
        if not self.used:
            return request
        messages = []
        for message in request.messages:
            if isinstance(message, AIMessage) and message.invalid_tool_calls and not message.tool_calls:
                # Invalid calls are never sent back as executable provider tool
                # messages. Keep the exact failed proposal as assistant text;
                # the unmodified original remains in the run's captured state.
                raw = "\n\n".join(str(item.get("args") or "") for item in message.invalid_tool_calls)
                extra = {key: value for key, value in message.additional_kwargs.items() if key not in {"tool_calls", "function_call"}}
                message = message.model_copy(update={"content": "[Unexecuted invalid tool arguments]\n" + raw, "invalid_tool_calls": [], "additional_kwargs": extra})
            messages.append(message)
        instruction = self.repair_instruction if self.pending else _SOURCE_FIX if self.source_fix else _SCHEMA_FIX if self.schema_fix else _FINISH
        if messages and isinstance(messages[0], SystemMessage):
            messages[0] = messages[0].model_copy(update={"content": str(messages[0].content) + "\n\n" + instruction})
        else:
            messages.insert(0, SystemMessage(content=instruction))
        allowed = {"report_submit_outline"}
        if self.schema_fix and not self.pending and self.candidate_budget_patch:
            allowed.add("report_patch_outline_budget")
        tools = [item for item in request.tools or [] if (item.get("name") if isinstance(item, dict) else getattr(item, "name", "")) in allowed] if self.pending or self.schema_fix else []
        if self.pending:
            self.pending = False
            self.correcting = True
        return request.override(messages=messages, tools=tools)

    def wrap_model_call(self, request, handler):
        return handler(self.prepare_request(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self.prepare_request(request))

    def _submission_result(self, request, response):
        if request.tool_call.get("name") not in {"report_submit_outline", "report_patch_outline_budget"}:
            return response
        try:
            payload = json.loads(response.content)
        except (ValueError, TypeError, AttributeError):
            return response
        self.candidate_budget_patch = isinstance(payload, dict) and isinstance(payload.get("candidate_budget_patch"), dict)
        if isinstance(payload, dict) and payload.get("saved") is True and payload.get("validation_ok") is True:
            self.validated_outline_submission = True
        if (self.bound_source_correction or request.tool_call.get("name") == "report_patch_outline_budget") and isinstance(payload, dict) and payload.get("validation_ok") is True:
            self.used = self.finish_only = True
            self.source_fix = self.schema_fix = self.pending = False
            self.events.append({"status": "submission_validated", "validation_ok": True})
            return response
        source_error = isinstance(payload, dict) and payload.get("error") == "invalid_outline_tree" and any(marker in str(payload.get("detail", "")) for marker in ("evidence source_ids", "unknown material", ".source_ids"))
        if self.bound_source_correction and source_error:
            self.used = True
            if self.source_corrections >= 1:
                self.failed = self.finish_only = True
                self.source_fix = self.schema_fix = self.pending = False
                self.events.append({"status": "source_correction_exhausted"})
            else:
                self.source_corrections += 1
                self.source_fix = self.schema_fix = True
                self.finish_only = False
                self.events.append({"status": "source_correction_requested"})
            return response
        if not self.used:
            return response
        self.source_fix = False
        self.schema_fix = isinstance(payload, dict) and payload.get("error") == "invalid_outline_tree"
        self.finish_only = not self.schema_fix
        if self.schema_fix:
            self.events.append({"status": "normal_schema_feedback", "detail": str(payload.get("detail", ""))[:1000]})
        else:
            self.events.append({"status": "submission_validated", "validation_ok": payload.get("validation_ok") if isinstance(payload, dict) else None})
        return response

    def wrap_tool_call(self, request, handler):
        return self._submission_result(request, handler(request))

    async def awrap_tool_call(self, request, handler):
        return self._submission_result(request, await handler(request))

    def consume_stop_reason(self, run_id=None):
        reason = ("outline_delivery_repair_failed" if self.repair_kind == "missing_outline_submission" else "tool_argument_repair_failed") if self.failed else None
        self.failed = False
        return reason
