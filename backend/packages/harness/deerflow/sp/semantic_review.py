"""One opt-in, source-only CLAUSE self-review; never host answer correction."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.sp.evidence_quotes import _annotate_message, _complete_original, _public_review_candidate, _serialized, _written_candidate, clause_public_audit_scope
from deerflow.subagents.status_contract import model_response_stop_reason

_REVIEW = "sp_clause_semantic_review"
_FEEDBACK = "sp_clause_semantic_review_feedback"
_KINDS = {"raw_final", "write_file", "file_receipt", "artifact_content"}
_REASONS = {"complete_original_read_unavailable", "conflicting_original_reads", "no_qualified_candidate_after_request", "file_not_rewritten_after_request"}


def clause_semantic_review_enabled(contract: Mapping[str, Any], skill_names: Sequence[str] | None, auditor_role: str | None) -> bool:
    return os.environ.get("SP_CLAUSE_SEMANTIC_REVIEW") == "1" and clause_public_audit_scope(contract, skill_names, auditor_role)


def collect_semantic_review_diagnostics(messages: list, *, scope: str | None = None, finalize: bool = False) -> list[dict]:
    """Export only whitelisted host annotations, without source/candidate text.

    Completed means a subsequent eligible model candidate was received. It does
    not assert that the model's semantic judgment is correct or source-verified.
    """
    diagnostics = []
    for message in messages:
        info = (_serialized(message).get("additional_kwargs") or {}).get(_REVIEW)
        if not isinstance(info, Mapping) or not isinstance(info.get("status"), str) or info["status"] not in {"requested", "completed", "exhausted"}:
            continue
        if not isinstance(info.get("scope"), str) or (scope is not None and info["scope"] != scope) or not isinstance(info.get("candidate_kind"), str) or info["candidate_kind"] not in _KINDS:
            continue
        if isinstance(info.get("attempt"), bool) or not isinstance(info.get("attempt"), int) or info["attempt"] not in (0, 1):
            continue
        if isinstance(info.get("candidate_items"), bool) or not isinstance(info.get("candidate_items"), int) or info["candidate_items"] < 0:
            continue
        if info["status"] in {"requested", "completed"} and info["attempt"] != 1:
            continue
        if info["status"] == "exhausted" and (not isinstance(info.get("reason"), str) or info["reason"] not in _REASONS):
            continue
        diagnostic = {key: info[key] for key in ("status", "scope", "attempt", "candidate_kind", "candidate_items")}
        if isinstance(info.get("reason"), str) and info["reason"] in _REASONS:
            diagnostic["reason"] = info["reason"]
        if diagnostic not in diagnostics:
            diagnostics.append(diagnostic)
    if finalize:
        for requested in list(diagnostics):
            if requested["status"] == "requested" and not any(info["scope"] == requested["scope"] and info["status"] in {"completed", "exhausted"} for info in diagnostics):
                diagnostics.append({**requested, "status": "exhausted", "reason": "no_qualified_candidate_after_request"})
    return diagnostics


def _requested_annotation(messages, scope):
    for message in messages:
        info = (_serialized(message).get("additional_kwargs") or {}).get(_REVIEW)
        if isinstance(info, Mapping) and info.get("scope") == scope and info.get("status") == "requested":
            return info
    return {}


def _review_candidate(latest, text, kind, write_message, messages, contract, scope, captured_sources):
    if _REVIEW in latest.additional_kwargs:
        return None
    history = [*messages, *(captured_sources or [])]
    diagnostics = collect_semantic_review_diagnostics(history, scope=scope)
    if any(info["status"] in {"completed", "exhausted"} for info in diagnostics):
        return None
    requested = next((info for info in diagnostics if info["status"] == "requested"), None)
    info = {"status": "completed" if requested else "requested", "scope": scope, "attempt": 1, "candidate_kind": kind, "candidate_items": len(json.loads(text))}
    write_call_id = _serialized(write_message).get("tool_call_id") if write_message is not None else None
    if requested:
        original_request = _requested_annotation(history, scope)
        if write_call_id and write_call_id == original_request.get("write_call_id"):
            info.update(status="exhausted", reason="file_not_rewritten_after_request")
    else:
        source, unavailable = _complete_original(history, contract["source_requirements"]["files"][0]["total_lines"])
        if source is None:
            info.update(status="exhausted", attempt=0, reason=unavailable)
    if write_call_id:
        info["write_call_id"] = write_call_id  # Internal lineage; never exported.
    candidate = _annotate_message(latest, _REVIEW, info, captured_sources)
    return {"messages": [candidate], **({"jump_to": "model"} if info["status"] == "requested" and isinstance(latest, AIMessage) else {})}


def semantic_review_update(state: Mapping[str, Any], contract: Mapping[str, Any], *, scope: str, captured_sources: list | None = None):
    found = _public_review_candidate(state, contract, captured_sources)
    if found is None:
        return None
    latest, text, kind, write_message = found
    return _review_candidate(latest, text, kind, write_message, state.get("messages") or [], contract, scope, captured_sources)


def semantic_write_review_update(state: Mapping[str, Any], contract: Mapping[str, Any], *, scope: str, captured_sources: list | None = None):
    messages = state.get("messages") or []
    if not messages or not isinstance(messages[-1], ToolMessage) or model_response_stop_reason(messages):
        return None
    written = _written_candidate(messages, contract)
    if written is None or not isinstance(written[1], ToolMessage):
        return None
    requested = _requested_annotation([*messages, *(captured_sources or [])], scope)
    if requested.get("write_call_id") == written[1].tool_call_id:
        return None  # Reading during review is not a new candidate or old receipt.
    # A parallel tool batch can return another tool result after the write.
    # Anchor the transient request on its final result so it reaches the next
    # model call, while retaining the actual write call's separate lineage.
    return _review_candidate(messages[-1], written[0], "write_file", written[1], messages, contract, scope, captured_sources)


def review_update_requests_semantic_pass(update):
    return update is not None and update["messages"][0].additional_kwargs[_REVIEW]["status"] == "requested"


def semantic_review_pending(state, *, scope, captured_sources=None):
    diagnostics = collect_semantic_review_diagnostics([*(state.get("messages") or []), *(captured_sources or [])], scope=scope)
    return any(info["status"] == "requested" for info in diagnostics) and not any(info["status"] in {"completed", "exhausted"} for info in diagnostics)


def state_with_review_update(state, update):
    """Project a metadata-only update for the next check in this same hook."""
    candidate = update["messages"][0]
    messages = [
        candidate
        if (candidate.id and getattr(message, "id", None) == candidate.id)
        or (isinstance(candidate, ToolMessage) and isinstance(message, ToolMessage) and candidate.tool_call_id == message.tool_call_id)
        or (isinstance(candidate, AIMessage) and message is state["messages"][-1] and candidate.content == message.content)
        else message
        for message in state["messages"]
    ]
    return {**state, "messages": messages}


def augment_semantic_review_request(request):
    info = None
    for message in reversed(request.messages):
        if isinstance(message, AIMessage | ToolMessage):
            info = message.additional_kwargs.get(_REVIEW)
            if info is not None or isinstance(message, AIMessage):
                break
    if not isinstance(info, Mapping) or info.get("status") != "requested":
        return request
    file_delivery = info.get("candidate_kind") in {"write_file", "file_receipt"}
    note = HumanMessage(
        name=_FEEDBACK,
        additional_kwargs={"hide_from_ui": True},
        content=(
            "<clause-semantic-review>\n"
            "Perform the one allowed semantic self-review of your first complete candidate in this same audit. "
            "Use only the original public /mnt/user-data/uploads/document.txt read_file evidence already in this audit; if read_file is currently bound, reopen passages as needed. "
            "Review all five public issue families against the quoted source and its surrounding context, definitions and exceptions. "
            "Apply these alternative public-document evidence standards:\n"
            "- Ambiguity: identify materially different plausible readings grounded in the document and explain what remains unresolved by its context.\n"
            "- Inconsistency: identify statements, obligations, dates or quantities that cannot be reconciled within the document, accounting for its definitions and exceptions.\n"
            "- Misaligned terminology: show where the document's labels, definitions or referents fail to align and explain the resulting mismatch.\n"
            "- Omission: identify missing information or an incomplete provision evidenced by the document's own references, obligations or context; do not infer a gap from an external checklist of what a contract should contain.\n"
            "- Structural flaws: identify broken references or numbering, duplication, misplaced text or incomplete operative fragments, and explain their effect on the document's structure or interpretation.\n"
            "A supported finding need not be a contradiction or a pair of incompatible clauses. Do not assume every family is present or add category fields to the public answer. "
            "Merge only genuine duplicate descriptions of the same issue. "
            "Remove a finding only when rechecking the public evidence does not support the claimed issue under these standards; exclude guesses based on assumptions imported from outside the document. "
            "Do not substitute external law, general knowledge, stylistic preferences or speculative improvements for document evidence. "
            "Revisit the whole document for distinct supported issues in any of the five families omitted from your candidate, without limiting the audit to the initial findings. "
            "Retain every supported distinct finding, including multiple different issues in one provision; apply no finding-count limit. "
            "Do not remove findings merely to simplify output, improve a guessed score or avoid checking evidence. Do not return an empty array to conceal unfinished work. "
            "After a complete whole-document review, return [] if you find no supported issue. "
            "Copy complete original source sentences/passages verbatim, preserving spelling, punctuation and internal whitespace, including OCR fragments; do not add decorative quotation wrappers or line labels. "
            + (
                "For this actual file delivery, execute write_file again with append=false and the complete reviewed array at the same current-action path, "
                "even if your review leaves the content unchanged; verify that real write before issuing a receipt. "
                if file_delivery
                else "Return the complete reviewed raw public JSON array, even if your review leaves the content unchanged. "
            )
            + "This is a request for your own source-based judgment, not a host verdict that any particular finding is wrong. The host supplies no replacement text or labels and changes no findings. "
            "If the substantive audit is incomplete, use the ordinary partial/blocked envelope with concrete gaps. All native source, schema, status, tool, token and safety budgets still apply.\n"
            "</clause-semantic-review>"
        ),
    )
    return request.override(messages=[*request.messages, note])


def emit_semantic_review_events(raw_result, writer):
    if not callable(writer):
        return
    for diagnostic in collect_semantic_review_diagnostics(getattr(raw_result, "ai_messages", None) or [], finalize=True):
        try:
            writer({"type": "clause_semantic_review", "action_id": diagnostic["scope"], "diagnostic": diagnostic})
        except Exception:
            pass  # Telemetry cannot alter inference status, content or completion.
