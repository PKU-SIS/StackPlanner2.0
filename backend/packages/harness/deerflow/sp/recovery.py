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
