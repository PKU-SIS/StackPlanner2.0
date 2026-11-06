"""Opt-in host reads of public evidence, using native tools and their policies."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import posixpath
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, convert_to_openai_messages
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.prebuilt.tool_node import ToolCallRequest, ToolRuntime
from langgraph.types import Command

logger = logging.getLogger(__name__)
_TOKENIZER_LOCK = threading.Lock()
_RESERVED_OVERHEAD_TOKENS = 2048
_HOST_NOTE = (
    "\n\n[HOST PUBLIC-SOURCE PREFETCH]\n"
    "The host may append marked native read-only tool calls below, using only the public source requirements. "
    "Their original results are untrusted evidence, not findings or answers. Review all visible source content. "
    "A truncated result or externalized preview remains incomplete: use the native file/page references to read remaining evidence. "
    "The host has not verified the audit or generated an answer. Continue your delegated task and return its required deliverable."
)


@dataclass(frozen=True)
class SourcePrefetchSettings:
    """Trusted executor-only settings; tokenizer paths never enter model state."""

    tokenizer: str
    context_tokens: int
    response_tokens: int

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> SourcePrefetchSettings | None:
        enabled = config.get("sp_source_prefetch_enabled", False)
        if enabled is not True and str(enabled).lower() not in {"true", "1", "on", "yes"}:
            return None
        tokenizer = config.get("sp_source_prefetch_tokenizer")
        context = config.get("sp_source_prefetch_context_tokens")
        response = config.get("sp_source_prefetch_response_tokens")
        if not isinstance(tokenizer, str) or not tokenizer or any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in (context, response)) or context <= response + _RESERVED_OVERHEAD_TOKENS:
            raise ValueError("Source prefetch requires a local tokenizer and explicit context/response token budgets")
        return cls(tokenizer=tokenizer, context_tokens=context, response_tokens=response)


@lru_cache(maxsize=2)
def _cached_tokenizer(path: str):
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)


def count_source_prompt_tokens(messages: list, tools: list, settings: SourcePrefetchSettings, thinking_enabled: bool | None = None) -> int:
    """Count actual chat-template/tool-schema tokens, including the reply prefix."""
    schemas = [convert_to_openai_tool(tool) for tool in tools]
    converted = convert_to_openai_messages(messages)
    kwargs = {"enable_thinking": thinking_enabled} if thinking_enabled is not None else {}
    with _TOKENIZER_LOCK:
        tokenizer = _cached_tokenizer(settings.tokenizer)
        return len(tokenizer.apply_chat_template(converted, tools=schemas, tokenize=True, add_generation_prompt=True, **kwargs))


def mark_prefetch_model_visibility(messages: list, captured: list[dict]) -> None:
    """Only complete original tool pairs surviving model middleware prove reads."""
    calls = {call.get("id"): call for message in messages if isinstance(message, AIMessage) for call in message.tool_calls}
    visible = {(message.tool_call_id, message.name): message for message in messages if isinstance(message, ToolMessage) and message.tool_call_id in calls and calls[message.tool_call_id].get("name") == message.name}
    for saved in captured:
        extra = saved.get("additional_kwargs") or {}
        if saved.get("type") != "tool" or not extra.get("sp_host_source_prefetch"):
            continue
        message = visible.get((saved.get("tool_call_id"), saved.get("name")))
        if message is not None and message.content == saved.get("content"):
            saved["additional_kwargs"] = {**extra, "sp_host_source_prefetch_visible": True}


class SourcePrefetchVisibilityMiddleware(AgentMiddleware):
    """Innermost model wrapper: summaries/previews do not count as full input."""

    def __init__(self, captured: list[dict], *, contract: Mapping[str, Any] | None = None, settings: SourcePrefetchSettings | None = None, auditor_role: str | None = None, thinking_enabled: bool | None = None):
        self._captured = captured
        self._contract = contract
        self._settings = settings
        self._auditor_role = auditor_role
        self._thinking_enabled = thinking_enabled

    def _complete_file_audit_request(self, request):
        """Use fresh original pairs in this request, never a parent's read ledger."""
        contract = self._contract
        if self._settings is None or self._auditor_role != "researcher" or not isinstance(contract, Mapping) or contract.get("delivery_mode") != "inline" or "output_schema" not in contract:
            return None
        required = contract.get("source_requirements")
        if not isinstance(required, Mapping) or required.get("audit_agent") != "researcher" or required.get("document_ids") != []:
            return None
        files = required.get("files")
        if not isinstance(files, list) or not files:
            return None
        for file in files:
            if (
                not isinstance(file, Mapping)
                or not isinstance(file.get("path"), str)
                or not file["path"].startswith("/mnt/user-data/uploads/")
                or not isinstance(file.get("total_lines"), int)
                or isinstance(file["total_lines"], bool)
                or file["total_lines"] < 1
            ):
                return None
        from deerflow.sp.source_access import collect_source_access, source_access_gaps

        current_calls = {call.get("id"): call for message in request.messages if isinstance(message, AIMessage) for call in message.tool_calls}
        current_results = {(message.tool_call_id, message.name): message for message in request.messages if isinstance(message, ToolMessage)}
        saved_calls = {call.get("id"): call for saved in self._captured if saved.get("type") == "ai" and (saved.get("additional_kwargs") or {}).get("sp_host_source_prefetch") for call in saved.get("tool_calls", [])}
        original_pairs = []
        for saved in self._captured:
            if saved.get("type") != "tool" or not (saved.get("additional_kwargs") or {}).get("sp_host_source_prefetch"):
                continue
            call_id = saved.get("tool_call_id")
            call = saved_calls.get(call_id)
            result = current_results.get((call_id, saved.get("name")))
            if call is None or current_calls.get(call_id) != call or result is None or result.content != saved.get("content") or result.status != "success" or saved.get("status") != "success":
                continue
            original_pairs.extend([{"type": "ai", "tool_calls": [call]}, {**saved, "additional_kwargs": {**saved["additional_kwargs"], "sp_host_source_prefetch_visible": True}}])
        # Recompute coverage from only these exact current-request originals.
        # Older visibility flags and source_access from any run are irrelevant.
        access = collect_source_access(original_pairs)
        if source_access_gaps({"sp_task_contract": contract, "sp_source_access": access}):
            return None
        tools = [tool for tool in request.tools if getattr(tool, "name", None) not in {"read_file", "grep"}]
        if len(tools) == len(request.tools):
            return None
        note = HumanMessage(
            name="sp_complete_visible_file_audit",
            additional_kwargs={"hide_from_ui": True},
            content=(
                "<complete-visible-file-audit>\n"
                "All required public file sources are present as complete original native read_file results in this exact model input. "
                "Review that original content and perform the complete original audit now, including the requested analysis and final answer. "
                "Repeated read_file/grep tools are temporarily unavailable while these originals remain fully visible. "
                "A rules catalogue, coverage map, source-read receipt or plan alone does not complete the original task. "
                "Use the original scenario and all relevant source rules to form your own supported conclusion, then return the exact raw public output required by output_schema/output_parse_mode. "
                "The host has not performed the substantive audit or chosen an answer. Do not select a default answer, fabricate evidence, or hide incomplete work. "
                "If a substantive requirement genuinely remains unresolved, return honest partial status with concrete evidence gaps. "
                "All native token, turn, loop, safety and public-schema checks still apply.\n"
                "</complete-visible-file-audit>"
            ),
        )
        return request.override(tools=tools, messages=[*request.messages, note])

    def _within_context(self, request) -> bool:
        try:
            count = count_source_prompt_tokens(request.messages, request.tools, self._settings, self._thinking_enabled)
        except Exception:
            logger.debug("Current-source audit token verification unavailable; retaining native source tools", exc_info=True)
            return False
        return count + self._settings.response_tokens + _RESERVED_OVERHEAD_TOKENS <= self._settings.context_tokens

    def wrap_model_call(self, request, handler):
        mark_prefetch_model_visibility(request.messages, self._captured)
        complete = self._complete_file_audit_request(request)
        return handler(complete if complete is not None and self._within_context(complete) else request)

    async def awrap_model_call(self, request, handler):
        mark_prefetch_model_visibility(request.messages, self._captured)
        complete = self._complete_file_audit_request(request)
        within_context = complete is not None and await asyncio.to_thread(self._within_context, complete)
        return await handler(complete if within_context else request)


