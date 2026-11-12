"""Reuse only identical source facts proven present in this actual model request."""

import copy
import json

from langchain_core.messages import HumanMessage


def preloaded_fact_projection(facts, messages, *, action_id, calculation_runs):
    """No source shortening: equal fields reference the same complete prompt body."""
    present = False
    for message in messages:
        if not isinstance(message, HumanMessage) or not isinstance(message.content, str):
            continue
        marker = "<sp-subagent-task>"
        if marker not in message.content:
            continue
        try:
            frame, _ = json.JSONDecoder().raw_decode(message.content.split(marker, 1)[1].lstrip())
        except (ValueError, TypeError):
            continue
        if isinstance(frame, dict) and frame.get("action_id") == action_id and frame.get("context_refs", {}).get("audit_calculation_runs") == calculation_runs:
            present = True
            break
    if not present:
        return facts
    rows = [(number, row) for number, row in enumerate(calculation_runs) if isinstance(row, dict) and row.get("audit_run_id") == facts.get("audit_run_id")]
    if len(rows) != 1:
        return facts
    number, row = rows[0]
    prefix = f"context_refs.audit_calculation_runs[{number}]"
    result = copy.deepcopy(facts)
    refs = {}
    for key, value in facts.items():
        if key in {"audit_run_id", "rule_id", "m01_evidence_previews"}:
            continue
        if key in row.get("code_facts", {}) and value == row["code_facts"][key]:
            result.pop(key)
            refs[key] = prefix + ".code_facts." + key
        elif key in row and value == row[key]:
            result.pop(key)
            refs[key] = prefix + "." + key
    originals = row.get("m01_navigation", {}).get("candidate_previews", [])
    positions = {}
    for position, item in enumerate(originals):
        if isinstance(item, dict):
            positions.setdefault(item.get("candidate_id"), []).append((position, item))
    aliases = {"offset": "finding_offset", "candidate_path": "path", "paired_sources": "sources"}
    for item in result.get("m01_evidence_previews", []):
        if not isinstance(item, dict):
            continue
        matches = positions.get(item.get("candidate_id"), [])
        if len(matches) != 1:
            continue
        position, original = matches[0]
        item_refs = {}
        for key, value in list(item.items()):
            if key == "candidate_id":
                continue
            source_key = aliases.get(key, key)
            if source_key in original and original[source_key] == value:
                item.pop(key)
                item_refs[key] = prefix + f".m01_navigation.candidate_previews[{position}]." + source_key
        if item_refs:
            item["same_current_prompt_fields"] = item_refs
    if refs:
        result["same_current_prompt_fields"] = refs
    return result
