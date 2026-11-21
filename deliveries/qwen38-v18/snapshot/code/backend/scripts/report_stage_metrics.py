"""Pure audit helpers; these do not change agent execution or acceptance."""

import json


def summary_format_issue(text):
    """File existence and length do not prove the result envelope was decoded."""
    if text.lstrip().startswith(("{", "```json")):
        return "raw_json_envelope"
    if "<raw-result-truncated; head and tail preserved>" in text:
        return "truncated_raw_result"
    if any(marker in text for marker in ("[FORCED STOP]", "[Recovered tool evidence]", "<recovered-tool-evidence>")):
        return "recovered_or_forced_output"
    return None


def collect_tool_errors(steps):
    """Include file-tool failures absent from the report research ledger."""
    names = {call["id"]: call.get("name") for step in steps for call in step.get("tool_calls", [])}
    errors = []
    for step in steps:
        if step.get("type") != "tool":
            continue
        content = str(step.get("content", ""))
        try:
            payload = json.loads(content)
        except (ValueError, TypeError):
            payload = None
        if step.get("status") == "error" or content.startswith("Error") or isinstance(payload, dict) and payload.get("error"):
            errors.append({"tool": step.get("name") or names.get(step.get("tool_call_id")), "tool_call_id": step.get("tool_call_id"), "detail": content[:2000]})
    return errors