def _would_compact(messages: list, middlewares: list) -> bool:
    for middleware in middlewares:
        if not all(hasattr(middleware, name) for name in ("_messages_for_trigger_count", "_should_summarize", "token_counter")):
            continue
        trigger_messages = middleware._messages_for_trigger_count(messages, None)
        # Leave native estimator room for small before-model annotations as
        # well as respecting the independently counted exact context budget.
        if middleware._should_summarize(trigger_messages, middleware.token_counter(trigger_messages) + 1024):
            return True
    return False


def _source_coverage(result: ToolMessage) -> dict:
    artifact = result.artifact if isinstance(result.artifact, Mapping) else {}
    coverage = artifact.get("native_source_coverage")
    if isinstance(coverage, dict):
        return coverage
    try:
        parsed = json.loads(result.content)
    except (ValueError, TypeError):
        return {}
    return parsed.get("source_coverage", {}) if isinstance(parsed, dict) else {}


def _unread_line_ranges(total: int, ranges: list) -> list[tuple[int, int]]:
    cursor = 1
    missing = []
    valid = sorted((pair[0], pair[1]) for pair in ranges if isinstance(pair, list | tuple) and len(pair) == 2 and all(isinstance(value, int) and not isinstance(value, bool) for value in pair) and pair[0] > 0 and pair[1] >= pair[0])
    for lower, upper in valid:
        if lower > cursor:
            missing.append((cursor, min(total, lower - 1)))
        cursor = max(cursor, upper + 1)
        if cursor > total:
            break
    if cursor <= total:
        missing.append((cursor, total))
    return missing


