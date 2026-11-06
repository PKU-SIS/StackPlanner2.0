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
    if previous is None or previous.action != "observe" or previous.metadata.get("completion_status") not in ("partial", "blocked"):
        return ""
    if (previous.metadata.get("stop_reason") or previous.metadata.get("subagent_stop_reason")) != "token_capped":
        return ""
    return (
        "<public-array-token-cap-recovery>\n"
        "The previous native Reporter delivery was token_capped and is an unverified draft. "
        "Rebuild the complete answer from the original public sources and verify supported prior findings. "
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
