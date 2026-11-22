"""Bounded raw-output repetition observations for an existing capped recovery."""

from __future__ import annotations

import os
from collections.abc import Mapping

from deerflow.sp.evidence_quotes import clause_public_audit_scope
from deerflow.subagents.status_contract import model_response_stop_reason

DIAGNOSTIC_KEY = "clause_capped_output_repetition"
_FLAG = "SP_CLAUSE_CAPPED_REPETITION_FEEDBACK"
_MIN_CHARS = 8192
_MAX_CHARS = 131072
_WIDTH = 256
_MIN_OCCURRENCES = 8
_MAX_PROBES = 128


def captured_capped_repetition(raw, messages, *, contract, skill_names, auditor_role, run_id, action_id) -> dict | None:
    """Observe an exact captured length response, never parse or repair findings."""
    if os.environ.get(_FLAG) != "1" or not run_id or not action_id or not isinstance(contract, Mapping) or not isinstance(skill_names, list | tuple):
        return None
    if not clause_public_audit_scope(contract, skill_names, auditor_role) or not isinstance(raw, str) or len(raw) < _MIN_CHARS or not raw.lstrip().startswith("["):
        return None
    if not isinstance(messages, list) or model_response_stop_reason(messages) != "token_capped":
        return None
    final = next((message for message in reversed(messages) if isinstance(message, Mapping) and message.get("type") == "ai"), None)
    if final is None or final.get("content") != raw or final.get("tool_calls") or final.get("invalid_tool_calls"):
        return None
    inspected = raw[:_MAX_CHARS]
    for probe in range(_MAX_PROBES):
        offset = probe * (len(inspected) - _WIDTH) // (_MAX_PROBES - 1)
        fragment = inspected[offset : offset + _WIDTH]
        # Ignore whitespace/low-variety loops; this diagnostic concerns long
        # repeated output passages, without assigning them semantic identity.
        if len(fragment.strip()) < 192 or len(set(fragment)) < 16:
            continue
        occurrences = inspected.count(fragment)
        if occurrences < _MIN_OCCURRENCES:
            continue
        offsets = []
        cursor = 0
        for _ in range(3):
            cursor = inspected.find(fragment, cursor)
            offsets.append(cursor)
            cursor += _WIDTH
        return {"version": 1, "run_id": run_id, "action_id": action_id, "inspected_characters": len(inspected), "fragment_characters": _WIDTH, "nonoverlapping_occurrences": occurrences, "first_offsets": offsets}
    return None


def capped_repetition_feedback(previous, *, contract, refs, run_id) -> str:
    """Read only host-bound facts for the actual current capped text artifact."""
    if os.environ.get(_FLAG) != "1" or previous is None or not isinstance(contract, Mapping) or not clause_public_audit_scope(contract, ["clause"], "reporter"):
        return ""
    metadata = previous.metadata
    facts = metadata.get(DIAGNOSTIC_KEY)
    if previous.actor != "reporter" or previous.action != "observe" or previous.run_id != run_id or metadata.get("completion_status") not in {"partial", "blocked"} or metadata.get("stop_reason") != "token_capped":
        return ""
    if not isinstance(facts, Mapping) or facts.get("version") != 1 or facts.get("run_id") != run_id or facts.get("action_id") != metadata.get("action_id") or facts.get("artifact_id") != previous.result_ref:
        return ""
    ref = refs.get(previous.result_ref)
    current = [item for item in refs.values() if item.get("is_current", True)]
    if not isinstance(ref, Mapping) or not current or max(current, key=lambda item: item.get("version") if type(item.get("version")) is int else 0).get("artifact_id") != previous.result_ref:
        return ""
    if ref.get("run_id") != run_id or (ref.get("metadata") or {}).get("delegate_action_id") != facts["action_id"] or (ref.get("metadata") or {}).get("completion_status") not in {"partial", "blocked"}:
        return ""
    size, width, count, offsets = (facts.get(key) for key in ("inspected_characters", "fragment_characters", "nonoverlapping_occurrences", "first_offsets"))
    if any(type(value) is not int for value in (size, width, count)) or not _MIN_CHARS <= size <= _MAX_CHARS or width != _WIDTH or not _MIN_OCCURRENCES <= count <= size // width:
        return ""
    if not isinstance(offsets, list) or len(offsets) != 3 or any(type(value) is not int or not 0 <= value <= size - width for value in offsets) or any(right - left < width for left, right in zip(offsets, offsets[1:])):
        return ""
    return (
        "<clause-capped-output-repetition>\n"
        f"Host observation of unchanged capped draft {previous.result_ref}: in its first {size} raw output characters, "
        f"one exact {width}-character fragment occurs {count} non-overlapping times; its first three zero-based character offsets are {offsets}. "
        "These are raw-string repetition facts, not a count of findings or a semantic duplicate verdict. "
        "Check whether repeated entries restate an unchanged issue; preserve every distinct supported issue, including different issues sharing the same quotation. "
        "The host has not selected, removed or replaced any finding or quotation. All original source, status, schema and budget requirements still apply.\n"
        "</clause-capped-output-repetition>\n"
    )