async def _native_call(request: ToolCallRequest, middlewares: list) -> ToolMessage:
    """Use exactly the graph's native interceptor order, including guardrails."""

    async def execute(current):
        call = current.tool_call
        return await current.tool.ainvoke({**call, "type": "tool_call", "args": {**call["args"], "runtime": current.runtime}}, config=current.runtime.config)

    handler = execute
    for middleware in reversed(middlewares):
        if type(middleware).awrap_tool_call is AgentMiddleware.awrap_tool_call:
            continue
        inner = handler

        async def wrapped(current, middleware=middleware, inner=inner):
            return await middleware.awrap_tool_call(current, inner)

        handler = wrapped
    result = await handler(request)
    if isinstance(result, Command) and isinstance(result.update, Mapping) and not result.goto and result.graph is None and result.resume is None:
        updates = result.update
        returned = updates.get("messages", [])
        # Native lazy sandbox acquisition wraps the genuine ToolMessage in a
        # state-update Command. Apply its non-message state before continuing;
        # the caller appends the one actual result with normal budget handling.
        if isinstance(returned, list) and len(returned) == 1 and isinstance(returned[0], ToolMessage) and returned[0].tool_call_id == request.tool_call["id"]:
            request.state.update({key: value for key, value in updates.items() if key != "messages"})
            result = returned[0]
    if not isinstance(result, ToolMessage):
        raise TypeError(f"Public source read returned an unsupported native control result: {type(result).__module__}.{type(result).__qualname__}")
    return result


async def _compact_real_result(result: ToolMessage, request: ToolCallRequest) -> ToolMessage:
    """Reuse native lossless externalization, never mark a preview as a read."""
    from deerflow.agents.middlewares.tool_output_budget_middleware import ToolOutputBudgetMiddleware
    from deerflow.config.tool_output_config import ToolOutputConfig

    middleware = ToolOutputBudgetMiddleware(ToolOutputConfig(externalize_min_chars=1, exempt_tools=[], preview_head_chars=160, preview_tail_chars=80, fallback_max_chars=800, fallback_head_chars=160, fallback_tail_chars=80))

    async def original(_):
        return result

    return await middleware.awrap_tool_call(request, original)


