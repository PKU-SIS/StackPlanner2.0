"""Host-owned, bounded recovery limits shared by schema and execution gates."""

from collections.abc import Mapping
from typing import Any

SP_REPORT_RECOVERY_MAX_ATTEMPTS = 3
SP_DELEGATE_RECOVERY_MAX_ATTEMPTS = 3
SP_CAPPED_DELEGATE_RECOVERY_MAX_ATTEMPTS = 2
MAX_SP_RECOVERY_ATTEMPTS = 32

_DEFAULTS = {
    "reporter": SP_REPORT_RECOVERY_MAX_ATTEMPTS,
    "delegate": SP_DELEGATE_RECOVERY_MAX_ATTEMPTS,
    "capped_delegate": SP_CAPPED_DELEGATE_RECOVERY_MAX_ATTEMPTS,
}
_RUNTIME_KEYS = {
    "reporter": "sp_report_recovery_max_attempts",
    "delegate": "sp_delegate_recovery_max_attempts",
    "capped_delegate": "sp_capped_delegate_recovery_max_attempts",
}


def normalize_recovery_limits(values: Mapping[str, Any] | None = None) -> dict[str, int]:
    limits = dict(_DEFAULTS)
    for kind, default in _DEFAULTS.items():
        value = (values or {}).get(kind, default)
        if isinstance(value, bool):
            continue
        try:
            limits[kind] = max(1, min(int(value), MAX_SP_RECOVERY_ATTEMPTS))
        except (TypeError, ValueError, OverflowError):
            continue
    # A guard stop may have fewer retries, never more than ordinary failures.
    limits["capped_delegate"] = min(limits["capped_delegate"], limits["delegate"])
    return limits


def runtime_recovery_limits(runtime: Mapping[str, Any]) -> dict[str, int]:
    return normalize_recovery_limits({kind: runtime.get(key, _DEFAULTS[kind]) for kind, key in _RUNTIME_KEYS.items()})


def recovery_limit(state: Mapping[str, Any] | None, kind: str) -> int:
    raw = (state or {}).get("sp_recovery_limits")
    return normalize_recovery_limits(raw if isinstance(raw, Mapping) else None)[kind]


def source_audit_recovery_instruction(previous: Any, *, has_documents: bool) -> str:
    """Expose actual prior gaps and transport semantics, without resolving them."""
    raw_gaps = previous.metadata.get("evidence_gaps")
    actual_gaps = [str(gap)[:500] for gap in raw_gaps[:5]] if isinstance(raw_gaps, list) else []
    instruction = (
        "Use a materially different recovery method that resolves the previous native auditor's actual gaps, not another identical evidence-only delegation. "
        "Reuse the actual referenced draft for valid prior work; the current auditor still needs original source content. "
        "Complete the original audit, including any analysis and final answer requested by the public task; do not stop at collecting rules or planning the analysis. "
        + (f"Previous native audit result_ref: {previous.result_ref}. " if previous.result_ref else "")
        + "Previous native audit gaps: "
        + "; ".join(actual_gaps or ["The previous audit has no clean complete native status."])
        + " "
    )
    if has_documents:
        return instruction + (
            "For RAG document enumeration, use the actual native pagination header: total_chunks is a chunk count, total_pages is ceil(total_chunks/page_size), "
            "and returned_chunks is the actual returned count, not page_size. Follow next_page only while has_more. "
            "Empty out_of_range pages are beyond the end of the indexed document, not missing source pages. "
            "Recheck previous coverage claims against these actual service facts and inspect every actual original chunk. "
            "Indexed pagination is not proof that the substantive audit is complete; preserve partial status for any genuine remaining requirement. "
        )
    return instruction + "For a file-only audit with all historical source reads complete, native prefetch re-reads those real files for this new model instance within its context budget. "


def _is_run_reporter_artifact(ref: Mapping[str, Any], *, run_id: str | None) -> bool:
    metadata = ref.get("metadata")
    return bool(run_id and ref.get("run_id") == run_id and ref.get("type") in {"report", "report_revision", "final_report"} and isinstance(metadata, Mapping) and (ref.get("created_by") or metadata.get("target_agent")) == "reporter")


def is_token_capped_report_artifact(ref: Mapping[str, Any], *, run_id: str | None) -> bool:
    """Recognize a native current-run unfinished Reporter artifact, not its text."""
    metadata = ref.get("metadata")
    return bool(_is_run_reporter_artifact(ref, run_id=run_id) and metadata.get("completion_status") in {"partial", "blocked"} and (metadata.get("stop_reason") or metadata.get("subagent_stop_reason")) == "token_capped")


