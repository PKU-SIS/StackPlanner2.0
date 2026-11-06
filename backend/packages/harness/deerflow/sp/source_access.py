"""Observable source access for public evidence contracts, without answer inference."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def merge_source_access(left: dict | None, right: dict | None) -> dict:
    left, right = left or {}, right or {}
    if right.get("run_id") and left.get("run_id") != right["run_id"]:
        left = {}
    reads = {}
    for source in (left, right):
        for path, ranges in (source.get("reads") or {}).items():
            reads[path] = sorted({tuple(pair) for pair in [*reads.get(path, []), *ranges]})
    pages = []
    for source in (left, right):
        for page in source.get("pages", []):
            if page not in pages:
                pages.append(page)
    return {"run_id": right.get("run_id") or left.get("run_id"), "reads": reads, "pages": pages}


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(item.get("text", "") for item in value if isinstance(item, dict))
    return ""


def collect_source_access(messages: list | None) -> dict:
    calls: dict[str, dict] = {}
    reads: dict[str, list] = {}
    pages: list[dict] = []
    for message in messages or []:
        if not isinstance(message, Mapping):
            continue
        if message.get("type", message.get("role")) in {"ai", "assistant"}:
            calls.update({call["id"]: call for call in message.get("tool_calls", []) if isinstance(call, dict) and call.get("id")})
            continue
        if message.get("type", message.get("role")) != "tool" or message.get("status") == "error":
            continue
        extra = message.get("additional_kwargs") or {}
        if isinstance(extra, Mapping) and extra.get("sp_host_source_prefetch") and not extra.get("sp_host_source_prefetch_visible"):
            # Host execution alone is not model observation: native summary or
            # input trimming may remove a prefetched original before first use.
            continue
        call = calls.get(message.get("tool_call_id"))
        if not call or call.get("name") != message.get("name"):
            continue
        args = call.get("args") or {}
        body = _text(message.get("content"))
        if body.startswith("Error:") or not isinstance(args, Mapping):
            continue
        if call["name"] in {"read_file", "read_file_tool"}:
            path = args.get("path")
            if not isinstance(path, str) or not path.startswith("/mnt/user-data/") or "[truncated:" in body or "[Full read_file output saved to" in body or body.startswith("(empty"):
                continue
            start = args.get("start_line") or 1
            end = args.get("end_line")
            if not isinstance(start, int) or start < 1 or (end is not None and (not isinstance(end, int) or end < start)):
                continue
            # Native ranged reads use "\n".join(source.splitlines()[slice]).
            # splitlines() again loses an actually returned final empty line,
            # including the valid empty body of a one-line blank range.
            # Unbounded reads retain original EOF/newline semantics instead.
            ranged = args.get("start_line") is not None or end is not None
            observed_end = start + len(body.split("\n") if ranged else body.splitlines()) - 1
            if end is not None:
                observed_end = min(end, observed_end)
            if observed_end >= start:
                reads.setdefault(path, []).append([start, observed_end])
        elif call["name"] == "ragflow_retrieve":
            artifact = message.get("artifact") or {}
            source = artifact.get("native_source_coverage") if isinstance(artifact, Mapping) else None
            inline = False
            if source is None:
                try:
                    result = json.loads(body)
                    source = result.get("source_coverage")
                    inline = isinstance(result.get("chunks"), list)
                except (ValueError, AttributeError):
                    continue
            if not isinstance(source, dict) or source.get("mode") != "document" or source.get("document_id") != args.get("document_id"):
                continue
            pages.append({**source, "inline_complete": inline})
    return {"reads": reads, "pages": pages}


def _covers(ranges: list, start: int, end: int) -> bool:
    current = start
    for lower, upper in sorted(ranges):
        if lower > current:
            break
        current = max(current, upper + 1)
        if current > end:
            return True
    return end < start


def public_audit_completion_gaps(state: Mapping[str, Any]) -> list[str]:
    """Require the designated auditor's latest actual current-run completion.

    Keep this separate from source_access_gaps: a delegate's source checks run
    before its observation is appended to native task memory.
    """
    contract = state.get("sp_task_contract") or {}
    required = contract.get("source_requirements") if isinstance(contract, Mapping) else None
    target = required.get("audit_agent") if isinstance(required, Mapping) else None
    if not isinstance(target, str) or not target.strip():
        return []
    from deerflow.sp.memory import TaskMemoryStack

    run_id = str(state.get("sp_loop_run_id") or "").strip()
    stack = TaskMemoryStack.from_dict(state.get("sp_task_memory"))
    for entry in reversed(stack.entries):
        if not run_id or str(entry.run_id or "") != run_id:
            continue
        if entry.action == "delegate" and entry.metadata.get("target_agent") == target:
            break
        if entry.action not in {"observe", "error"} or entry.actor != target:
            continue
        metadata = entry.metadata
        if entry.action != "observe" or metadata.get("completion_status") != "complete":
            return [f"Required public {target} audit has no complete native status."]
        if metadata.get("stop_reason") or metadata.get("subagent_stop_reason") or metadata.get("evidence_gaps"):
            return [f"Required public {target} audit has unresolved native gaps or a stop reason."]
        return []
    return [f"Required public {target} audit has no current-run completed native observation."]


def source_access_gaps(state: Mapping[str, Any]) -> list[str]:
    contract = state.get("sp_task_contract") or {}
    required = contract.get("source_requirements") if isinstance(contract, Mapping) else None
    if not isinstance(required, Mapping):
        return []
    access = state.get("sp_source_access") or {}
    if access.get("run_id") and state.get("sp_loop_run_id") and access["run_id"] != state["sp_loop_run_id"]:
        access = {}
    reads = access.get("reads") or {}
    gaps = []
    for file in required.get("files", []):
        if not _covers(reads.get(file["path"], []), 1, file["total_lines"]):
            gaps.append(f"Required public source file remains unread or truncated: {file['path']} (lines 1..{file['total_lines']}).")
    for document_id in required.get("document_ids", []):
        pages = [page for page in access.get("pages", []) if page.get("document_id") == document_id]
        totals, chunks = set(), set()
        for page in pages:
            path, lines = page.get("externalized_path"), page.get("content_lines") or []
            complete = page.get("inline_complete") or (path and lines and all(_covers(reads.get(path, []), line, line) for line in lines))
            if not complete or not isinstance(page.get("total"), int) or page["total"] < 0:
                continue
            if page["total"] == 0 and page.get("document_bytes") != 0:
                continue
            totals.add(page["total"])
            chunks.update(value for value in page.get("chunk_ids", []) if isinstance(value, str) and value)
        if len(totals) != 1 or len(chunks) != next(iter(totals), -1):
            gaps.append(
                f"Required external source evidence remains unread: document_id={document_id}. Enumerate all pages with ragflow_retrieve(document_id=..., page_size=100); read complete chunk content, not a preview or a zero-match query."
            )
    return gaps
