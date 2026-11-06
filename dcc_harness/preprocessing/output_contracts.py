"""Recognize published FinAuditing formats from public instructions only."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dcc_harness.data import PublicTask


@lru_cache(maxsize=1)
def _published_formats() -> tuple[tuple[str, dict], ...]:
    root = Path(__file__).resolve().parents[2] / "configs/dcc/prompts/finauditing"
    schemas = {
        "finre": {
            "output_parse_mode": "text",
            "output_schema": {
                "type": "string",
                "enum": ["Reversal", "Inappropriateness", "CombinationErr"],
            },
        },
        "finsm": {
            "output_parse_mode": "json",
            "output_schema": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
        },
        "finmr": {
            "output_parse_mode": "json",
            "output_schema": {
                "type": "object",
                "required": ["extracted_value", "calculated_value"],
                "additionalProperties": False,
                "properties": {
                    key: {"type": "string", "minLength": 1}
                    for key in ("extracted_value", "calculated_value")
                },
            },
        },
    }
    return tuple(
        ((root / f"{name}.md").read_text().strip(), contract)
        for name, contract in schemas.items()
    )


def public_output_contract(task: PublicTask) -> dict:
    if task.benchmark not in {"dcc", "dcc_files", "dcc_inline"}:
        return {}
    bodies = [task.prompt, *(item.text for item in task.files)]
    matches = [
        contract
        for published, contract in _published_formats()
        if any(published in body for body in bodies)
    ]
    if len(matches) > 1:
        raise ValueError("conflicting published public output contracts")
    # Return independent state, never expose a mutable shared template.
    import copy

    return copy.deepcopy(matches[0]) if matches else {}
