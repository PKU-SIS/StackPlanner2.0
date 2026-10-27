"""Agent-facing report tools bound to one host delegation.

Only the report Outline receives ``report_register_research_request``.  The
tool writes the authoritative ledger; the model never supplies report role or
report identity.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Annotated, Any

from langchain.tools import InjectedToolCallId, tool

from deerflow.sp.report import store as report_store
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError, gap_next_action

MAX_REGISTERED_QUESTIONS = 12
MAX_QUESTION_CHARS = 4000
MAX_REFERENCE_QUOTE_CHARS = 4000


def build_register_research_request_tool(
    *,
    paths: report_store.ReportPaths,
    action_id: str,
):
    """Create the Outline-bound research-request registration tool."""

    @tool("report_register_research_request", parse_docstring=True)
    def report_register_research_request(
        questions: list[dict[str, Any]],
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> str:
        """Register Outline-owned research questions in the active report ledger.

        Register a new gap when the question has no existing gap identity. Use
        mode="continue" with the exact gap_id when refining an existing open or
        awaiting_user gap; continuation preserves its identity and round history.
        The host validates references and assigns stable gap ids. Registration
        succeeds even before the first outline tree is accepted.

        Each questions item uses nested objects: requirement_refs is an array of
        {input_id, quote}; materials_checked is an array of {material_id, lines};
        constraints is an object; mode="continue" also includes continue_gap_id.
        For section nodes marked needs_richer_research, pass their accepted
        node_ids and state the deeper themes, dimensions, or source directions
        in the request. If a child writable also depends on that evidence,
        include both (for example node_ids=["n6","n7"]: n6 marked section, n7
        child); the child alone loses the section depth signal. Before the first
        tree is accepted, or for a newly added section not yet accepted, use
        node_ids=[] and then write the returned gap_id into that section's
        gap_ids in the next tree. Only accepted tree nodes may be passed as
        node_ids; acceptance reuses attach_nodes_to_gaps.

        Args:
            questions: Batch of research requests. Each item contains mode,
                question, constraints, why_insufficient, stop_condition,
                requirement_refs, optional node_ids, optional continue_gap_id,
                and optional materials_checked.
        """
        with report_store.report_lock(paths):
            return _register_questions(
                paths=paths,
                action_id=action_id,
                questions=questions,
                tool_call_id=tool_call_id,
            )

    return report_register_research_request


def _register_questions(
    *,
    paths: report_store.ReportPaths,
    action_id: str,
    questions: list[dict[str, Any]],
    tool_call_id: str,
) -> str:
    try:
        ledger = ReportLedger.load(paths)
    except Exception as exc:  # noqa: BLE001 - tool boundary reports instead of crashing
        return _error(f"report_ledger_unavailable: {exc}")
    if not isinstance(questions, list) or not questions:
        return _error("questions must be a non-empty list")
    if len(questions) > MAX_REGISTERED_QUESTIONS:
        return _error(f"at most {MAX_REGISTERED_QUESTIONS} questions may be registered in one call")

    idempotency_key = f"{action_id}:{tool_call_id}"
    tool_calls = ledger.payload.setdefault("tool_calls", {})
    if isinstance(tool_calls.get(idempotency_key), Mapping):
        return json.dumps(dict(tool_calls[idempotency_key]), ensure_ascii=False)

    prepared: list[dict[str, Any]] = []
    try:
        for index, raw in enumerate(questions):
            prepared.append(_validate_question(raw, ledger, index))
        registered: list[dict[str, Any]] = []
        for item in prepared:
            gap = ledger.register_gap(
                mode=item["mode"],
                continue_gap_id=item.get("continue_gap_id"),
                question=item["question"],
                constraints=item["constraints"],
                why_insufficient=item["why_insufficient"],
                stop_condition=item["stop_condition"],
                requirement_refs=item["requirement_refs"],
                node_ids=item["node_ids"],
                materials_checked=item["materials_checked"],
            )
            registered.append(
                {
                    "gap_id": gap["gap_id"],
                    "status": gap["status"],
                    "rounds": gap["rounds"],
                    "allowed_rounds": gap["allowed_rounds"],
                    "next_action": gap_next_action(gap),
                }
            )
    except ReportValidationError as exc:
        return _error(str(exc))

    result = {
        "registered": registered,
        "open_gaps": [
            {
                "gap_id": gap.get("gap_id"),
                "question": gap.get("question"),
                "status": gap.get("status"),
                "rounds": gap.get("rounds"),
                "allowed_rounds": gap.get("allowed_rounds"),
            }
            for gap in ledger.gaps()
            if gap.get("status") not in {"resolved", "not_needed"}
        ],
    }
    tool_calls[idempotency_key] = result
    ledger.save()
    return json.dumps(result, ensure_ascii=False, indent=2)


def _validate_question(raw: Any, ledger: ReportLedger, index: int) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ReportValidationError(f"questions[{index}] must be an object")
    mode = str(raw.get("mode") or "").strip().lower()
    if mode not in {"new", "continue"}:
        raise ReportValidationError(f"questions[{index}].mode must be 'new' or 'continue'")
    continue_gap_id = raw.get("continue_gap_id")
    if continue_gap_id is not None and not isinstance(continue_gap_id, str):
        raise ReportValidationError(f"questions[{index}].continue_gap_id must be a string")
    if mode == "new" and continue_gap_id:
        raise ReportValidationError(f"questions[{index}] mode=new must not include continue_gap_id")
    if mode == "continue" and not continue_gap_id:
        raise ReportValidationError(f"questions[{index}] mode=continue requires continue_gap_id")
    question = str(raw.get("question") or "").strip()
    if not question:
        raise ReportValidationError(f"questions[{index}].question must be non-empty")
    if len(question) > MAX_QUESTION_CHARS:
        raise ReportValidationError(f"questions[{index}].question exceeds {MAX_QUESTION_CHARS} characters")
    why = str(raw.get("why_insufficient") or "").strip()
    stop = str(raw.get("stop_condition") or "").strip()
    if not why:
        raise ReportValidationError(f"questions[{index}].why_insufficient must be non-empty")
    if not stop:
        raise ReportValidationError(f"questions[{index}].stop_condition must be non-empty")
    constraints = raw.get("constraints") or {}
    if not isinstance(constraints, Mapping):
        raise ReportValidationError(f"questions[{index}].constraints must be an object")

    requirement_refs = raw.get("requirement_refs")
    if not isinstance(requirement_refs, list) or not requirement_refs:
        raise ReportValidationError(f"questions[{index}].requirement_refs must contain at least one original requirement")
    normalized_refs: list[dict[str, str]] = []
    for ref_index, ref in enumerate(requirement_refs):
        if not isinstance(ref, Mapping):
            raise ReportValidationError(f"questions[{index}].requirement_refs[{ref_index}] must be an object")
        input_id = str(ref.get("input_id") or "").strip()
        quote = str(ref.get("quote") or "").strip()
        if not input_id or not quote:
            raise ReportValidationError(f"questions[{index}].requirement_refs[{ref_index}] requires input_id and quote")
        if len(quote) > MAX_REFERENCE_QUOTE_CHARS:
            raise ReportValidationError(f"questions[{index}].requirement_refs[{ref_index}].quote is too long")
        if ledger.input(input_id) is None:
            raise ReportValidationError(f"questions[{index}] references unknown input: {input_id}")
        normalized_refs.append({"input_id": input_id, "quote": quote})

    node_ids = raw.get("node_ids") or []
    if not isinstance(node_ids, list):
        raise ReportValidationError(f"questions[{index}].node_ids must be a list")
    normalized_node_ids: list[str] = []
    accepted_nodes = ledger.payload.get("nodes") if isinstance(ledger.payload.get("nodes"), Mapping) else {}
    for node_id in node_ids:
        value = str(node_id or "").strip()
        if not value:
            raise ReportValidationError(f"questions[{index}].node_ids must contain non-empty strings")
        if value not in accepted_nodes:
            raise ReportValidationError(f"questions[{index}] references a node not in the accepted tree: {value}")
        normalized_node_ids.append(value)

    materials_checked = raw.get("materials_checked") or []
    if not isinstance(materials_checked, list):
        raise ReportValidationError(f"questions[{index}].materials_checked must be a list")
    normalized_materials: list[dict[str, Any]] = []
    for material_index, item in enumerate(materials_checked):
        if not isinstance(item, Mapping):
            raise ReportValidationError(f"questions[{index}].materials_checked[{material_index}] must be an object")
        material_id = str(item.get("material_id") or "").strip()
        material = ledger.material(material_id)
        if material is None:
            raise ReportValidationError(f"questions[{index}] checked unknown material: {material_id}")
        entry: dict[str, Any] = {"material_id": material_id}
        lines = item.get("lines")
        if lines is not None:
            entry["lines"] = _validate_line_range(lines, int(material.get("lines") or 0), f"questions[{index}].materials_checked[{material_index}].lines")
        normalized_materials.append(entry)

    return {
        "mode": mode,
        "continue_gap_id": continue_gap_id.strip() if isinstance(continue_gap_id, str) else None,
        "question": question,
        "constraints": {str(key): value for key, value in constraints.items()},
        "why_insufficient": why,
        "stop_condition": stop,
        "requirement_refs": normalized_refs,
        "node_ids": normalized_node_ids,
        "materials_checked": normalized_materials,
    }


def _validate_line_range(value: Any, total_lines: int, field_name: str) -> list[int]:
    if not isinstance(value, list) or len(value) != 2:
        raise ReportValidationError(f"{field_name} must be [start, end]")
    start, end = value
    if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
        raise ReportValidationError(f"{field_name} must contain integers")
    if start < 1 or end < start or (total_lines and end > total_lines):
        raise ReportValidationError(f"{field_name} is outside the material line range 1..{total_lines}")
    return [start, end]


def _error(message: str) -> str:
    return json.dumps({"registered": [], "error": message}, ensure_ascii=False)