def _run_reporter_artifacts(state: Mapping[str, Any], *, run_id: str) -> dict[str, Mapping[str, Any]]:
    raw = state.get("sp_current_artifact_refs")
    if not isinstance(raw, Mapping):
        return {}
    history = raw.get("_history")
    return {str(ref["artifact_id"]): ref for ref in [*(history if isinstance(history, list) else []), *raw.values()] if isinstance(ref, Mapping) and ref.get("artifact_id") and _is_run_reporter_artifact(ref, run_id=run_id)}


def _unresolved_capped_report_artifact_ids(refs: Mapping[str, Mapping[str, Any]], *, run_id: str) -> list[str]:
    """Follow the current unfinished report's real artifact lineage across retries."""
    current = [ref for ref in refs.values() if ref.get("is_current", True)]
    if not current:
        return []
    latest = max(current, key=lambda ref: ref.get("version") if isinstance(ref.get("version"), int) else 0)
    if (latest.get("metadata") or {}).get("completion_status") not in {"partial", "blocked"}:
        return []
    pending = [str(latest["artifact_id"])]
    visited: set[str] = set()
    capped: list[str] = []
    while pending:
        artifact_id = pending.pop()
        if artifact_id in visited or artifact_id not in refs:
            continue
        visited.add(artifact_id)
        ref = refs[artifact_id]
        metadata = ref.get("metadata") or {}
        # A genuinely completed report closes its earlier recovery ancestry.
        if metadata.get("completion_status") == "complete":
            continue
        if is_token_capped_report_artifact(ref, run_id=run_id):
            capped.append(artifact_id)
        for values in (ref.get("parent_artifact_ids"), metadata.get("source_artifact_ids")):
            if isinstance(values, list):
                pending.extend(str(value) for value in values if value)
    return capped


def artifact_array_token_cap_instruction(state: Mapping[str, Any], *, stack: Any = None, run_id: str | None = None) -> str:
    """Guide the model after an actual current-run capped artifact-array audit.

    This changes only the next recovery prompt; original drafts, source evidence,
    tool/runtime budgets and native acceptance remain untouched.
    """
    contract = state.get("sp_task_contract")
    if not isinstance(contract, Mapping) or contract.get("delivery_mode") != "artifact" or contract.get("output_parse_mode", "json") != "json":
        return ""
    schema = contract.get("output_schema")
    if not isinstance(schema, Mapping) or schema.get("type") != "array":
        return ""
    run_id = run_id or str(state.get("sp_loop_run_id") or "")
    if not run_id:
        return ""
    if stack is None:
        from deerflow.sp.memory import TaskMemoryStack

        stack = TaskMemoryStack.from_dict(state.get("sp_task_memory"))
    previous = next((entry for entry in reversed(stack.entries) if entry.actor == "reporter" and entry.action in {"observe", "error"} and entry.run_id == run_id), None)
    if previous is not None and previous.action == "observe" and previous.metadata.get("completion_status") == "complete":
        return ""
    report_refs = _run_reporter_artifacts(state, run_id=run_id)
    artifact_ids = _unresolved_capped_report_artifact_ids(report_refs, run_id=run_id)
    previous_capped = (
        previous is not None
        and previous.action == "observe"
        and previous.metadata.get("completion_status") in ("partial", "blocked")
        and (previous.metadata.get("stop_reason") or previous.metadata.get("subagent_stop_reason")) == "token_capped"
    )
    if not artifact_ids and (report_refs or not previous_capped):
        return ""
    return (
        "<public-array-token-cap-recovery>\n"
        "A native current-run Reporter delivery was token_capped and remains an unverified, noncanonical draft. "
        + ("Unresolved original draft artifact references: " + ", ".join(artifact_ids) + ". Inspect the unchanged full draft through native tools only as needed to verify supported findings. " if artifact_ids else "")
        + "Rebuild the complete answer from the original public sources and verify supported prior findings. "
        "Include each distinct supported issue exactly once; consolidate repeated descriptions of the same issue. "
        "You are not required to reproduce repeated draft entries unchanged. Preserve every distinct supported issue. "
        "For evidence, retain the complete verbatim original sentences or passages required by the public task; "
        "choose the shortest sufficient complete quotation without truncating necessary evidence. "
        "Keep explanations normally to one to three sentences, while satisfying every public requirement. "
        "Return one complete, closed, valid JSON array matching the original public schema, including its closing bracket. "
        "Never truncate necessary evidence or omit a distinct supported issue to save tokens, invent findings, or replace unfinished work with an empty/default answer. "
        "If the substantive audit cannot be completed, preserve honest partial status and concrete evidence gaps.\n"
        "</public-array-token-cap-recovery>\n"
    )
