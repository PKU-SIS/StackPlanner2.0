"""Opt-in CLAUSE quote review: feedback only, never answer or score repair."""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

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


def _annotate_review(latest, findings, messages, contract, *, scope, captured_sources, candidate_kind):
    extra = latest.additional_kwargs
    if _REVIEW in extra:
        return None
    source, unavailable = _complete_original([*messages, *(captured_sources or [])], contract["source_requirements"]["files"][0]["total_lines"])
    attempts = max((info["attempt"] for info in collect_quote_review_diagnostics([*messages, *(captured_sources or [])], scope=scope)), default=0)
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
        "candidate_kind": candidate_kind,
    }
    if unavailable:
        info["reason"] = unavailable
    candidate = latest.model_copy(update={"id": latest.id or str(uuid.uuid4()), "additional_kwargs": {**extra, _REVIEW: info}})
    # Native streaming captures a turn once by ID, before after_model updates.
    # Persist only this host diagnostic in its matching saved metadata; never
    # modify candidate content, tool results, status, labels or artifact bytes.
    for saved in captured_sources or []:
        if isinstance(saved, dict) and latest.id and saved.get("type") == latest.type and saved.get("id") == latest.id and saved.get("content") == latest.content:
            saved["additional_kwargs"] = {**(saved.get("additional_kwargs") or {}), _REVIEW: info}
    return candidate


def _written_candidate(messages, contract, *, selected_paths=None):
    """Inspect complete arguments of real successful writes, never file bytes."""
    from deerflow.sp.subagents.dr2_adapter import _successful_output_writes

    calls, candidates = {}, {}
    for raw in messages:
        message = _serialized(raw)
        if message.get("type", message.get("role")) in {"ai", "assistant"}:
            calls.update({call["id"]: (message, call) for call in message.get("tool_calls", []) if isinstance(call, Mapping) and call.get("id")})
            continue
        pair = calls.get(message.get("tool_call_id")) if message.get("type", message.get("role")) == "tool" else None
        if pair is None:
            continue
        call = pair[1]
        args = call.get("args") or {}
        path = args.get("path") if isinstance(args, Mapping) else None
        if call.get("name") not in {"write_file", "str_replace"} or not isinstance(path, str) or not path.lower().endswith(".json") or (selected_paths is not None and path not in selected_paths):
            continue
        # A later mutation (including a failed one) invalidates the old snapshot.
        candidates.pop(path, None)
        if not _successful_output_writes([pair[0], message]) or call.get("name") != "write_file" or args.get("append", False) is not False:
            continue
        content, structured = args.get("content"), args.get("json_content")
        if (content is not None) == (structured is not None):
            continue
        try:
            text = content if isinstance(content, str) else json.dumps(structured, ensure_ascii=False, allow_nan=False) if isinstance(structured, list | dict) else None
        except (ValueError, TypeError):
            continue
        if text is not None and not output_contract_gaps({"sp_task_contract": contract}, text):
            candidates[path] = (text, raw)
    if selected_paths is not None and len(candidates) != 1:
        return None  # Do not choose among ambiguous file deliveries.
    return next(reversed(candidates.values()), None) if candidates else None


def quote_write_review_update(state: Mapping[str, Any], contract: Mapping[str, Any], *, scope: str, captured_sources: list | None = None) -> dict[str, Any] | None:
    """Review an executed write before the next ordinary specialist model turn."""
    messages = state.get("messages") or []
    if not messages or not isinstance(messages[-1], ToolMessage) or model_response_stop_reason(messages):
        return None
    written = _written_candidate(messages, contract)
    if written is None or not isinstance(written[1], ToolMessage):
        return None
    candidate = _annotate_review(written[1], json.loads(written[0]), messages, contract, scope=scope, captured_sources=captured_sources, candidate_kind="write_file")
    return {"messages": [candidate]} if candidate is not None else None


