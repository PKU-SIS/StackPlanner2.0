"""One tool-free continuation of a length-truncated report research body."""

import json
from dataclasses import replace
from typing import Any

from deerflow.sp.report import store
from deerflow.sp.subagents.adapter import SPSubagentStatus

RESEARCH_OUTPUT_LIMIT = 65_536
CONTINUE_INSTRUCTION = """Continue the truncated research Markdown immediately after its last character.
Return ONLY the missing suffix, never repeat or rewrite the supplied prefix.
No tools, searches, fetching or new research. Use only the original body and saved
evidence. Finish any interrupted sentence/table and close the synthesis normally.
Keep canonical [S<n>] citations and source URLs. Do not invent evidence or source
identities. For supplementary research, execution declarations must match the
supplied host tool records; continuation does not perform another research round.
Do not wrap the suffix in JSON or code fences or promise another continuation.
""".strip()


def continue_output(result: Any, task: Any, ledger: Any, executor: Any, *, marker: str):
    if task.subagent_type != "researcher" or task.metadata.get("report_role") != "researcher" or not task.metadata.get("report_id"):
        return result
    if result.stop_reason != "output_capped" or result.error or result.status != SPSubagentStatus.COMPLETED or task.metadata.get("report_output_continuation"):
        return result
    prefix = str(result.artifact_content or "")
    if not prefix.strip() or executor is None:
        return result
    directory = ledger.paths.summary_file(task.action_id).parent / "diagnostics"
    if not (directory / "original.md").exists():
        store.write_text_atomic(directory / "original.md", prefix)
    evidence, chars = [], 0
    for m in ledger.materials():
        excerpt = str(m.get("preview") or "")[:600]
        if chars + len(excerpt) > 24_000:
            break
        chars += len(excerpt)
        evidence.append({k: m.get(k) for k in ("source_id", "material_id", "title", "url")} | {"excerpt": excerpt})
    budget = task.metadata.get("report_research_token_budget") or {"enabled": True, "max_tokens": 2_000_000}
    used = sum(int(r.get("total_tokens") or int(r.get("input_tokens") or 0) + int(r.get("output_tokens") or 0)) for r in result.token_usage_records)
    remaining = int(int(budget["max_tokens"]) * float(budget.get("hard_stop_threshold", 1.0))) - used if budget.get("enabled", True) else None
    used_input = sum(int(r.get("input_tokens") or 0) for r in result.token_usage_records)
    used_output = sum(int(r.get("output_tokens") or 0) for r in result.token_usage_records)
    input_remaining = int(budget["max_input_tokens"]) - used_input if budget.get("enabled", True) and budget.get("max_input_tokens") else None
    output_remaining = int(budget["max_output_tokens"]) - used_output if budget.get("enabled", True) and budget.get("max_output_tokens") else None
    meta = {**task.metadata, "report_output_continuation": True, "report_summary_repair": not bool(task.metadata.get("report_gap_ids")), "report_supplement_repair": bool(task.metadata.get("report_gap_ids"))}
    if remaining is not None:
        meta["report_delivery_remaining_tokens"] = remaining
    repair = replace(
        task,
        task="Continue the original truncated synthesis once; output only the missing suffix.",
        description="Complete truncated research delivery",
        expected_output="Missing Markdown suffix only",
        allowed_tools=[],
        context_refs={
            "research_continuation": {
                "prefix": prefix,
                "original_task": task.task,
                "saved_evidence": evidence,
                "host_tool_execution_records": result.artifact_metadata.get("host_tool_execution_records") or [],
                "original_gap_attempts": result.artifact_metadata.get("gap_attempts") or [],
                "assigned_gaps": (task.context_refs.get("report_packet") or {}).get("gaps", []),
            }
        },
        metadata=meta,
    )
    # UTF-8 bytes conservatively bound prompt tokens; reserve framework/system overhead.
    from deerflow.sp.subagents.dr2_adapter import render_sp_subagent_prompt

    input_reserve = len(render_sp_subagent_prompt(repair).encode("utf-8")) + len(CONTINUE_INSTRUCTION.encode("utf-8")) + 8192
    output_limit = min(RESEARCH_OUTPUT_LIMIT, remaining - input_reserve) if remaining is not None else RESEARCH_OUTPUT_LIMIT
    if output_remaining is not None:
        output_limit = min(output_limit, output_remaining)
    if output_limit < 1024 or input_remaining is not None and input_reserve > input_remaining:
        return replace(result, artifact_metadata={**result.artifact_metadata, "continuation_skipped_reason": "insufficient_remaining_budget"})
    repair.metadata["report_delivery_output_limit"] = output_limit
    repair.metadata["report_delivery_input_remaining"] = input_remaining
    repair.metadata["report_delivery_output_remaining"] = output_remaining
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / marker).open("x", encoding="utf-8") as f:
            json.dump({"action_id": task.action_id, "kind": "output_continuation", "remaining_tokens": remaining, "max_output_tokens": output_limit}, f)
    except FileExistsError:
        return result
    try:
        suffix = executor.execute(repair)
    except Exception as exc:
        return replace(result, error=f"Output continuation failed: {type(exc).__name__}: {exc}", artifact_metadata={**result.artifact_metadata, "output_continuation_attempted": True})
    tail = str(suffix.artifact_content or "")
    store.write_text_atomic(directory / "continuation.md", tail)
    joined = prefix + tail
    store.write_text_atomic(directory / "continued.md", joined)
    if not tail.strip():
        suffix = replace(suffix, error="Output continuation returned an empty suffix")
    return replace(
        suffix,
        artifact_content=joined,
        result=" ".join(joined.split())[:680],
        token_usage_records=[*result.token_usage_records, *suffix.token_usage_records],
        artifact_metadata={**result.artifact_metadata, "output_continuation_attempted": True, "completion_status": "complete" if not suffix.error and not suffix.stop_reason and suffix.status == SPSubagentStatus.COMPLETED else "partial"},
    )
