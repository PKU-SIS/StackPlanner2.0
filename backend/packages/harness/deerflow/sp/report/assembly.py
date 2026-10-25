"""P3 deterministic assembly, citation binding, review inputs, and local edits."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from deerflow.sp.report import store as report_store
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError, utc_now_iso

CITATION_RE = re.compile(r"\[S(\d+)\]")
HEADING_RE = re.compile(r"(?m)^#{1,6}\s")
EDIT_KINDS = frozenset({"bridge", "terminology", "format", "dedupe"})
ACTIVE_CANDIDATE_STATUSES = frozenset({"pending_review", "reviewed", "finalized"})
CANDIDATE_MANIFEST_MAX_CHARS = 500_000


def _tree_document(ledger: ReportLedger) -> dict[str, Any]:
    tree_ref = ledger.payload.get("tree")
    if not isinstance(tree_ref, Mapping) or not tree_ref.get("path"):
        raise ReportValidationError("report assembly requires an accepted tree")
    try:
        payload = json.loads(Path(str(tree_ref["path"])).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportValidationError(f"accepted tree is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReportValidationError("accepted tree is not an object")
    return payload


def _read_text(path: Any, label: str) -> str:
    try:
        return Path(str(path)).read_text(encoding="utf-8")
    except (OSError, TypeError) as exc:
        raise ReportValidationError(f"{label} is unreadable: {exc}") from exc


def _json_digest(value: Any) -> str:
    return report_store.sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _bound_text(record: Mapping[str, Any], label: str, *, strip_trailing_newline: bool = False) -> str:
    content = _read_text(record.get("path"), label)
    bound = content.rstrip("\n") if strip_trailing_newline else content
    expected = record.get("sha256")
    if expected and report_store.sha256_text(bound) != str(expected):
        raise ReportValidationError(f"{label} content hash does not match the ledger")
    return bound


def _draft_for_node(ledger: ReportLedger, node_id: str) -> dict[str, Any] | None:
    raw = ledger.payload.get("drafts")
    items = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    return next((item for item in reversed(items) if item.get("node_id") == node_id and item.get("is_current") is True), None)


def _node_order(tree: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    nodes = [node for node in tree.get("nodes") or [] if isinstance(node, Mapping)]
    by_id = {str(node.get("node_id")): node for node in nodes}
    children: dict[str, list[Mapping[str, Any]]] = {}
    roots: list[Mapping[str, Any]] = []
    for node in nodes:
        parent_id = str(node.get("parent_id") or "")
        if parent_id and parent_id in by_id:
            children.setdefault(parent_id, []).append(node)
        else:
            roots.append(node)
    ordered: list[Mapping[str, Any]] = []
    visited: set[str] = set()

    def walk(node: Mapping[str, Any]) -> None:
        node_id = str(node.get("node_id") or "")
        if node_id in visited:
            raise ReportValidationError(f"accepted tree contains a cycle at node {node_id}")
        visited.add(node_id)
        ordered.append(node)
        for child in children.get(node_id, []):
            walk(child)

    for root in roots:
        walk(root)
    if len(visited) != len(nodes):
        raise ReportValidationError("accepted tree contains nodes unreachable from a root")
    return ordered


def _node_depth(node: Mapping[str, Any], nodes: Mapping[str, Mapping[str, Any]]) -> int:
    depth = 0
    current = node
    seen: set[str] = set()
    while current.get("parent_id"):
        parent_id = str(current.get("parent_id"))
        if parent_id in seen:
            break
        seen.add(parent_id)
        parent = nodes.get(parent_id)
        if parent is None:
            break
        depth += 1
        current = parent
    return depth


def node_heading_level(node: Mapping[str, Any], nodes: Mapping[str, Mapping[str, Any]]) -> int:
    """Return the Markdown heading level the host emits for this node."""
    return min(6, _node_depth(node, nodes) + 2)


def _sections(ledger: ReportLedger, tree: Mapping[str, Any]) -> list[dict[str, Any]]:
    nodes = {str(node.get("node_id")): node for node in _node_order(tree)}
    sections: list[dict[str, Any]] = []
    for node in _node_order(tree):
        node_id = str(node.get("node_id") or "")
        section = {
            "node_id": node_id,
            "parent_id": node.get("parent_id"),
            "kind": str(node.get("kind") or ""),
            "title": str(node.get("title") or "").strip(),
            "level": node_heading_level(node, nodes),
            "body": "",
            "draft_id": None,
        }
        if node.get("kind") == "writable":
            draft = _draft_for_node(ledger, node_id)
            if draft is None:
                raise ReportValidationError(f"assembly blocked: writable node {node_id} has no current draft")
            if draft.get("review_status") == "open":
                raise ReportValidationError(f"assembly blocked: draft {draft.get('draft_id')} still requires feedback review")
            path = draft.get("path")
            content = _read_text(path, f"draft {draft.get('draft_id')}")
            if not content.strip():
                raise ReportValidationError(f"assembly blocked: draft {draft.get('draft_id')} is empty")
            if report_store.sha256_text(content) != str(draft.get("sha256") or ""):
                raise ReportValidationError(f"assembly blocked: draft {draft.get('draft_id')} content hash does not match the ledger")
            section["body"] = content
            section["draft_id"] = draft.get("draft_id")
            section["draft_sha256"] = draft.get("sha256")
            section["applicable_fingerprint"] = draft.get("applicable_fingerprint")
        sections.append(section)
    return sections


def _source_table(ledger: ReportLedger, sections: Iterable[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sources_raw = ledger.payload.get("sources") if isinstance(ledger.payload.get("sources"), list) else []
    sources_by_id = {str(item.get("source_id")): item for item in sources_raw if isinstance(item, Mapping)}
    materials = ledger.materials()
    occurrences: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for section in sections:
        for match in CITATION_RE.finditer(str(section.get("body") or "")):
            source_id = f"S{match.group(1)}"
            if source_id not in sources_by_id:
                raise ReportValidationError(f"citation references unknown source {source_id}")
            used_ids.add(source_id)
            occurrences.append(
                {
                    "source_id": source_id,
                    "node_id": section.get("node_id"),
                    "draft_id": section.get("draft_id"),
                    "offset": match.start(),
                }
            )
    ordered_ids = sorted(used_ids, key=lambda value: int(value[1:]))
    table: list[dict[str, Any]] = []
    for source_id in ordered_ids:
        source = sources_by_id[source_id]
        versions = [
            {
                "material_id": material.get("material_id"),
                "sha256": material.get("sha256"),
                "chars": material.get("chars"),
                "lines": material.get("lines"),
                "path": material.get("virtual_path"),
                "created_at": material.get("created_at"),
            }
            for material in materials
            if str(material.get("source_id")) == source_id
        ]
        versions.sort(key=lambda item: str(item.get("material_id") or ""))
        table.append(
            {
                "source_id": source_id,
                "title": source.get("title"),
                "url": source.get("url"),
                "material_versions": versions,
            }
        )
    return table, occurrences


def _source_list_markdown(source_table: Iterable[Mapping[str, Any]]) -> str:
    lines = ["## 来源", ""]
    entries = 0
    for source in source_table:
        entries += 1
        title = str(source.get("title") or "").strip()
        url = str(source.get("url") or "").strip()
        versions = [str(item.get("material_id") or "") for item in source.get("material_versions") or [] if isinstance(item, Mapping)]
        suffix = f"（material: {', '.join(versions)}）" if versions else ""
        label = f"[{source.get('source_id')}]"
        lines.append(f"{entries}. {label} {title}{(' — ' + url) if url else ''}{suffix}".rstrip())
    if entries == 0:
        lines.append("（无引用）")
    return "\n".join(lines)


def _candidate_text(tree: Mapping[str, Any], sections: Iterable[Mapping[str, Any]], source_table: Iterable[Mapping[str, Any]]) -> str:
    title = str(tree.get("report_title") or "报告").strip()
    parts = [f"# {title}", ""]
    for section in sections:
        parts.extend(["#" * int(section.get("level") or 2) + " " + str(section.get("title") or ""), ""])
        body = str(section.get("body") or "").strip()
        if body:
            parts.extend([body, ""])
    parts.extend([_source_list_markdown(source_table), ""])
    return "\n".join(parts).rstrip() + "\n"


def _draft_binding(ledger: ReportLedger, tree: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in _node_order(tree):
        if node.get("kind") != "writable":
            continue
        node_id = str(node.get("node_id") or "")
        draft = _draft_for_node(ledger, node_id)
        if draft is None:
            raise ReportValidationError(f"assembly blocked: writable node {node_id} has no current draft")
        content = _bound_text(draft, f"draft {draft.get('draft_id')}")
        result.append(
            {
                "node_id": node_id,
                "draft_id": draft.get("draft_id"),
                "sha256": report_store.sha256_text(content),
                "applicable_fingerprint": draft.get("applicable_fingerprint"),
            }
        )
    return result


def base_inputs_digest(ledger: ReportLedger) -> str:
    tree = _tree_document(ledger)
    tree_ref = ledger.payload.get("tree") if isinstance(ledger.payload.get("tree"), Mapping) else {}
    tree_content = _read_text(tree_ref.get("path"), "accepted tree")
    inputs: list[dict[str, Any]] = []
    for item in ledger.inputs():
        text = _bound_text(item, f"user input {item.get('input_id')}", strip_trailing_newline=True)
        inputs.append(
            {
                "input_id": item.get("input_id"),
                "sha256": report_store.sha256_text(text),
                "applied": bool(item.get("applied")),
            }
        )
    materials = []
    for item in ledger.materials():
        content = _bound_text(item, f"material {item.get('material_id')}")
        materials.append(
            {
                "material_id": item.get("material_id"),
                "source_id": item.get("source_id"),
                "sha256": report_store.sha256_text(content),
                "chars": len(content),
            }
        )
    materials.sort(key=lambda item: str(item.get("material_id") or ""))
    payload = {
        "tree_version": tree_ref.get("version"),
        "tree_sha256": report_store.sha256_text(tree_content),
        "requirements": tree.get("requirements") or [],
        "inputs": inputs,
        "drafts": _draft_binding(ledger, tree),
        "materials": materials,
    }
    return _json_digest(payload)


def candidate_inputs_digest(base_digest: str, candidate_sha256: str, edits: Iterable[Mapping[str, Any]]) -> str:
    return _json_digest(
        {
            "base_digest": base_digest,
            "candidate_sha256": candidate_sha256,
            "edits": [dict(item) for item in edits],
        }
    )


def _candidate_manifest(record: Mapping[str, Any], tree: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": record.get("candidate_id"),
        "tree_version": record.get("tree_version"),
        "report_title": tree.get("report_title"),
        "base_digest": record.get("base_digest"),
        "inputs_digest": record.get("inputs_digest"),
        "candidate_sha256": record.get("sha256"),
        "sections": record.get("sections"),
        "source_table": record.get("source_table"),
        "citation_map": record.get("citation_map"),
        "edits_applied": record.get("edits_applied"),
    }


def _write_candidate_files(ledger: ReportLedger, record: dict[str, Any], tree: Mapping[str, Any]) -> None:
    candidate_path = Path(str(record["path"]))
    report_store.write_text_atomic(candidate_path, str(record["content"]))
    read_back = candidate_path.read_text(encoding="utf-8")
    if report_store.sha256_text(read_back) != str(record["sha256"]):
        raise ReportValidationError("candidate file verification failed")
    manifest = _candidate_manifest(record, tree)
    encoded = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    report_store.write_json_atomic(Path(str(record["manifest_path"])), manifest)
    if len(encoded) > CANDIDATE_MANIFEST_MAX_CHARS:
        raise ReportValidationError("candidate manifest exceeds the P3 size bound")


def candidate_file_content(candidate: Mapping[str, Any]) -> str:
    content = _read_text(candidate.get("path"), f"candidate {candidate.get('candidate_id')}")
    if report_store.sha256_text(content) != str(candidate.get("sha256") or ""):
        raise ReportValidationError(f"candidate {candidate.get('candidate_id')} content hash does not match the ledger")
    return content


def _new_candidate(
    ledger: ReportLedger,
    tree: Mapping[str, Any],
    *,
    base_digest: str,
    sections: list[dict[str, Any]],
    edits_applied: list[dict[str, Any]],
    parent_candidate_id: str | None,
) -> dict[str, Any]:
    candidates = ledger.payload.get("candidates") if isinstance(ledger.payload.get("candidates"), list) else []
    candidate_id = report_store.next_identifier("c", [str(item.get("candidate_id")) for item in candidates if isinstance(item, Mapping)])
    source_table, citation_map = _source_table(ledger, sections)
    content = _candidate_text(tree, sections, source_table)
    path = ledger.paths.assembly_file(candidate_id)
    manifest_path = ledger.paths.assembly_manifest(candidate_id)
    sha256 = report_store.sha256_text(content)
    record = {
        "candidate_id": candidate_id,
        "status": "pending_review",
        "tree_version": tree.get("version"),
        "base_digest": base_digest,
        "inputs_digest": candidate_inputs_digest(base_digest, sha256, edits_applied),
        "path": str(path),
        "virtual_path": ledger.paths.virtual(path),
        "manifest_path": str(manifest_path),
        "sha256": sha256,
        "chars": len(content),
        "sections": sections,
        "source_table": source_table,
        "citation_map": citation_map,
        "edits_applied": edits_applied,
        "parent_candidate_id": parent_candidate_id,
        "created_at": utc_now_iso(),
        "content": content,
    }
    _write_candidate_files(ledger, record, tree)
    record.pop("content", None)
    candidates.append(record)
    ledger.payload["candidates"] = candidates
    ledger.payload["candidate"] = record
    return record


def invalidate_review(ledger: ReportLedger, reason: str) -> None:
    review = ledger.payload.get("review")
    if isinstance(review, dict) and review.get("status") in {"ready", "issues", "edits_applied"}:
        review["status"] = "stale"
        review["stale_reason"] = reason
        review["stale_at"] = utc_now_iso()
    review_id = review.get("review_id") if isinstance(review, dict) else None
    reviews = ledger.payload.get("reviews")
    if review_id and isinstance(reviews, list):
        for item in reviews:
            if isinstance(item, dict) and item.get("review_id") == review_id and item.get("status") in {"ready", "issues", "edits_applied"}:
                item["status"] = "stale"
                item["stale_reason"] = reason
                item["stale_at"] = utc_now_iso()
    final = ledger.payload.get("final_report")
    if isinstance(final, dict):
        final["status"] = "superseded"
        final["is_current"] = False
        final["superseded_reason"] = reason
        final["superseded_at"] = utc_now_iso()
    final_reports = ledger.payload.get("final_reports")
    if isinstance(final_reports, list):
        for item in final_reports:
            if isinstance(item, dict) and item.get("status") == "current":
                item["status"] = "superseded"
                item["is_current"] = False
                item["superseded_reason"] = reason
                item["superseded_at"] = utc_now_iso()


def ensure_candidate(ledger: ReportLedger) -> dict[str, Any]:
    tree = _tree_document(ledger)
    base_digest = base_inputs_digest(ledger)
    current = ledger.payload.get("candidate")
    if isinstance(current, dict) and current.get("base_digest") == base_digest and current.get("status") in ACTIVE_CANDIDATE_STATUSES:
        try:
            candidate_file_content(current)
        except ReportValidationError:
            pass
        else:
            return current

    reason = "assembly inputs changed"
    for item in ledger.payload.get("candidates") or []:
        if isinstance(item, dict) and item.get("status") in ACTIVE_CANDIDATE_STATUSES:
            item["status"] = "superseded"
            item["superseded_reason"] = reason
            item["superseded_at"] = utc_now_iso()
    invalidate_review(ledger, reason)
    sections = _sections(ledger, tree)
    record = _new_candidate(ledger, tree, base_digest=base_digest, sections=sections, edits_applied=[], parent_candidate_id=None)
    ledger.save()
    return record


def require_current_candidate(ledger: ReportLedger) -> dict[str, Any]:
    candidate = ledger.payload.get("candidate")
    if not isinstance(candidate, dict):
        raise ReportValidationError("no current assembly candidate")
    if candidate.get("status") not in ACTIVE_CANDIDATE_STATUSES:
        raise ReportValidationError(f"current candidate is not reviewable: {candidate.get('status')}")
    return candidate


def review_binding_error(ledger: ReportLedger, *, require_ready: bool = True) -> str | None:
    candidate = ledger.payload.get("candidate")
    review = ledger.payload.get("review")
    if not isinstance(candidate, dict):
        return "no current assembly candidate"
    if not isinstance(review, dict):
        return "no integration review"
    if candidate.get("status") not in ACTIVE_CANDIDATE_STATUSES:
        return f"candidate is {candidate.get('status')}"
    if review.get("candidate_id") != candidate.get("candidate_id"):
        return "review is bound to a different candidate"
    if review.get("candidate_sha256") != candidate.get("sha256"):
        return "review candidate hash does not match the current candidate"
    if review.get("inputs_digest") != candidate.get("inputs_digest"):
        return "review inputs_digest does not match the current candidate"
    try:
        candidate_file_content(candidate)
    except ReportValidationError as exc:
        return str(exc)
    try:
        current_base = base_inputs_digest(ledger)
    except ReportValidationError as exc:
        return str(exc)
    if review.get("base_digest") != current_base:
        return "review inputs changed after review"
    if candidate.get("base_digest") != current_base:
        return "candidate inputs changed after assembly"
    if require_ready and review.get("status") != "ready":
        return f"review is {review.get('status')}"
    return None


def current_ready_review(ledger: ReportLedger) -> dict[str, Any] | None:
    review = ledger.payload.get("review")
    if not isinstance(review, dict) or review.get("status") != "ready":
        return None
    return review if review_binding_error(ledger) is None else None


def final_binding_error(ledger: ReportLedger) -> str | None:
    final = ledger.payload.get("final_report")
    if not isinstance(final, dict) or final.get("status") != "current":
        return "no current final report"
    review = current_ready_review(ledger)
    if review is None:
        return review_binding_error(ledger) or "no current ready integration review"
    if final.get("review_id") != review.get("review_id"):
        return "final report is bound to a different review"
    candidate = ledger.payload.get("candidate")
    if not isinstance(candidate, dict) or final.get("candidate_id") != candidate.get("candidate_id"):
        return "final report is bound to a different candidate"
    path = Path(str(final.get("path") or ""))
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return "final report file is unreadable"
    if report_store.sha256_text(content) != str(final.get("sha256") or ""):
        return "final report content hash does not match the ledger"
    return None


def _citation_ids(text: str) -> set[str]:
    return {f"S{match.group(1)}" for match in CITATION_RE.finditer(text)}


def apply_edits(ledger: ReportLedger, edits: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    current = require_current_candidate(ledger)
    candidate_file_content(current)
    if base_inputs_digest(ledger) != current.get("base_digest"):
        raise ReportValidationError("candidate inputs changed; reassemble and review before applying edits")
    tree = _tree_document(ledger)
    sections = copy.deepcopy(list(current.get("sections") or []))
    applied: list[dict[str, Any]] = []
    edits_list = [item for item in edits if isinstance(item, Mapping)]
    if not edits_list:
        raise ReportValidationError("integrate_review returned no edits")
    for index, raw in enumerate(edits_list):
        draft_id = str(raw.get("draft_id") or "").strip()
        kind = str(raw.get("kind") or "").strip().lower()
        anchor = raw.get("anchor")
        replacement = raw.get("replacement")
        reason = str(raw.get("reason") or "").strip()
        if not draft_id:
            raise ReportValidationError(f"edits[{index}].draft_id is required")
        if kind not in EDIT_KINDS:
            raise ReportValidationError(f"edits[{index}].kind is not an allowed local edit")
        if not isinstance(anchor, str) or not anchor:
            raise ReportValidationError(f"edits[{index}].anchor must be a non-empty string")
        if not isinstance(replacement, str) or (not replacement.strip() and kind != "dedupe"):
            raise ReportValidationError(f"edits[{index}].replacement must be non-empty unless this is a dedupe deletion")
        if not reason:
            raise ReportValidationError(f"edits[{index}].reason is required")
        if HEADING_RE.search(replacement):
            raise ReportValidationError(f"edits[{index}] would change heading structure; return revise instead")
        target = next((section for section in sections if section.get("draft_id") == draft_id), None)
        if target is None:
            raise ReportValidationError(f"edits[{index}] targets a draft not visible in the current candidate")
        body = str(target.get("body") or "")
        count = body.count(anchor)
        if count != 1:
            raise ReportValidationError(f"edits[{index}] anchor must occur exactly once in {draft_id}; found {count}")
        if _citation_ids(body) != _citation_ids(body.replace(anchor, replacement, 1)):
            raise ReportValidationError(f"edits[{index}] would change citation identity; return revise instead")
        updated = body.replace(anchor, replacement, 1)
        if not updated.strip():
            raise ReportValidationError(f"edits[{index}] would empty the node body")
        target["body"] = updated
        target["body_sha256"] = report_store.sha256_text(updated)
        target["last_edit_kind"] = kind
        applied.append(
            {
                "draft_id": draft_id,
                "kind": kind,
                "anchor": anchor,
                "replacement": replacement,
                "reason": reason,
                "source_candidate_id": current.get("candidate_id"),
                "applied_at": utc_now_iso(),
            }
        )

    source_table, citation_map = _source_table(ledger, sections)
    content = _candidate_text(tree, sections, source_table)
    candidates = ledger.payload.get("candidates") if isinstance(ledger.payload.get("candidates"), list) else []
    candidate_id = report_store.next_identifier("c", [str(item.get("candidate_id")) for item in candidates if isinstance(item, Mapping)])
    path = ledger.paths.assembly_file(candidate_id)
    manifest_path = ledger.paths.assembly_manifest(candidate_id)
    sha256 = report_store.sha256_text(content)
    edits_applied = [*list(current.get("edits_applied") or []), *applied]
    record = {
        "candidate_id": candidate_id,
        "status": "pending_review",
        "tree_version": tree.get("version"),
        "base_digest": current.get("base_digest"),
        "inputs_digest": candidate_inputs_digest(str(current.get("base_digest")), sha256, edits_applied),
        "path": str(path),
        "virtual_path": ledger.paths.virtual(path),
        "manifest_path": str(manifest_path),
        "sha256": sha256,
        "chars": len(content),
        "sections": sections,
        "source_table": source_table,
        "citation_map": citation_map,
        "edits_applied": edits_applied,
        "parent_candidate_id": current.get("candidate_id"),
        "created_at": utc_now_iso(),
        "content": content,
    }
    _write_candidate_files(ledger, record, tree)
    record.pop("content", None)
    current["status"] = "superseded"
    current["superseded_reason"] = "local edits applied"
    current["superseded_at"] = utc_now_iso()
    candidates.append(record)
    ledger.payload["candidates"] = candidates
    ledger.payload["candidate"] = record
    invalidate_review(ledger, "local edits require re-review")
    ledger.save()
    return record


def review_inputs(ledger: ReportLedger) -> dict[str, Any]:
    candidate = require_current_candidate(ledger)
    tree = _tree_document(ledger)
    drafts = []
    for section in candidate.get("sections") or []:
        if not isinstance(section, Mapping) or not section.get("draft_id"):
            continue
        draft = next((item for item in ledger.payload.get("drafts") or [] if isinstance(item, dict) and item.get("draft_id") == section.get("draft_id")), None)
        drafts.append(
            {
                "draft_id": section.get("draft_id"),
                "node_id": section.get("node_id"),
                "path": (draft or {}).get("virtual_path"),
                "body": section.get("body"),
                "sha256": report_store.sha256_text(str(section.get("body") or "")),
                "applicable_fingerprint": (draft or {}).get("applicable_fingerprint"),
            }
        )
    materials = []
    for item in ledger.materials():
        materials.append(
            {
                "material_id": item.get("material_id"),
                "source_id": item.get("source_id"),
                "title": item.get("title"),
                "url": item.get("url"),
                "path": item.get("virtual_path"),
                "sha256": item.get("sha256"),
                "chars": item.get("chars"),
                "lines": item.get("lines"),
            }
        )
    return {
        "candidate": candidate,
        "tree": tree,
        "drafts": drafts,
        "materials": materials,
        "sources": candidate.get("source_table") or [],
        "citation_map": candidate.get("citation_map") or [],
        "base_digest": candidate.get("base_digest"),
        "inputs_digest": candidate.get("inputs_digest"),
        "candidate_sha256": candidate.get("sha256"),
    }