def quote_review_update(state: Mapping[str, Any], contract: Mapping[str, Any], *, scope: str, captured_sources: list | None = None) -> dict[str, Any] | None:
    """Review an unchanged final value or an actually written file receipt."""
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
    if not text.strip() or "[FORCED STOP]" in text or "[TOKEN BUDGET EXCEEDED]" in text:
        return None
    kind = "raw_final"
    if output_contract_gaps({"sp_task_contract": contract}, text):
        try:
            envelope = json.loads(text)
        except (ValueError, TypeError):
            return None
        if not isinstance(envelope, Mapping):
            return None
        metadata = envelope.get("artifact_metadata") or {}
        if not isinstance(metadata, Mapping) or envelope.get("completion_status") in ("partial", "blocked") or metadata.get("completion_status") in ("partial", "blocked") or metadata.get("evidence_gaps"):
            return None
        paths = metadata.get("created_paths")
        written = _written_candidate([*messages, *(captured_sources or [])], contract, selected_paths=paths) if isinstance(paths, list) and paths else None
        if written is not None:
            text, kind = written[0], "file_receipt"
        else:
            body = envelope.get("artifact_content")
            text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            if output_contract_gaps({"sp_task_contract": contract}, text):
                return None
            kind = "artifact_content"
    candidate = _annotate_review(latest, json.loads(text), messages, contract, scope=scope, captured_sources=captured_sources, candidate_kind=kind)
    if candidate is None:
        return None
    return {"messages": [candidate], **({"jump_to": "model"} if candidate.additional_kwargs[_REVIEW]["feedback"] else {})}


def collect_quote_review_diagnostics(messages: list, *, scope: str | None = None) -> list[dict]:
    """Export only host message annotations, never model envelope claims/content."""
    diagnostics = []
    for message in messages:
        info = (_serialized(message).get("additional_kwargs") or {}).get(_REVIEW)
        if not isinstance(info, Mapping) or info.get("status") not in {"checked", "unavailable"} or not isinstance(info.get("scope"), str) or (scope is not None and info["scope"] != scope):
            continue
        if any(isinstance(info.get(key), bool) or not isinstance(info.get(key), int) or info[key] < 0 for key in ("checked_items", "attempt")) or info["attempt"] > _MAX_REVIEWS:
            continue
        indices = info.get("unmatched_indices")
        if not isinstance(indices, list) or any(isinstance(index, bool) or not isinstance(index, int) or index < 0 for index in indices) or any(not isinstance(info.get(key), bool) for key in ("feedback", "exhausted")):
            continue
        diagnostic = {key: info[key] for key in ("status", "scope", "checked_items", "unmatched_indices", "attempt", "feedback", "exhausted")}
        if info.get("candidate_kind") in {"raw_final", "write_file", "file_receipt", "artifact_content"}:
            diagnostic["candidate_kind"] = info["candidate_kind"]
        if info.get("reason") in {"conflicting_original_reads", "complete_original_read_unavailable"}:
            diagnostic["reason"] = info["reason"]
        if diagnostic not in diagnostics:
            diagnostics.append(diagnostic)
    return diagnostics


def emit_quote_review_events(raw_result, writer) -> None:
    """Emit content-free diagnostics at the outer native result-observer boundary."""
    if not callable(writer):
        return
    for diagnostic in collect_quote_review_diagnostics(getattr(raw_result, "ai_messages", None) or []):
        try:
            writer({"type": "clause_quote_review", "action_id": diagnostic["scope"], "diagnostic": diagnostic})
        except Exception:
            # Diagnostic storage must not alter inference completion or output.
            pass


def augment_quote_review_request(request):
    """Ask the same specialist to verify quotes, without selecting replacements."""
    info = None
    for message in reversed(request.messages):
        if isinstance(message, AIMessage | ToolMessage):
            info = message.additional_kwargs.get(_REVIEW)
            if info is not None or isinstance(message, AIMessage):
                break
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
            "Reopen the original with read_file to verify flagged spans. Preserve OCR word-internal spaces, broken words and character fragments exactly; do not reconstruct a more fluent version. "
            "Do not add decorative literal quotation marks around text: JSON string delimiters are serialization, not extra quotation marks belonging inside the text value. "
            "Noncontiguous complete sentences from the same document are permitted. Do not paraphrase quotations, infer another contract version, or turn summaries/OCR repairs into source text. "
            "Reconsider whether the explanation is supported by an internal relationship, without inventing defects or imposing a count. "
            "The host has not selected replacement spans or removed findings. Do not drop a supported finding merely to satisfy this diagnostic or replace unfinished work with an empty array. "
            "If the diagnostic appears mistaken, verify the original and retain the supported quotation. "
            + (
                "This review concerns an actually executed file write. If revising that file delivery, call native write_file again with append=false "
                "and the complete revised array at the same current-action output path, and verify it with real tools. "
                "A receipt, promise, or different inline array does not rewrite the existing file. Do not claim a write without executing it. "
                if info.get("candidate_kind") in {"write_file", "file_receipt"}
                else "Return your complete raw public JSON array, with all supported distinct findings and correctly escaped original quotations. "
            )
            + "If the substantive audit is incomplete, use the ordinary honest partial/blocked envelope with concrete gaps. All native source, schema, token, turn and safety gates still apply.\n"
            "</clause-quote-review>"
        ),
    )
    return request.override(messages=[*request.messages, note])
