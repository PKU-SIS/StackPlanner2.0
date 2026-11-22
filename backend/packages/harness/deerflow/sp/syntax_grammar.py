"""Opt-in single-request syntax decoding, never host-side answer repair."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping
from copy import deepcopy

from jsonschema import Draft202012Validator, SchemaError
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.sp.evidence_quotes import _annotate_message, _complete_original, _serialized, clause_public_audit_scope
from deerflow.sp.output_contract import _has_external_ref
from deerflow.utils.messages import message_to_text

ANNOTATION = "sp_clause_syntax_grammar"
REQUEST_KEY = "_sp_clause_syntax_grammar"
_FORMAT = "sp_public_format_feedback"
_REASONS = {"unsupported_provider", "source_not_visible", "pending_tools", "existing_constraint", "context_unverified", "already_used"}
_CONSTRAINTS = {"response_format", "json_schema", "regex", "ebnf", "structural_tag", REQUEST_KEY}


def syntax_candidate_enabled(contract, skill_names, auditor_role, candidate) -> bool:
    """Only a real ordinary array with JSON syntax failure can be nominated.

    Ordinary native format/stop/status guards run before this nomination.
    The final request independently proves source visibility and capability.
    """
    if os.environ.get("SP_CLAUSE_SYNTAX_GRAMMAR") != "1" or not clause_public_audit_scope(contract, skill_names, auditor_role):
        return False
    if _has_external_ref(contract["output_schema"]):
        return False
    try:
        Draft202012Validator.check_schema(contract["output_schema"])
    except SchemaError:
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
    """Keep the exact public success shape and the existing honest failure exit."""
    return {
        "anyOf": [
            deepcopy(contract["output_schema"]),
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


class SyntaxGrammarRequestGate:
    """Executor-local one-use gate at the innermost actual model boundary."""

    def __init__(self, captured, *, contract, skill_names, auditor_role, scope):
        self.captured = captured
        self.contract = contract
        self.skill_names = skill_names
        self.auditor_role = auditor_role
        self.scope = scope
        self._used = False
        self._lock = threading.Lock()

    def _record(self, request, status, *, reason=None, backend="none"):
        latest = next((message for message in reversed(request.messages) if isinstance(message, AIMessage)), None)
        info = latest.additional_kwargs.get(_FORMAT) if latest is not None else None
        if not isinstance(info, Mapping):
            return request
        diagnostic = {"status": status, "scope": self.scope, "attempt": info["attempt"], "backend": backend}
        if reason is not None:
            diagnostic["reason"] = reason
        previous = latest.additional_kwargs.get(ANNOTATION)
        records = [*previous, diagnostic] if isinstance(previous, list) else [previous, diagnostic] if isinstance(previous, Mapping) and previous != diagnostic else diagnostic
        candidate = _annotate_message(latest, ANNOTATION, records, self.captured)
        return request.override(messages=[candidate if message is latest else message for message in request.messages])

    def prepare(self, request):
        latest = next((message for message in reversed(request.messages) if isinstance(message, AIMessage)), None)
        info = latest.additional_kwargs.get(_FORMAT) if latest is not None else None
        if not isinstance(info, Mapping) or not info.get("syntax_grammar_candidate") or info.get("scope") != self.scope:
            return None
        if not isinstance(self.contract, Mapping) or not syntax_candidate_enabled(self.contract, self.skill_names, self.auditor_role, latest):
            return None
        if self._used:
            self._record(request, "guard_skip", reason="already_used")
            return None
        try:
            capability = getattr(request.model, "public_syntax_grammar_capability", None)
            supported = callable(capability) and capability() == "sglang"
        except Exception:
            supported = False  # Preserve the ordinary provider path; cancellation still propagates.
        if not supported:
            self._record(request, "guard_skip", reason="unsupported_provider")
            return None
        if _pending_tools(request.messages):
            self._record(request, "guard_skip", reason="pending_tools", backend="sglang")
            return None
        if not _exact_visible_source(request.messages, self.captured, self.contract):
            self._record(request, "guard_skip", reason="source_not_visible", backend="sglang")
            return None
        settings = request.model_settings
        extras = settings.get("extra_body") or {}
        if any(name in settings or name in extras for name in _CONSTRAINTS):
            self._record(request, "guard_skip", reason="existing_constraint", backend="sglang")
            return None
        note = HumanMessage(
            name="sp_clause_syntax_final",
            additional_kwargs={"hide_from_ui": True},
            content=(
                "<syntax-only-final>\nThis is the existing native format-correction turn, temporarily without tools. "
                "The complete original public source remains visible in this exact request. Return the complete raw public JSON array with correct single-layer escaping. "
                "Verify findings against that source; preserve every supported distinct finding and complete original quotation, not broken serialized fragments. "
                "The grammar does not establish substantive completion or choose an answer. If the audit remains incomplete, return the ordinary native SP envelope with "
                'artifact_metadata.completion_status="partial" or "blocked" and concrete nonempty artifact_metadata.evidence_gaps. '
                "Never conceal unfinished work using a default/empty success. All existing budgets and native acceptance/review gates apply.\n</syntax-only-final>"
            ),
        )
        settings = {key: value for key, value in settings.items() if key not in {"tools", "tool_choice", "parallel_tool_calls"}}
        settings.update(stream=False, **{REQUEST_KEY: {"schema": syntax_repair_schema(self.contract), "scope": self.scope, "attempt": info["attempt"], "backend": "sglang"}})
        return request.override(messages=[*request.messages, note], tools=[], model_settings=settings)

    def finish(self, original, proposed, *, within_context):
        if not within_context:
            self._record(original, "guard_skip", reason="context_unverified", backend="sglang")
            return original
        with self._lock:
            if self._used:
                self._record(original, "guard_skip", reason="already_used", backend="sglang")
                return original
            self._used = True
        return self._record(proposed, "applied", backend="sglang")


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
            diagnostic = {key: info[key] for key in ("status", "scope", "attempt", "backend")}
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
