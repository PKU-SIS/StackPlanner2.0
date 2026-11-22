"""Opt-in native final-request syntax decoding, never host-side answer repair."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass

from jsonschema import Draft202012Validator, SchemaError, ValidationError
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.sp.evidence_quotes import _annotate_message, _complete_original, _serialized, clause_public_audit_scope, source_character_fidelity_instruction
from deerflow.sp.output_contract import _has_external_ref
from deerflow.subagents.status_contract import model_response_stop_reason
from deerflow.utils.messages import message_to_text

ANNOTATION = "sp_clause_syntax_grammar"
REQUEST_KEY = "_sp_clause_syntax_grammar"
_FORMAT = "sp_public_format_feedback"
_REASONS = {"unsupported_provider", "source_not_visible", "pending_tools", "existing_constraint", "context_unverified", "already_used", "stale_nomination", "stopped"}
_CONSTRAINTS = {"response_format", "json_schema", "regex", "ebnf", "structural_tag", REQUEST_KEY}
_PHASE_ATTEMPTS = {"format": {1, 2}, "semantic": {1}, "quote": {1, 2}}
_PHASE_FEEDBACK = {"format": _FORMAT, "semantic": "sp_clause_semantic_review_feedback", "quote": "sp_clause_quote_review_feedback"}


def _syntax_scope_enabled(contract, skill_names, auditor_role) -> bool:
    if os.environ.get("SP_CLAUSE_SYNTAX_GRAMMAR") != "1" or not clause_public_audit_scope(contract, skill_names, auditor_role):
        return False
    if _has_external_ref(contract["output_schema"]):
        return False
    try:
        Draft202012Validator.check_schema(contract["output_schema"])
    except SchemaError:
        return False
    return True


def syntax_candidate_enabled(contract, skill_names, auditor_role, candidate) -> bool:
    """Only an ordinary array with JSON syntax failure nominates format repair."""
    if not _syntax_scope_enabled(contract, skill_names, auditor_role):
        return False
    text = message_to_text(candidate)
    if not text.lstrip().startswith("["):
        return False
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return True
    except (ValueError, RecursionError):
        pass
    return False


def syntax_repair_schema(contract) -> dict:
    """Project public syntax for decoding; retain native host validation and exits."""
    success_schema = deepcopy(contract["output_schema"])
    text_schema = success_schema["items"]["properties"]["text"]
    if text_schema.get("pattern") == r"\S":
        # XGrammar anchors patterns on serialized characters, narrowing this
        # public substring search to one character and mishandling JSON escapes.
        # Its standard string grammar preserves JSON syntax; the original host
        # schema still enforces nonblank text after decoding the model response.
        del text_schema["pattern"]
    return {
        "anyOf": [
            success_schema,
            {
                "type": "object",
                "properties": {
                    "artifact_metadata": {
                        "type": "object",
                        "properties": {"completion_status": {"enum": ["partial", "blocked"]}, "evidence_gaps": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1}},
                        "required": ["completion_status", "evidence_gaps"],
                        "additionalProperties": True,
                    },
                },
                "required": ["artifact_metadata"],
                "additionalProperties": True,
            },
        ]
    }


def _pending_tools(messages) -> bool:
    pending = {}
    for message in messages:
        if isinstance(message, AIMessage):
            if message.invalid_tool_calls:
                return True
            for call in message.tool_calls:
                if not call.get("id") or call["id"] in pending:
                    return True
                pending[call["id"]] = call.get("name")
        elif isinstance(message, ToolMessage):
            if message.tool_call_id not in pending or (message.name is not None and pending[message.tool_call_id] != message.name):
                return True
            pending.pop(message.tool_call_id)
    return bool(pending)


def _exact_visible_source(messages, captured, contract) -> bool:
    """Authenticate current pairs against native originals, not a read ledger.

    Captured originals absent from this exact request cannot prove visibility.
    Matching names/IDs without exact args and body equality are insufficient.
    """
    saved_calls, saved_results = {}, {}
    for raw in captured:
        saved = _serialized(raw)
        if saved.get("type", saved.get("role")) in {"ai", "assistant"}:
            for call in saved.get("tool_calls") or []:
                if isinstance(call, Mapping) and call.get("id"):
                    previous = saved_calls.get(call["id"])
                    if previous is not None and previous != call:
                        return False
                    saved_calls[call["id"]] = call
        elif saved.get("type", saved.get("role")) == "tool":
            key = (saved.get("tool_call_id"), saved.get("name"))
            previous = saved_results.get(key)
            if previous is not None and previous.get("content") != saved.get("content"):
                return False
            saved_results[key] = saved
    current_calls = {call.get("id"): call for message in messages if isinstance(message, AIMessage) for call in message.tool_calls}
    original_pairs = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        call = current_calls.get(message.tool_call_id)
        saved = saved_results.get((message.tool_call_id, message.name))
        if not call or call != saved_calls.get(message.tool_call_id) or not saved or message.status != "success" or saved.get("status", "success") != "success" or message.content != saved.get("content"):
            continue
        original_pairs.extend(
            [
                {"type": "ai", "tool_calls": [call]},
                {**saved, "additional_kwargs": {**(saved.get("additional_kwargs") or {}), "sp_host_source_prefetch_visible": True}},
            ]
        )
    source, unavailable = _complete_original(original_pairs, contract["source_requirements"]["files"][0]["total_lines"])
    return source is not None and unavailable is None


def clause_source_ready_enabled(contract, skill_names, auditor_role) -> bool:
    """Independent opt-in for the authenticated public CLAUSE audit only."""
    return (
        os.environ.get("SP_CLAUSE_SOURCE_READY_NO_REREAD") == "1"
        and isinstance(contract, Mapping)
        and isinstance(skill_names, list | tuple)
        and clause_public_audit_scope(contract, skill_names, auditor_role)
    )


def clause_source_ready_instruction() -> str:
    """Explain source readiness without certifying the audit or inventing EOF gaps."""
    return (
        "[HOST CLAUSE SOURCE-READY MODE] "
        "The complete required original public source is currently visible in this exact model input as authenticated successful native read_file results. "
        "Review that original evidence and perform the requested audit. Repeated read_file/grep tools are temporarily unavailable while the complete originals remain visible. "
        "Source defects, physical line breaks and candidate quotation mismatches are audit content or candidate issues; they do not by themselves establish missing source evidence. "
        "Preserve source characters and every supported distinct finding, and return the exact raw public output required by the task contract. "
        "The host has not performed the substantive audit, verified your quotations or selected an answer. "
        "Source readiness does not establish task completion. Do not fabricate findings or conceal unfinished work using a default/empty success answer. "
        "If a mandatory requirement genuinely remains unresolved, return the ordinary partial/blocked failure envelope with concrete evidence_gaps. "
        "All existing native source, quotation, schema, status, token, turn, loop, safety and acceptance checks still apply. "
    )


def _current_source_visibility_instruction(contract) -> str:
    """Render only an already-authenticated original public file boundary."""
    file = contract["source_requirements"]["files"][0]
    path, total = file["path"], file["total_lines"]
    return (
        "[HOST CURRENT PUBLIC-SOURCE TRANSPORT FACTS] "
        f"{path}: required physical source lines 1..{total} are completely visible in this exact request as authenticated successful original native read_file results. "
        "These are physical newline-delimited source lines; long lines can wrap into many visual display lines. "
        "The physical line count is not a page or sentence count or a content-length limit. "
        f"Physical source line {total + 1} is beyond EOF of this required original file; an empty request starting there does not indicate missing required content. "
        "A candidate quotation mismatch, when reported, is not a source-read failure or proof of truncated or unavailable source. "
        "These transport facts are not substantive audit completion or a certification of quotation accuracy. "
        "Recheck candidate quotations against the original evidence; preserve every supported distinct finding and honest partial/blocked status for genuine unmet requirements. "
        "All existing source, quotation, schema, status, tool, token and acceptance checks still apply. "
    )


def clause_current_source_visibility_note(messages, captured, *, contract, skill_names, auditor_role):
    """Certify current original transport only, independent of syntax opt-in."""
    if not isinstance(contract, Mapping) or not isinstance(skill_names, list | tuple) or not clause_public_audit_scope(contract, skill_names, auditor_role):
        return None
    if _pending_tools(messages) or not _exact_visible_source(messages, captured, contract):
        return None
    return HumanMessage(name="sp_clause_current_source_visibility", content=_current_source_visibility_instruction(contract), additional_kwargs={"hide_from_ui": True})


@dataclass(frozen=True)
class _FinalNomination:
    phase: str
    attempt: int
    candidate_id: str
    content: object


class SyntaxGrammarRequestGate:
    """Consume only fresh native nominations, once per existing final phase."""

    def __init__(self, captured, *, contract, skill_names, auditor_role, scope):
        self.captured = captured
        self.contract = contract
        self.skill_names = skill_names
        self.auditor_role = auditor_role
        self.scope = scope
        self._pending = None
        self._issued = set()
        self._used = set()
        self._prepared = {}
        self._lock = threading.Lock()

    def clear_nomination(self):
        with self._lock:
            self._pending = None

    def nominate(self, candidate, *, phase, attempt):
        """Trusted middleware calls this only after a real native re-entry decision.

        This run-local state is never deserialized from tools, client state,
        message metadata or provider output. Old phase/attempts cannot reopen it.
        """
        if not isinstance(phase, str) or phase not in _PHASE_ATTEMPTS or type(attempt) is not int or attempt not in _PHASE_ATTEMPTS[phase] or not isinstance(candidate, AIMessage) or not candidate.id:
            return False
        with self._lock:
            key = (phase, attempt)
            if key in self._issued:
                return False
            self._issued.add(key)
            self._pending = _FinalNomination(phase, attempt, candidate.id, deepcopy(candidate.content))
        return True

    def _record(self, request, nomination, status, *, reason=None, backend="none"):
        latest = next((message for message in reversed(request.messages) if isinstance(message, AIMessage)), None)
        if latest is None or latest.id != nomination.candidate_id or latest.content != nomination.content:
            return request
        diagnostic = {"status": status, "scope": self.scope, "phase": nomination.phase, "attempt": nomination.attempt, "backend": backend}
        if reason is not None:
            diagnostic["reason"] = reason
        previous = latest.additional_kwargs.get(ANNOTATION)
        records = [*previous, diagnostic] if isinstance(previous, list) else [previous, diagnostic] if isinstance(previous, Mapping) and previous != diagnostic else diagnostic
        candidate = _annotate_message(latest, ANNOTATION, records, self.captured)
        return request.override(messages=[candidate if message is latest else message for message in request.messages])

    def prepare(self, request):
        with self._lock:
            nomination, self._pending = self._pending, None
        if nomination is None:
            return None
        latest = next((message for message in reversed(request.messages) if isinstance(message, AIMessage)), None)
        position = next((index for index in reversed(range(len(request.messages))) if request.messages[index] is latest), -1)
        tail = request.messages[position + 1 :] if position >= 0 else []
        if (
            latest is None
            or latest.id != nomination.candidate_id
            or latest.content != nomination.content
            or not tail
            or any(not isinstance(message, HumanMessage) or not message.additional_kwargs.get("hide_from_ui") or message.name not in _PHASE_FEEDBACK.values() for message in tail)
            or not any(message.name == _PHASE_FEEDBACK[nomination.phase] for message in tail)
        ):
            self._record(request, nomination, "guard_skip", reason="stale_nomination")
            return None
        extra = latest.additional_kwargs
        if (
            latest.tool_calls
            or latest.invalid_tool_calls
            or model_response_stop_reason(request.messages)
            or any(extra.get(key) for key in ("error_type", "hide_from_ui", "tool_calls", "function_call", "subagent_stop_reason", "safety_termination"))
            or latest.response_metadata.get("finish_reason") in {"tool_calls", "function_call", "content_filter", "safety"}
            or (extra.get("stackplanner") or {}).get("status") in {"incomplete", "recovery_required", "action_capped", "blocked"}
        ):
            self._record(request, nomination, "guard_skip", reason="stopped")
            return None
        if not isinstance(self.contract, Mapping) or not _syntax_scope_enabled(self.contract, self.skill_names, self.auditor_role):
            return None
        if nomination.phase == "format":
            if not syntax_candidate_enabled(self.contract, self.skill_names, self.auditor_role, latest):
                return None
        else:
            try:
                Draft202012Validator(self.contract["output_schema"]).validate(json.loads(message_to_text(latest)))
            except (ValueError, RecursionError, ValidationError):
                return None
        try:
            capability = getattr(request.model, "public_syntax_grammar_capability", None)
            supported = callable(capability) and capability() == "sglang"
        except Exception:
            supported = False  # Preserve the ordinary provider path; cancellation still propagates.
        if not supported:
            self._record(request, nomination, "guard_skip", reason="unsupported_provider")
            return None
        if _pending_tools(request.messages):
            self._record(request, nomination, "guard_skip", reason="pending_tools", backend="sglang")
            return None
        if not _exact_visible_source(request.messages, self.captured, self.contract):
            self._record(request, nomination, "guard_skip", reason="source_not_visible", backend="sglang")
            return None
        settings = request.model_settings
        extras = settings.get("extra_body") or {}
        if request.response_format is not None or any(name in settings or name in extras for name in _CONSTRAINTS):
            self._record(request, nomination, "guard_skip", reason="existing_constraint", backend="sglang")
            return None
        note = HumanMessage(
            name="sp_clause_syntax_final",
            additional_kwargs={"hide_from_ui": True},
            content=(
                f"<syntax-only-final>\nThis is the existing native {nomination.phase} final turn, temporarily without tools. "
                "The complete original public source remains visible in this exact request. Return the complete raw public JSON array with correct single-layer escaping. "
                "Verify findings against that source; preserve every supported distinct finding and complete original quotation, not broken serialized fragments. "
                + (clause_source_ready_instruction() if clause_source_ready_enabled(self.contract, self.skill_names, self.auditor_role) else _current_source_visibility_instruction(self.contract))
                + source_character_fidelity_instruction()
                + "The grammar does not establish substantive completion or choose an answer. If the audit remains incomplete, return the ordinary native SP envelope with "
                'artifact_metadata.completion_status="partial" or "blocked" and concrete nonempty artifact_metadata.evidence_gaps. '
                "Never conceal unfinished work using a default/empty success. All existing budgets and native acceptance/review gates apply.\n</syntax-only-final>"
            ),
        )
        settings = {key: value for key, value in settings.items() if key not in {"tools", "tool_choice", "parallel_tool_calls"}}
        settings.update(stream=False, **{REQUEST_KEY: {"schema": syntax_repair_schema(self.contract), "scope": self.scope, "phase": nomination.phase, "attempt": nomination.attempt, "backend": "sglang"}})
        proposed = request.override(messages=[*request.messages, note], tools=[], tool_choice=None, model_settings=settings)
        with self._lock:
            self._prepared[id(proposed)] = nomination
        return proposed

    def finish(self, original, proposed, *, within_context):
        with self._lock:
            nomination = self._prepared.pop(id(proposed), None)
        if nomination is None:
            return original
        if not within_context:
            self._record(original, nomination, "guard_skip", reason="context_unverified", backend="sglang")
            return original
        with self._lock:
            key = (nomination.phase, nomination.attempt)
            if key in self._used:
                self._record(original, nomination, "guard_skip", reason="already_used", backend="sglang")
                return original
            self._used.add(key)
        return self._record(proposed, nomination, "applied", backend="sglang")


def collect_syntax_grammar_diagnostics(messages, *, scope=None):
    """Content-free host diagnostics, never schema/body/source text."""
    diagnostics = []
    for message in messages:
        saved = (_serialized(message).get("additional_kwargs") or {}).get(ANNOTATION)
        for info in saved if isinstance(saved, list) else [saved]:
            if not isinstance(info, Mapping) or info.get("status") not in {"applied", "guard_skip", "response_received"} or not isinstance(info.get("scope"), str) or (scope is not None and info["scope"] != scope):
                continue
            if isinstance(info.get("attempt"), bool) or not isinstance(info.get("attempt"), int) or info["attempt"] not in {1, 2} or info.get("backend") not in {"sglang", "none"}:
                continue
            phase = info.get("phase")
            if phase is not None and (not isinstance(phase, str) or phase not in _PHASE_ATTEMPTS or info["attempt"] not in _PHASE_ATTEMPTS[phase]):
                continue
            diagnostic = {key: info[key] for key in ("status", "scope", "attempt", "backend")}
            if phase is not None:
                diagnostic["phase"] = phase
            if info.get("reason") in _REASONS:
                diagnostic["reason"] = info["reason"]
            if diagnostic not in diagnostics:
                diagnostics.append(diagnostic)
    return diagnostics


def emit_syntax_grammar_events(raw_result, writer):
    if not callable(writer):
        return
    for diagnostic in collect_syntax_grammar_diagnostics(getattr(raw_result, "ai_messages", None) or []):
        try:
            writer({"type": "clause_syntax_grammar", "action_id": diagnostic["scope"], "diagnostic": diagnostic})
        except Exception:
            pass  # Diagnostics must not change completion or failure handling.
