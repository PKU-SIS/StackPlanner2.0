"""Bounded input packets for report Outline and Researcher delegations."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from deerflow.sp.report import assembly as report_assembly
from deerflow.sp.report import store as report_store
from deerflow.sp.report import tree as report_tree
from deerflow.sp.report import writing as report_writing
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError, gap_next_action
from deerflow.sp.report.prompts import (
    REPORT_INTEGRATE_REVIEW_PROMPT,
    REPORT_OUTLINE_PROMPT,
    REPORT_RESEARCH_PROMPT,
    REPORT_WRITER_PROMPT,
)

OUTLINE_SUMMARY_MAX_CHARS = 24_000
OUTLINE_TREE_MAX_CHARS = 80_000
RESEARCH_INPUT_MAX_CHARS = 4_000


def _read_text(path: str | Path | None) -> tuple[str, str | None]:
    if not path:
        return "", "missing path"
    try:
        return Path(path).read_text(encoding="utf-8"), None
    except (OSError, UnicodeDecodeError) as exc:
        return "", f"unreadable: {exc}"


def _material_entry(material: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "material_id": material.get("material_id"),
        "source_id": material.get("source_id"),
        "title": material.get("title"),
        "url": material.get("url"),
        "path": material.get("virtual_path"),
        "chars": material.get("chars"),
        "lines": material.get("lines"),
        "content_scope": material.get("content_scope"),
        "upstream_truncation": material.get("upstream_truncation"),
        "preview": material.get("preview"),
        "fetched_at": material.get("fetched_at"),
    }


def _gap_entry(gap: Mapping[str, Any], ledger: ReportLedger) -> dict[str, Any]:
    return {
        "gap_id": gap.get("gap_id"),
        "question": gap.get("question"),
        "status": gap.get("status"),
        "rounds": gap.get("rounds"),
        "allowed_rounds": int(gap.get("allowed_rounds") or 2) + int(gap.get("additional_rounds") or 0),
        "constraints": gap.get("constraints") or {},
        "why_insufficient": gap.get("why_insufficient"),
        "stop_condition": gap.get("stop_condition"),
        "requirement_refs": gap.get("requirement_refs") or [],
        "node_ids": gap.get("node_ids") or [],
        "materials_checked": gap.get("materials_checked") or [],
        "next_action": gap_next_action(gap),
        "outcome": gap.get("outcome"),
        "pending_input_ids": gap.get("pending_input_ids") or [],
        "latest_history": (gap.get("history") or [])[-3:],
    }


def load_current_tree(ledger: ReportLedger) -> dict[str, Any] | None:
    tree_ref = ledger.payload.get("tree")
    if not isinstance(tree_ref, Mapping) or not tree_ref.get("path"):
        return None
    try:
        payload = json.loads(Path(str(tree_ref["path"])).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportValidationError(f"current report tree is unreadable: {exc}") from exc
    return payload if isinstance(payload, dict) else None


def writer_basis(ledger: ReportLedger, node_id: str) -> dict[str, Any]:
    tree = load_current_tree(ledger)
    if tree is None:
        raise ReportValidationError("cannot write a node before an accepted tree")
    nodes = {str(node.get("node_id")): node for node in tree.get("nodes") or [] if isinstance(node, Mapping)}
    node = nodes.get(node_id)
    if node is None or node.get("kind") != "writable":
        raise ReportValidationError(f"write target is not a writable node: {node_id}")
    requirements = {str(item.get("id")): item for item in tree.get("requirements") or [] if isinstance(item, Mapping)}
    fingerprints = report_tree.node_fingerprints(tree)
    return {
        "tree_version": tree.get("version"),
        "node_id": node_id,
        "fingerprint": fingerprints.get(node_id),
        "node_spec": report_tree.node_spec_payload(node, requirements, nodes),
    }


def build_writer_packet(
    ledger: ReportLedger,
    *,
    write_key: str,
    node_id: str,
    draft_virtual_path: str,
    basis: Mapping[str, Any],
    prior_draft: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    tree = load_current_tree(ledger)
    if tree is None:
        raise ReportValidationError("cannot write a node before an accepted tree")
    nodes = {str(node.get("node_id")): node for node in tree.get("nodes") or [] if isinstance(node, Mapping)}
    node = nodes.get(node_id)
    if node is None:
        raise ReportValidationError(f"unknown writer node: {node_id}")
    request = ledger.payload.get("request") if isinstance(ledger.payload.get("request"), Mapping) else {}
    request_text, request_error = _read_text(request.get("path"))
    inputs = []
    for item in ledger.inputs():
        text, error = _read_text(item.get("path"))
        inputs.append(
            {
                "input_id": item.get("input_id"),
                "source": item.get("source"),
                "path": item.get("virtual_path"),
                "text": text,
                "error": error,
            }
        )
    evidence = []
    for item in node.get("evidence") or []:
        if not isinstance(item, Mapping):
            continue
        material = ledger.material(str(item.get("material_id")))
        if material is None:
            continue
        evidence.append(
            {
                "material_id": material.get("material_id"),
                "source_id": material.get("source_id"),
                "title": material.get("title"),
                "url": material.get("url"),
                "path": material.get("virtual_path"),
                "lines": (item.get("locator") or {}).get("lines") if isinstance(item.get("locator"), Mapping) else None,
                "use": item.get("use"),
                "content_scope": material.get("content_scope"),
                "upstream_truncation": material.get("upstream_truncation"),
            }
        )
    prior = None
    if prior_draft is not None:
        prior_text, prior_error = _read_text(prior_draft.get("path"))
        rewrite_source = prior_draft.get("rewrite_source") if isinstance(prior_draft.get("rewrite_source"), Mapping) else {}
        prior = {
            "draft_id": prior_draft.get("draft_id"),
            "path": prior_draft.get("virtual_path"),
            "content": prior_text[:OUTLINE_SUMMARY_MAX_CHARS],
            "content_truncated": len(prior_text) > OUTLINE_SUMMARY_MAX_CHARS,
            "error": prior_error,
            "status": prior_draft.get("status"),
            "reason": rewrite_source.get("reason") or prior_draft.get("disposition_reason"),
        }
    parent_id = node.get("parent_id")
    parent = nodes.get(str(parent_id)) if parent_id else None
    siblings = [{"node_id": item.get("node_id"), "title": item.get("title"), "status": item.get("status")} for item in nodes.values() if item.get("parent_id") == parent_id and item.get("node_id") != node_id]
    return {
        "report_id": ledger.payload.get("report_id"),
        "write_key": write_key,
        "node_id": node_id,
        "draft_path": draft_virtual_path,
        "request": {"text": request_text, "path": ledger.paths.virtual(ledger.paths.request), "error": request_error},
        "inputs": inputs,
        "tree_version": tree.get("version"),
        "node": node,
        "basis": dict(basis),
        "evidence": evidence,
        "context": {
            "parent": {"node_id": parent.get("node_id"), "title": parent.get("title")} if parent else None,
            "siblings": siblings,
            "relations": node.get("relations") or [],
        },
        "prior_draft": prior,
        "instructions": REPORT_WRITER_PROMPT,
    }


def build_integrate_review_packet(ledger: ReportLedger) -> dict[str, Any]:
    inputs_view = report_assembly.review_inputs(ledger)
    candidate = inputs_view["candidate"]
    tree = inputs_view["tree"]
    candidate_text, candidate_error = _read_text(candidate.get("path"))
    tree_text = json.dumps(tree, ensure_ascii=False, indent=2)
    inputs = []
    for item in ledger.inputs():
        text, error = _read_text(item.get("path"))
        inputs.append(
            {
                "input_id": item.get("input_id"),
                "source": item.get("source"),
                "applied": bool(item.get("applied")),
                "path": item.get("virtual_path"),
                "text": text,
                "error": error,
            }
        )
    materials = []
    for item in inputs_view["materials"]:
        text, error = _read_text(item.get("path"))
        materials.append(
            {
                **item,
                "text": text[:OUTLINE_SUMMARY_MAX_CHARS],
                "content_truncated": len(text) > OUTLINE_SUMMARY_MAX_CHARS,
                "error": error,
            }
        )
    request_text, request_error = _read_text(ledger.paths.request)
    return {
        "report_id": ledger.payload.get("report_id"),
        "report_task": "integrate_review",
        "request": {
            "text": request_text,
            "path": ledger.paths.virtual(ledger.paths.request),
            "error": request_error,
        },
        "candidate": {
            "candidate_id": candidate.get("candidate_id"),
            "status": candidate.get("status"),
            "path": candidate.get("virtual_path"),
            "content": candidate_text,
            "content_error": candidate_error,
            "sha256": candidate.get("sha256"),
            "inputs_digest": candidate.get("inputs_digest"),
            "tree_version": candidate.get("tree_version"),
            "edits_applied": candidate.get("edits_applied") or [],
            "source_table": candidate.get("source_table") or [],
            "citation_map": candidate.get("citation_map") or [],
        },
        "candidate_sha256": candidate.get("sha256"),
        "inputs_digest": candidate.get("inputs_digest"),
        "base_digest": candidate.get("base_digest"),
        "tree": {
            "version": tree.get("version"),
            "path": (ledger.payload.get("tree") or {}).get("virtual_path"),
            "content": tree_text,
            "requirements": tree.get("requirements") or [],
        },
        "inputs": inputs,
        "drafts": inputs_view["drafts"],
        "materials": materials,
        "sources": inputs_view["sources"],
        "citation_map": inputs_view["citation_map"],
        "current_review": ledger.payload.get("review"),
        "instructions": REPORT_INTEGRATE_REVIEW_PROMPT,
    }


def build_outline_packet(ledger: ReportLedger) -> dict[str, Any]:
    """Build the full planning view for one Outline delegation."""
    request_text, request_error = _read_text(ledger.paths.request)
    inputs: list[dict[str, Any]] = []
    for item in ledger.inputs():
        text, error = _read_text(item.get("path"))
        inputs.append(
            {
                "input_id": item.get("input_id"),
                "source": item.get("source"),
                "path": item.get("virtual_path"),
                "applied": bool(item.get("applied")),
                "chars": item.get("chars"),
                "text": text,
                "error": error,
            }
        )

    summaries: list[dict[str, Any]] = []
    for item in ledger.payload.get("summaries") or []:
        if not isinstance(item, Mapping):
            continue
        text, error = _read_text(item.get("path"))
        truncated = len(text) > OUTLINE_SUMMARY_MAX_CHARS
        summaries.append(
            {
                "action_id": item.get("action_id"),
                "gap_ids": item.get("gap_ids") or [],
                "path": item.get("virtual_path"),
                "artifact_id": item.get("artifact_id"),
                "chars": len(text),
                "content": text[:OUTLINE_SUMMARY_MAX_CHARS],
                "content_truncated": truncated,
                "error": error,
            }
        )

    tree_payload = ledger.payload.get("tree") if isinstance(ledger.payload.get("tree"), Mapping) else None
    tree_content = ""
    tree_error = None
    tree_truncated = False
    if tree_payload is not None:
        tree_content, tree_error = _read_text(tree_payload.get("path"))
        tree_truncated = len(tree_content) > OUTLINE_TREE_MAX_CHARS
        tree_content = tree_content[:OUTLINE_TREE_MAX_CHARS]

    draft_entries: list[dict[str, Any]] = []
    for item in report_writing.drafts(ledger):
        text, error = _read_text(item.get("path"))
        truncated = len(text) > OUTLINE_SUMMARY_MAX_CHARS
        draft_entries.append(
            {
                "draft_id": item.get("draft_id"),
                "write_key": item.get("write_key"),
                "node_id": item.get("node_id"),
                "path": item.get("virtual_path"),
                "chars": item.get("chars"),
                "sha256": item.get("sha256"),
                "status": item.get("status"),
                "is_current": bool(item.get("is_current")),
                "review_status": item.get("review_status"),
                "generated_fingerprint": item.get("generated_fingerprint"),
                "applicable_fingerprint": item.get("applicable_fingerprint"),
                "keep_reason": item.get("keep_reason"),
                "content": text[:OUTLINE_SUMMARY_MAX_CHARS],
                "content_truncated": truncated,
                "error": error,
            }
        )
    feedback_entries = [
        {
            "feedback_id": item.get("feedback_id"),
            "write_key": item.get("write_key"),
            "node_id": item.get("node_id"),
            "kind": item.get("kind"),
            "message": item.get("message"),
            "material_ids": item.get("material_ids") or [],
            "requirement_ids": item.get("requirement_ids") or [],
            "status": item.get("status"),
            "gap_id": item.get("gap_id"),
        }
        for item in report_writing.feedback_entries(ledger)
        if item.get("status") in {"open", "handed_off"}
    ]
    writing_control = report_writing.control_entries(ledger)

    return {
        "report_id": ledger.payload.get("report_id"),
        "request": {
            "text": request_text,
            "path": ledger.paths.virtual(ledger.paths.request),
            "error": request_error,
        },
        "inputs": inputs,
        "materials": [_material_entry(item) for item in ledger.materials()],
        "material_catalog_path": ledger.paths.virtual(ledger.paths.catalog),
        "summaries": summaries,
        "tree": (
            {
                "version": tree_payload.get("version"),
                "path": tree_payload.get("virtual_path"),
                "content": tree_content,
                "content_truncated": tree_truncated,
                "error": tree_error,
                "last_changes": tree_payload.get("last_changes"),
            }
            if tree_payload is not None
            else None
        ),
        "drafts": draft_entries,
        "write_requests": writing_control["write_requests"],
        "writes_pending": writing_control["writes_pending"],
        "draft_review_due": writing_control["draft_review_due"],
        "feedback": feedback_entries,
        "gaps": [_gap_entry(gap, ledger) for gap in ledger.gaps()],
        "unapplied_input_ids": ledger.unapplied_input_ids(),
        "instructions": REPORT_OUTLINE_PROMPT,
    }


def build_researcher_packet(ledger: ReportLedger, gap_ids: Iterable[str], *, action_id: str) -> dict[str, Any]:
    """Build the evidence-question brief for one Researcher delegation."""
    normalized = [str(item) for item in gap_ids]
    if not normalized:
        raise ReportValidationError("researcher delegation requires at least one report gap")
    gaps: list[dict[str, Any]] = []
    for gap_id in normalized:
        gap = ledger.require_gap(gap_id)
        if gap.get("status") not in {"open", "in_flight"}:
            raise ReportValidationError(f"gap {gap_id} cannot be researched while status={gap.get('status')}")
        refs: list[dict[str, Any]] = []
        for ref in gap.get("requirement_refs") or []:
            if not isinstance(ref, Mapping):
                continue
            input_id = str(ref.get("input_id") or "")
            input_record = ledger.input(input_id)
            text, error = _read_text(input_record.get("path")) if input_record else ("", "missing input")
            refs.append(
                {
                    "input_id": input_id,
                    "quote": ref.get("quote"),
                    "input_text": text[:RESEARCH_INPUT_MAX_CHARS],
                    "input_text_truncated": len(text) > RESEARCH_INPUT_MAX_CHARS,
                    "error": error,
                }
            )
        entry = _gap_entry(gap, ledger)
        entry["requirement_refs"] = refs
        gaps.append(entry)
    return {
        "report_id": ledger.payload.get("report_id"),
        "action_id": action_id,
        "gaps": gaps,
        "materials": [_material_entry(item) for item in ledger.materials()],
        "material_catalog_path": ledger.paths.virtual(ledger.paths.catalog),
        "instructions": REPORT_RESEARCH_PROMPT,
    }


def build_pre_research_packet(ledger: ReportLedger, *, action_id: str) -> dict[str, Any]:
    """Build the orientation/research packet used before the first outline."""
    request_text, request_error = _read_text(ledger.paths.request)
    return {
        "report_id": ledger.payload.get("report_id"),
        "action_id": action_id,
        "pre_research": True,
        "request": {
            "text": request_text,
            "path": ledger.paths.virtual(ledger.paths.request),
            "error": request_error,
        },
        "inputs": [
            {
                "input_id": item.get("input_id"),
                "source": item.get("source"),
                "path": item.get("virtual_path"),
                "text": _read_text(item.get("path"))[0],
                "error": _read_text(item.get("path"))[1],
            }
            for item in ledger.inputs()
        ],
        "materials": [_material_entry(item) for item in ledger.materials()],
        "material_catalog_path": ledger.paths.virtual(ledger.paths.catalog),
        "gaps": [],
        "instructions": REPORT_RESEARCH_PROMPT,
    }


def outline_context_refs(packet: Mapping[str, Any]) -> dict[str, Any]:
    return {"report_packet": dict(packet)}


def researcher_context_refs(packet: Mapping[str, Any]) -> dict[str, Any]:
    return {"report_packet": dict(packet)}


def summary_for_central(ledger: ReportLedger) -> dict[str, Any]:
    """Compact result shape returned by report acceptance to Central."""
    tree = ledger.payload.get("tree") if isinstance(ledger.payload.get("tree"), Mapping) else {}
    return {
        "report_id": ledger.payload.get("report_id"),
        "tree_version": tree.get("version"),
        "open_gaps": [
            {
                "gap_id": gap.get("gap_id"),
                "status": gap.get("status"),
                "rounds": gap.get("rounds"),
                "next_action": gap_next_action(gap),
            }
            for gap in ledger.gaps()
            if gap.get("status") not in {"resolved", "not_needed"}
        ],
        "materials": [item.get("material_id") for item in ledger.materials()],
        "catalog_path": ledger.paths.virtual(ledger.paths.catalog),
        "tree_path": tree.get("virtual_path"),
        "control": ledger.control_summary(),
    }


def read_material_lines(ledger: ReportLedger, material_id: str, start: int, end: int) -> tuple[str, bool]:
    """Read a cited material range; returns (text, truncated)."""
    material = ledger.material(material_id)
    if material is None or not material.get("path"):
        raise ReportValidationError(f"unknown material: {material_id}")
    text = Path(str(material["path"])).read_text(encoding="utf-8")
    lines = text.splitlines()
    selected = lines[max(start - 1, 0) : end]
    return "\n".join(selected), end < len(lines)


def report_paths_from_ledger(ledger: ReportLedger) -> report_store.ReportPaths:
    return ledger.paths
