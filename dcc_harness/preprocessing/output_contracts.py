"""Recognize published source/output contracts from public instructions only."""

from __future__ import annotations

import json
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


@lru_cache(maxsize=1)
def _published_nba_parts() -> tuple[str, str, str]:
    path = (
        Path(__file__).resolve().parents[2]
        / "configs/dcc/prompts/rulearena-nba/task.md"
    )
    before_rules, remainder = path.read_text().strip().split("${rules}", 1)
    between, after_scenario = remainder.split("${document_text}", 1)
    return before_rules, between, after_scenario


def _has_public_nba_instructions(body: str) -> bool:
    remaining = body
    for part in _published_nba_parts():
        position = remaining.find(part)
        if position < 0:
            return False
        remaining = remaining[position + len(part) :]
    return True


def public_output_contract(task: PublicTask) -> dict:
    if task.benchmark == "clause":
        return {
            "output_file_extension": ".json",
            "output_parse_mode": "json",
            "output_schema": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["text", "explanation"],
                    "additionalProperties": False,
                    "properties": {
                        "text": {"type": "string", "pattern": r"\S"},
                        "explanation": {"type": "string"},
                    },
                },
            },
            "source_requirements": {
                "audit_agent": "reporter",
                "skill_names": ["clause"],
                "document_ids": [],
                "files": [
                    {
                        "path": f"/mnt/user-data/uploads/{item.name}",
                        "total_lines": len(item.text.splitlines()),
                    }
                    for item in task.files
                ],
            },
        }
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

    nba = any(_has_public_nba_instructions(body) for body in bodies)
    if matches and nba:
        raise ValueError("conflicting published public output contracts")
    if not matches and not nba:
        return {}
    contract = copy.deepcopy(matches[0]) if matches else {}
    context = next(
        (
            json.loads(item.text)
            for item in task.files
            if item.name == "dcc-context.json"
        ),
        {},
    )
    contract["source_requirements"] = {
        "audit_agent": "researcher",
        "files": [
            {
                "path": f"/mnt/user-data/uploads/{item.name}",
                "total_lines": len(item.text.splitlines()),
            }
            for item in task.files
        ],
        "document_ids": context.get("retrieval", {}).get("document_ids", []),
    }
    if matches:
        contract["output_file_extension"] = (
            ".json" if contract["output_parse_mode"] == "json" else ".txt"
        )
    return contract