async def prefetch_public_sources(
    *,
    state: dict,
    tools: list,
    middlewares: list,
    config: dict,
    context: dict,
    settings: SourcePrefetchSettings,
    contract: Mapping[str, Any],
    capture: Callable[[list], None] | None = None,
    token_counter: Callable[[list, list], int] | None = None,
    thinking_enabled: bool | None = None,
    max_tool_calls: int = 50,
    cancelled: Callable[[], bool] | None = None,
    observed_source_access: Mapping[str, Any] | None = None,
) -> None:
    """Append genuine source reads within a prompt budget; never create answers."""
    required = contract.get("source_requirements")
    if not isinstance(required, Mapping):
        return
    available = {tool.name: tool for tool in tools if tool.name in {"read_file", "ragflow_retrieve"}}
    if not available:
        return
    from deerflow.sp.source_access import source_access_gaps

    observed = observed_source_access if isinstance(observed_source_access, Mapping) else {}
    if observed.get("run_id") and context.get("run_id") and observed["run_id"] != context["run_id"]:
        observed = {}
    reads = observed.get("reads") or {}

    def covered(*, files=None, document_ids=None):
        return not source_access_gaps({"sp_loop_run_id": context.get("run_id"), "sp_task_contract": {"source_requirements": {"files": files or [], "document_ids": document_ids or []}}, "sp_source_access": observed})

    def line_read(path, line):
        return any(lower <= line <= upper for lower, upper in reads.get(path, []))

    limit = settings.context_tokens - settings.response_tokens - _RESERVED_OVERHEAD_TOKENS

    async def count(messages):
        if token_counter is not None:
            return token_counter(messages, tools)
        return await asyncio.to_thread(count_source_prompt_tokens, messages, tools, settings, thinking_enabled)

    async def fits(messages, extra=0):
        return await count(messages) + extra <= limit and not _would_compact(messages, middlewares)

    messages = state.get("messages", [])
    if not messages:
        return
    original_last = messages[-1]
    if not isinstance(original_last.content, str):
        return
    # Keep one user turn: adding another HumanMessage after the reads would
    # reset the native per-turn tool counters and discard attribution.
    messages[-1] = original_last.model_copy(update={"content": original_last.content + _HOST_NOTE})
    if not await fits(messages, 1024):
        messages[-1] = original_last
        return
    used = 0
    stopped = False
    maximum = max(0, min(32, max_tool_calls - 8))

    async def call(name, args):
        nonlocal used, stopped
        if stopped or name not in available or used >= maximum or (cancelled is not None and cancelled()):
            return None, False
        call_id = "host_source_" + uuid.uuid4().hex[:16]
        tool_call = {"name": name, "id": call_id, "args": args, "type": "tool_call"}
        ai = AIMessage(content="[HOST public-source prefetch: native read-only evidence call]", id=call_id + "_request", tool_calls=[tool_call], additional_kwargs={"sp_host_source_prefetch": True})
        minimal = ToolMessage(content="[truncated: source remains unread; page the original public source]", name=name, tool_call_id=call_id)
        if not await fits([*messages, ai, minimal], 1024):
            stopped = True
            return None, False
        messages.append(ai)
        runtime = ToolRuntime(state=state, context=context, config=config, stream_writer=lambda _: None, tools=tools, tool_call_id=call_id, store=None)
        request = ToolCallRequest(tool_call=tool_call, tool=available[name], state=state, runtime=runtime)
        result = await _native_call(request, middlewares)
        used += 1
        retained = await fits([*messages, result])
        if not retained:
            result = await _compact_real_result(result, request)
        if not await fits([*messages, result]):
            # The full actual output was saved by native externalization where
            # available. Keep only its real reference, not source-read proof.
            coverage = _source_coverage(result)
            path = coverage.get("externalized_path", args.get("path", ""))
            result = result.model_copy(update={"content": f"[truncated: public source exceeds the remaining prefetch context budget; source remains unread. Read {path or 'the original public source'} with native paginated tools.]"})
            stopped = True
        if not await fits([*messages, result]):
            raise ValueError("Source prefetch cannot fit the native result reference within its reserved context")
        result = result.model_copy(update={"id": result.id or call_id + "_result", "additional_kwargs": {**result.additional_kwargs, "sp_host_source_prefetch": True}})
        messages.append(result)
        if capture is not None:
            capture([ai, result])
        return result, retained

    async def read_range(path, start, end):
        result, retained = await call("read_file", {"description": "Host prefetch of required public source evidence", "path": path, "start_line": start, "end_line": end})
        if result is None:
            return
        body = result.content if isinstance(result.content, str) else ""
        if result.status == "error" or body.startswith(("Error:", "(empty")):
            return result
        if (not retained or "[truncated:" in body or "[Full read_file output saved to" in body) and end > start and not stopped:
            middle = (start + end) // 2
            await read_range(path, start, middle)
            await read_range(path, middle + 1, end)
        return result

    # A fresh file-only audit instance must see the actual original files,
    # even when an earlier (partial) auditor already established source proof.
    # Keep incomplete file/RAG continuation on missing ranges/pages, so large
    # mixed RAG documents do not restart their prefixes on each delegation.
    reexpose_files = not required.get("document_ids") and covered(files=required.get("files", []))
    for file in required.get("files", []):
        if not isinstance(file, Mapping):
            continue
        path, lines = file.get("path"), file.get("total_lines")
        if isinstance(path, str) and path.startswith("/mnt/user-data/uploads/") and posixpath.normpath(path) == path and isinstance(lines, int) and not isinstance(lines, bool) and lines > 0:
            if not reexpose_files and covered(files=[file]):
                continue
            for start, end in _unread_line_ranges(lines, [] if reexpose_files else reads.get(path, [])):
                await read_range(path, start, end)
    for document_id in required.get("document_ids", []):
        if not isinstance(document_id, str) or not document_id:
            continue
        if covered(document_ids=[document_id]):
            continue
        # Reuse actual saved native pages rather than issuing another RAG call
        # with a fresh externalized path and rereading the same first chunks.
        saved_pages = {}
        for saved in observed.get("pages", []):
            if saved.get("mode") != "document" or saved.get("document_id") != document_id or not isinstance(saved.get("page"), int) or saved["page"] < 1:
                continue
            path = saved.get("externalized_path")
            inline = saved.get("inline_complete") is True
            if not inline and not (isinstance(path, str) and path.startswith("/mnt/user-data/outputs/") and posixpath.normpath(path) == path and saved.get("content_lines")):
                continue
            key = (saved["page"], saved.get("page_size"))
            remaining = 0 if inline else sum(not line_read(path, line) for line in saved["content_lines"])
            if key not in saved_pages or remaining < saved_pages[key][0]:
                saved_pages[key] = (remaining, saved)
        reusable = set()
        for _, saved in sorted(saved_pages.values(), key=lambda pair: pair[1]["page"]):
            path = saved.get("externalized_path")
            usable = True
            if not saved.get("inline_complete"):
                for line in saved.get("content_lines", []):
                    if not isinstance(line, int) or isinstance(line, bool) or line < 1 or line_read(path, line):
                        continue
                    result = await read_range(path, line, line)
                    if result is not None and (result.status == "error" or str(result.content).startswith(("Error:", "(empty"))):
                        usable = False
                        break
            if usable and saved.get("page_size") == 100 and isinstance(saved.get("total"), int) and (saved["total"] > 0 or saved.get("document_bytes") == 0):
                reusable.add(saved["page"])
        totals = [saved["total"] for _, saved in saved_pages.values() if isinstance(saved.get("total"), int) and saved["total"] >= 0]
        known_last = max(1, math.ceil(max(totals) / 100)) if totals else None
        page = 1
        while not stopped:
            if page in reusable:
                if known_last is not None and page >= known_last:
                    break
                page += 1
                continue
            result, _ = await call("ragflow_retrieve", {"question": "Read complete scoped public source evidence", "document_id": document_id, "page": page, "page_size": 100})
            if result is None or result.status == "error":
                break
            coverage = _source_coverage(result)
            if coverage.get("mode") != "document" or coverage.get("document_id") != document_id:
                break
            path = coverage.get("externalized_path")
            if isinstance(path, str) and path.startswith("/mnt/user-data/outputs/") and posixpath.normpath(path) == path:
                # Only host-generated references from this real scoped RAG
                # response can add output paths to the prefetch read set.
                for line in coverage.get("content_lines", []):
                    if isinstance(line, int) and line > 0:
                        await read_range(path, line, line)
            total = coverage.get("total")
            if not isinstance(total, int) or total < 0 or page >= math.ceil(total / 100) or not coverage.get("returned"):
                break
            page += 1
    logger.info("Native public-source prefetch executed %d calls; full source coverage remains independently gated", used)
