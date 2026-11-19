"""Opt-in CLAUSE quote review: feedback only, never answer or score repair."""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from deerflow.sp.output_contract import output_contract_gaps
from deerflow.subagents.status_contract import model_response_stop_reason
from deerflow.utils.messages import message_to_text

_REVIEW = "sp_clause_quote_review"
_FEEDBACK = "sp_clause_quote_review_feedback"
_SOURCE = "/mnt/user-data/uploads/document.txt"
_MAX_REVIEWS = 2
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?。！？])(?:[\"'’”\)\]]*)(?=\s+|$)")


def clause_quote_review_enabled(contract: Mapping[str, Any], skill_names: Sequence[str] | None, auditor_role: str | None) -> bool:
    """Use the validated Skill selection and public shape, never private labels."""
    if os.environ.get("SP_CLAUSE_QUOTE_REVIEW") != "1" or auditor_role != "reporter" or "clause" not in (skill_names or []):
        return False
    if contract.get("delivery_mode") != "artifact" or contract.get("output_parse_mode", "json") != "json":
        return False
    schema = contract.get("output_schema")
    item = schema.get("items") if isinstance(schema, Mapping) else None
    properties = item.get("properties") if isinstance(item, Mapping) else None
    if (
        not isinstance(schema, Mapping)
        or schema.get("type") != "array"
        or not isinstance(item, Mapping)
        or item.get("type") != "object"
        or not isinstance(properties, Mapping)
        or set(properties) != {"text", "explanation"}
        or not isinstance(item.get("required"), list)
        or any(not isinstance(key, str) for key in item["required"])
        or set(item["required"]) != {"text", "explanation"}
        or item.get("additionalProperties") is not False
        or any(not isinstance(value, Mapping) or value.get("type") != "string" for value in properties.values())
    ):
        return False
    required = contract.get("source_requirements")
    files = required.get("files") if isinstance(required, Mapping) else None
    return (
        isinstance(required, Mapping)
        and required.get("audit_agent") == "reporter"
        and required.get("document_ids") == []
        and isinstance(files, list)
        and len(files) == 1
        and isinstance(files[0], Mapping)
        and files[0].get("path") == _SOURCE
        and isinstance(files[0].get("total_lines"), int)
        and not isinstance(files[0]["total_lines"], bool)
        and files[0]["total_lines"] > 0
    )


def _serialized(message: Any) -> Mapping[str, Any]:
    return message if isinstance(message, Mapping) else message.model_dump() if hasattr(message, "model_dump") else {}


def _complete_original(messages: list, total_lines: int) -> tuple[str | None, str | None]:
    """Reconstruct only paired, successful, full original native line reads."""
    calls, lines = {}, {}
    for raw in messages:
        message = _serialized(raw)
        kind = message.get("type", message.get("role"))
        if kind in {"ai", "assistant"}:
            calls.update({call["id"]: call for call in message.get("tool_calls", []) if isinstance(call, Mapping) and call.get("id")})
            continue
        if kind != "tool" or message.get("status") == "error":
            continue
        extra = message.get("additional_kwargs") or {}
        if extra.get("sp_host_source_prefetch") and not extra.get("sp_host_source_prefetch_visible"):
            continue
        call = calls.get(message.get("tool_call_id"))
        if not call or call.get("name") not in {"read_file", "read_file_tool"} or call.get("name") != message.get("name"):
            continue
        args, body = call.get("args") or {}, message.get("content")
        if not isinstance(args, Mapping) or args.get("path") != _SOURCE or not isinstance(body, str):
            continue
        if body.startswith(("Error:", "(empty")) or "[truncated:" in body or "[Full read_file output saved to" in body:
            continue
        start, end = args.get("start_line"), args.get("end_line")
        start = 1 if start is None else start
        if isinstance(start, bool) or not isinstance(start, int) or start < 1 or (end is not None and (isinstance(end, bool) or not isinstance(end, int) or end < start)):
            continue
        ranged = args.get("start_line") is not None or end is not None
        observed = body.split("\n") if ranged else body.splitlines()
        # A non-native excess or incomplete response cannot authenticate a file.
        expected_end = min(total_lines, end) if end is not None else total_lines
        if len(observed) != max(0, expected_end - start + 1):
            continue
        for index, value in enumerate(observed, start):
            value = value.removesuffix("\r")
            if index in lines and lines[index] != value:
                return None, "conflicting_original_reads"
            lines[index] = value
    if any(index not in lines for index in range(1, total_lines + 1)):
        return None, "complete_original_read_unavailable"
    return "\n".join(lines[index] for index in range(1, total_lines + 1)), None


def _whitespace(text: str) -> str:
    return " ".join(text.split())


def _supported_quote(quote: str, original: str) -> bool:
    normalized = _whitespace(quote)
    if not normalized:
        return False
    if normalized in original:
        return True
    # Permit noncontiguous/reordered complete sentences from the same document.
    # Deliberately permissive at abbreviations: this is a review hint, not a
    # sentence-completeness proof or a semantic/official quality decision.
    pieces, start = [], 0
    for boundary in _SENTENCE_BOUNDARY.finditer(normalized):
        pieces.append(normalized[start : boundary.end()].strip())
        start = boundary.end()
    if normalized[start:].strip():
        pieces.append(normalized[start:].strip())
    return bool(pieces) and all(piece in original for piece in pieces)


def quote_review_update(state: Mapping[str, Any], contract: Mapping[str, Any], *, scope: str, captured_sources: list | None = None) -> dict[str, Any] | None:
    """Annotate the unchanged final and request at most two model reviews."""
    messages = state.get("messages") or []
    latest = messages[-1] if messages else None
    if not isinstance(latest, AIMessage) or latest.tool_calls or latest.invalid_tool_calls or model_response_stop_reason(messages):
        return None
    extra = latest.additional_kwargs
    if _REVIEW in extra or any(extra.get(key) for key in ("error_type", "hide_from_ui", "tool_calls", "function_call", "subagent_stop_reason", "safety_termination")):
        return None
    if latest.response_metadata.get("finish_reason") in {"tool_calls", "function_call", "content_filter", "safety"} or (extra.get("stackplanner") or {}).get("status") in {"incomplete", "recovery_required", "action_capped", "blocked"}:
        return None
    text = message_to_text(latest)
    if not text.strip() or "[FORCED STOP]" in text or "[TOKEN BUDGET EXCEEDED]" in text or output_contract_gaps({"sp_task_contract": contract}, text):
        return None
    findings = json.loads(text)
    source, unavailable = _complete_original([*messages, *(captured_sources or [])], contract["source_requirements"]["files"][0]["total_lines"])
    attempts = max((info.get("attempt", 0) for message in messages if isinstance(message, AIMessage) and isinstance(info := message.additional_kwargs.get(_REVIEW), Mapping) and info.get("scope") == scope), default=0)
    normalized_source = _whitespace(source) if source is not None else None
    unmatched = [] if normalized_source is None else [index for index, item in enumerate(findings) if not _supported_quote(item["text"], normalized_source)]
    feedback = bool(unmatched) and attempts < _MAX_REVIEWS
    info = {
        "status": "unavailable" if source is None else "checked",
        "scope": scope,
        "checked_items": 0 if source is None else len(findings),
        "unmatched_indices": unmatched,
        "attempt": attempts + 1 if feedback else attempts,
        "feedback": feedback,
        "exhausted": bool(unmatched) and not feedback,
    }
    if unavailable:
        info["reason"] = unavailable
    candidate = latest.model_copy(update={"id": latest.id or str(uuid.uuid4()), "additional_kwargs": {**extra, _REVIEW: info}})
    # Native streaming captures a turn once by ID, before after_model updates.
    # Persist only this host diagnostic in its matching saved metadata; never
    # modify candidate content, tool results, status, labels or artifact bytes.
    for saved in captured_sources or []:
        if isinstance(saved, dict) and latest.id and saved.get("type") == "ai" and saved.get("id") == latest.id and saved.get("content") == latest.content:
            saved["additional_kwargs"] = {**(saved.get("additional_kwargs") or {}), _REVIEW: info}
    return {"messages": [candidate], **({"jump_to": "model"} if feedback else {})}


def augment_quote_review_request(request):
    """Ask the same specialist to verify quotes, without selecting replacements."""
    latest = next((message for message in reversed(request.messages) if isinstance(message, AIMessage)), None)
    info = latest.additional_kwargs.get(_REVIEW) if isinstance(latest, AIMessage) else None
    if not isinstance(info, Mapping) or not info.get("feedback"):
        return request
    note = HumanMessage(
        name=_FEEDBACK,
        additional_kwargs={"hide_from_ui": True},
        content=(
            "<clause-quote-review>\n"
            f"Public-source quotation review {info['attempt']}/{_MAX_REVIEWS}: text fields at zero-based finding indices {info['unmatched_indices']} could not be matched to the complete original document, "
            "even allowing whitespace differences and separately quoted sentences from different locations. This is a conservative diagnostic, not an official score or a verdict that the finding is false. "
            "Review each flagged quotation against /mnt/user-data/uploads/document.txt with the allowed native read tools and original source context. "
            "For each supported distinct finding, quote its exact original complete sentences/passages; preserve spelling, values and punctuation. "
            "Noncontiguous complete sentences from the same document are permitted. Do not paraphrase quotations, infer another contract version, or turn summaries/OCR repairs into source text. "
            "Reconsider whether the explanation is supported by an internal relationship, without inventing defects or imposing a count. "
            "The host has not selected replacement spans or removed findings. Do not drop a supported finding merely to satisfy this diagnostic or replace unfinished work with an empty array. "
            "If the diagnostic appears mistaken, verify the original and retain the supported quotation. Return your complete raw public JSON array, with all supported distinct findings and correctly escaped original quotations. "
            "If the substantive audit is incomplete, use the ordinary honest partial/blocked envelope with concrete gaps. All native source, schema, token, turn and safety gates still apply.\n"
            "</clause-quote-review>"
        ),
    )
    return request.override(messages=[*request.messages, note])
