"""Bounded native feedback for public output formatting; never repair answers."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import hook_config
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.sp.output_contract import output_contract_gaps
from deerflow.subagents.status_contract import model_response_stop_reason
from deerflow.utils.messages import message_to_text

_FEEDBACK = "sp_public_format_feedback"
_MAX_REPAIRS = 2


def has_unexecuted_public_tool_intent(text: Any, contract: Mapping[str, Any]) -> bool:
    """Recognize a schema-invalid call envelope without executing/extracting it."""
    if not isinstance(text, str) or not output_contract_gaps({"sp_task_contract": contract}, text):
        return False
    candidate = text.strip()
    fence = re.fullmatch(r"```(?:json|xml)?\s*(.*?)\s*```", candidate, re.DOTALL | re.IGNORECASE)
    if fence:
        candidate = fence.group(1).strip()
    if re.match(r"<tool_call(?:\s|>)", candidate, re.IGNORECASE):
        return True
    try:
        value = json.loads(candidate)
    except (ValueError, RecursionError):
        return False
    if not isinstance(value, Mapping):
        return False
    function = value.get("function") or value
    return isinstance(function, Mapping) and isinstance(function.get("name"), str) and ("arguments" in function or "parameters" in function)


def public_json_reconstruction_instruction() -> str:
    example = json.dumps({"value": 'a "quoted" phrase'}, ensure_ascii=False)
    return (
        "Use single-layer JSON encoding: structural quotation marks are unescaped; escape literal quotation marks inside each string value exactly once. "
        f"Encoding illustration only, not task content: {example}. "
        "Rebuild malformed serialization from the original sources and verified semantic field values if the same encoding error recurs; "
        "do not preserve damaged escape sequences, copy a merged text/explanation field, or patch serialized fragments as the baseline. "
        "Preserve all distinct supported findings, complete original quotations, and independently verified valid work; verify any untrusted prior finding against the sources. "
        "Return the complete raw value matching the original public schema, not a string containing JSON, a tool argument object, or <tool_call> text. "
        "Never invent findings, omit a supported finding, select a default/empty answer to hide unfinished work, or claim an unexecuted write. "
    )


def public_format_repair_update(state: Mapping[str, Any], contract: Mapping[str, Any], *, central: bool = False, scope: str | None = None) -> dict[str, Any] | None:
    """Retain the actual final candidate and ask the same native model to fix it."""
    if "output_schema" not in contract:
        return None
    messages = state.get("messages") or []
    latest = messages[-1] if messages else None
    if not isinstance(latest, AIMessage) or latest.tool_calls or latest.invalid_tool_calls or model_response_stop_reason(messages):
        return None
    extra = latest.additional_kwargs
    if extra.get("error_type") or extra.get("hide_from_ui") or extra.get("tool_calls") or extra.get("function_call") or extra.get("subagent_stop_reason") or extra.get("safety_termination"):
        return None
    if latest.response_metadata.get("finish_reason") in {"tool_calls", "function_call", "content_filter", "safety"}:
        return None
    if (extra.get("stackplanner") or {}).get("status") in {"incomplete", "recovery_required", "action_capped", "blocked"}:
        return None
    text = message_to_text(latest)
    if not text.strip() or "[FORCED STOP]" in text or "[TOKEN BUDGET EXCEEDED]" in text:
        return None
    try:
        envelope = json.loads(text)
    except (ValueError, RecursionError):
        envelope = None
    if isinstance(envelope, Mapping) and not has_unexecuted_public_tool_intent(text, contract):
        metadata = envelope.get("artifact_metadata") or {}
        if envelope.get("completion_status") in ("partial", "blocked") or (isinstance(metadata, Mapping) and (metadata.get("completion_status") in ("partial", "blocked") or metadata.get("evidence_gaps"))):
            return None
        if not central and "artifact_content" in envelope:
            body = envelope["artifact_content"]
            if body is not None and not output_contract_gaps({"sp_task_contract": contract}, body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)):
                return None
            paths = metadata.get("created_paths") if isinstance(metadata, Mapping) else None
            if isinstance(paths, list):
                from deerflow.sp.subagents.dr2_adapter import _successful_output_writes

                captured = [message.model_dump() if hasattr(message, "model_dump") else message for message in messages]
                if set(path for path in paths if isinstance(path, str)) & set(_successful_output_writes(captured)):
                    # Preserve real file delivery; ordinary acceptance reads
                    # and validates its bytes, rather than trusting this receipt.
                    return None
    gaps = output_contract_gaps({"sp_task_contract": contract}, text)
    if not gaps:
        return None
    scope = scope or str(state.get("sp_loop_run_id") or "native")
    attempts = max(
        (int(info.get("attempt", 0)) for message in messages if isinstance(message, AIMessage) and isinstance(info := message.additional_kwargs.get(_FEEDBACK), Mapping) and info.get("scope") == scope),
        default=0,
    )
    if attempts >= _MAX_REPAIRS:
        if not central:
            return None  # Existing native normalization/acceptance stays partial.
        guarded = latest.model_copy(
            update={"id": latest.id or str(uuid.uuid4()), "additional_kwargs": {**extra, "stackplanner_generated": True, "stackplanner": {"status": "incomplete", "target_agent": "central", "guard": "public_output_contract"}}}
        )
        return {"messages": [guarded], "sp_recovery_exhausted": {"target_agent": "central", "attempts": attempts, "limit": _MAX_REPAIRS, "evidence_gaps": gaps}}
    info = {"attempt": attempts + 1, "scope": scope, "gaps": gaps, "schema": contract["output_schema"], "mode": contract.get("output_parse_mode", "json"), "central": central}
    guarded = latest.model_copy(update={"id": latest.id or str(uuid.uuid4()), "additional_kwargs": {**extra, _FEEDBACK: info}})
    return {"messages": [guarded], "jump_to": "model"}


def augment_public_format_request(request):
    """Inject nonpersistent feedback; original source and draft stay in context."""
    # Native request policies may already have appended transient control
    # notes after the current AI candidate. Inspect the last actual AI turn.
    latest = next((message for message in reversed(request.messages) if isinstance(message, AIMessage)), None)
    info = latest.additional_kwargs.get(_FEEDBACK) if isinstance(latest, AIMessage) else None
    if not isinstance(info, Mapping):
        return request
    text_mode = info["mode"] == "text"
    format_instruction = (
        "Return the complete raw plain-text public value required by the original task and schema. Do not JSON-serialize that text or add surrounding serialization quotes. "
        "For a text enum, return the supported enum text itself; JSON notation quotes in the schema do not add output punctuation. "
        "Preserve literal quotation marks, line breaks, punctuation, and other content actually required by the original task/schema. "
        "Do not add transport wrappers or commentary beyond that required content. "
        if text_mode
        else "Return exactly one complete valid JSON value matching the original public schema, without Markdown fences, separate JSON documents, an SP envelope, promises, or a file receipt. "
        "For a JSON string schema, include its required serialization quotes and escape string content correctly. "
    )
    schema_description = "Public output_schema (describes allowed plain text, not JSON serialization)" if text_mode else "Public output_schema"
    json_delivery = info["mode"] == "json" and not info.get("central")
    json_instruction = (
        "During this JSON correction, write_file/str_replace are temporarily unbound. Return the complete public JSON value directly; native artifact persistence handles that actual value. "
        "Do not emit <tool_call> text, a write_file argument object, or a JSON string containing the entire answer. Those are not executed file writes or public delivery. " + public_json_reconstruction_instruction()
        if json_delivery
        else ""
    )
    note = HumanMessage(
        name=_FEEDBACK,
        additional_kwargs={"hide_from_ui": True},
        content=(
            "<public-format-correction>\n"
            f"Your previous ordinary final failed the public format check (correction {info['attempt']}/{_MAX_REPAIRS}). "
            "Correct your own complete final value now in this same audit context. Preserve all supported findings and original quotations. A malformed serialization is an unverified draft, not a canonical baseline to copy unchanged. "
            "Use the original source and actual draft already in this conversation. Do not invent findings, change the answer to a default, omit supported findings, or return empty/default content to conceal unfinished work. "
            + format_instruction
            + "The native host persists the real complete value when a file is required. A claim that a file was written is not file-tool evidence. "
            + json_instruction
            + "If the substantive audit is actually incomplete, return an honest partial/blocked SP envelope with concrete gaps instead.\n"
            f"Parse mode: {info['mode']}. {schema_description}: {json.dumps(info['schema'], ensure_ascii=False)}\n"
            "Actual format errors: " + "; ".join(info["gaps"]) + "\n</public-format-correction>"
        ),
    )
    changes = {"messages": [*request.messages, note]}
    if json_delivery and hasattr(request, "tools"):
        changes["tools"] = [tool for tool in request.tools if getattr(tool, "name", None) not in {"write_file", "str_replace"}]
    return request.override(**changes)


class PublicArtifactFormatRepairMiddleware(AgentMiddleware):
    """Keep public schema corrections inside the host-bound auditor instance."""

    def __init__(self, contract: Mapping[str, Any], *, action_id: str | None = None, auditor_role: str | None = None, skill_names=None, captured_sources=None, syntax_gate=None):
        self.contract = contract
        self.action_id = action_id
        self.auditor_role = auditor_role
        from deerflow.sp.evidence_quotes import clause_quote_review_enabled

        self.quote_review_enabled = clause_quote_review_enabled(contract, skill_names, auditor_role)
        from deerflow.sp.semantic_review import clause_semantic_review_enabled

        self.semantic_review_enabled = clause_semantic_review_enabled(contract, skill_names, auditor_role)
        self.captured_sources = captured_sources
        self.syntax_skill_names = skill_names
        self.syntax_gate = syntax_gate

    def _nominate_final(self, update, phase):
        if self.syntax_gate is None or update is None or update.get("jump_to") != "model":
            return update
        candidate = update["messages"][0]
        annotation = {"format": _FEEDBACK, "semantic": "sp_clause_semantic_review", "quote": "sp_clause_quote_review"}[phase]
        info = candidate.additional_kwargs.get(annotation) or {}
        if phase == "format":
            eligible = info.get("syntax_grammar_candidate")
        else:
            eligible = info.get("candidate_kind") == "raw_final" and (info.get("status") == "requested" if phase == "semantic" else info.get("feedback") is True)
        if eligible and info.get("scope") == self.action_id:
            self.syntax_gate.nominate(candidate, phase=phase, attempt=info.get("attempt"))
        return update

    def before_model(self, state, runtime):
        # A write_file result is an optional draft under the bound CLAUSE raw
        # final protocol. Keep quote feedback for after_model's actual final
        # candidate (including supported final file/envelope deliveries).
        if not self.semantic_review_enabled:
            return None
        from deerflow.sp.semantic_review import semantic_write_review_update

        captured = self.captured_sources() if self.captured_sources is not None else None
        return semantic_write_review_update(state, self.contract, scope=self.action_id or "native", captured_sources=captured)

    async def abefore_model(self, state, runtime):
        return self.before_model(state, runtime)

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime):
        if self.syntax_gate is not None:
            self.syntax_gate.clear_nomination()
        if self.contract.get("delivery_mode") != "artifact":
            required = self.contract.get("source_requirements")
            if (
                self.contract.get("delivery_mode") != "inline"
                or self.auditor_role != "researcher"
                or not isinstance(required, Mapping)
                or required.get("audit_agent") != "researcher"
                or required.get("document_ids") != []
                or not isinstance(required.get("files"), list)
                or not required["files"]
            ):
                return None
        update = public_format_repair_update(state, self.contract, scope=self.action_id)
        if update is not None and update.get("jump_to") == "model":
            from deerflow.sp.syntax_grammar import syntax_candidate_enabled

            candidate = update["messages"][0]
            if syntax_candidate_enabled(self.contract, self.syntax_skill_names, self.auditor_role, candidate):
                info = candidate.additional_kwargs[_FEEDBACK]
                candidate.additional_kwargs[_FEEDBACK] = {**info, "syntax_grammar_candidate": True}
        if update is not None or (not self.quote_review_enabled and not self.semantic_review_enabled):
            return self._nominate_final(update, "format")
        from deerflow.sp.evidence_quotes import quote_review_update
        from deerflow.sp.semantic_review import review_update_requests_semantic_pass, semantic_review_pending, semantic_review_update, state_with_review_update

        captured = self.captured_sources() if self.captured_sources is not None else None
        update = semantic_review_update(state, self.contract, scope=self.action_id or "native", captured_sources=captured) if self.semantic_review_enabled else None
        if review_update_requests_semantic_pass(update):
            return self._nominate_final(update, "semantic")
        reviewed = state_with_review_update(state, update) if update else state
        if self.semantic_review_enabled and semantic_review_pending(reviewed, scope=self.action_id or "native", captured_sources=captured):
            return update
        quote_update = quote_review_update(reviewed, self.contract, scope=self.action_id or "native", captured_sources=captured) if self.quote_review_enabled else None
        return self._nominate_final(quote_update, "quote") if quote_update else update

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime):
        return self.after_model(state, runtime)

    def wrap_model_call(self, request, handler):
        from deerflow.sp.evidence_quotes import augment_quote_review_request
        from deerflow.sp.semantic_review import augment_semantic_review_request

        request = augment_public_format_request(request)
        request = augment_semantic_review_request(request) if self.semantic_review_enabled else request
        return handler(augment_quote_review_request(request) if self.quote_review_enabled else request)

    async def awrap_model_call(self, request, handler):
        from deerflow.sp.evidence_quotes import augment_quote_review_request
        from deerflow.sp.semantic_review import augment_semantic_review_request

        request = augment_public_format_request(request)
        request = augment_semantic_review_request(request) if self.semantic_review_enabled else request
        return await handler(augment_quote_review_request(request) if self.quote_review_enabled else request)
