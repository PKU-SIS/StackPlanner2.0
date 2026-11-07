"""Recognize provider output that still contains unparsed control syntax."""

import json
import re

_PROTOCOL_MARKERS = re.compile(r"DSML|</?tool_call\b|<\|(?:im_start|im_end|eot_id|start_header_id|end_header_id)\|>")


def has_unrecovered_control_syntax(text: str) -> bool:
    """Reject raw call envelopes without interpreting or executing an action."""
    if _PROTOCOL_MARKERS.search(text):
        return True
    candidate = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, re.DOTALL | re.IGNORECASE)
    if fence:
        candidate = fence.group(1)
    try:
        value = json.loads(candidate)
    except (ValueError, TypeError):
        return False
    return isinstance(value, dict) and isinstance(value.get("name"), str) and value["name"].startswith("sp_") and ("arguments" in value or "parameters" in value)
