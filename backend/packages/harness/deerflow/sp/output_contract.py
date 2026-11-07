"""Validate host-supplied public delivery shapes, without repairing answers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator, SchemaError


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key {key!r}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-JSON constant {value}")


def _has_external_ref(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any((key in {"$ref", "$dynamicRef"} and isinstance(item, str) and not item.startswith("#")) or _has_external_ref(item) for key, item in value.items())
    return isinstance(value, list) and any(_has_external_ref(item) for item in value)


def output_contract_gaps(state: Mapping[str, Any], text: str | None) -> list[str]:
    """Return format gaps only; an empty valid array is not a proven audit."""
    contract = state.get("sp_task_contract")
    if not isinstance(contract, Mapping) or "output_schema" not in contract:
        return []
    schema = contract["output_schema"]
    mode = contract.get("output_parse_mode", "json")
    if mode not in {"json", "text"} or not isinstance(schema, dict) or _has_external_ref(schema):
        return ["Public output contract is unsupported; use a local JSON schema and explicit json/text parse mode."]
    if text is None:
        return ["Public output contract requires a complete readable delivery; a missing or truncated preview cannot validate it."]
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        return ["Public output contract contains an invalid JSON schema."]
    try:
        value = json.loads(text, object_pairs_hook=_strict_object, parse_constant=_reject_constant) if mode == "json" else text
    except (ValueError, RecursionError) as exc:
        return [f"Public output must be exactly one valid JSON value without fences or explanation: {str(exc)[:220]}"]
    errors = []
    for error in Draft202012Validator(schema).iter_errors(value):
        path = "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.absolute_path)
        errors.append(f"Public output schema mismatch at {path}: {error.message[:240]}")
        if len(errors) >= 5:
            break
    return errors
