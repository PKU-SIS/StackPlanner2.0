"""Outline tree parsing, structural validation, and acceptance."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from deerflow.sp.report import store as report_store
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError, utc_now_iso

NODE_KINDS = frozenset({"section", "writable"})
NODE_STATUSES = frozenset({"planned", "needs_evidence", "needs_user", "ready"})
TREE_REQUIRED_KEYS = ("report_title", "requirements", "nodes")
MAX_TREE_JSON_CHARS = 400_000


def parse_tree_payload(value: Any) -> dict[str, Any]:
    """Parse the authoritative Outline JSON carried in ``artifact_content``."""
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        raise ReportValidationError("Outline result has no artifact_content JSON")
    text = value.strip()
    if len(text) > MAX_TREE_JSON_CHARS:
        raise ReportValidationError(f"Outline tree exceeds the {MAX_TREE_JSON_CHARS}-character acceptance limit")
    candidates = [text]
    fenced = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced is not None:
        candidates.append(fenced.group(1).strip())
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict):
            return payload
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            payload, _ = decoder.raw_decode(text[match.start() :])
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict):
            return payload
    raise ReportValidationError("Outline artifact_content is not a JSON object")


def _as_list(value: Any, field_name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ReportValidationError(f"{field_name} must be a list")
    return value


def _as_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReportValidationError(f"{field_name} must be a non-empty string")
    return value.strip()


def _validate_lines(value: Any, total_lines: int, field_name: str) -> list[int]:
    if not isinstance(value, list) or len(value) != 2:
        raise ReportValidationError(f"{field_name} must be [start, end]")
    start, end = value
    if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
        raise ReportValidationError(f"{field_name} must contain integers")
    if start < 1 or end < start or (total_lines and end > total_lines):
        raise ReportValidationError(f"{field_name} is outside the material line range 1..{total_lines}")
    return [start, end]


def _previous_nodes(ledger: ReportLedger) -> dict[str, dict[str, Any]]:
    previous = ledger.payload.get("nodes")
    return {str(key): dict(value) for key, value in previous.items() if isinstance(value, Mapping)} if isinstance(previous, Mapping) else {}


def validate_tree(payload: Mapping[str, Any], ledger: ReportLedger) -> dict[str, Any]:
    """Validate one Outline result and return its normalized tree object."""
    tree = dict(payload)
    for key in TREE_REQUIRED_KEYS:
        if key not in tree:
            raise ReportValidationError(f"tree.{key} is required")
    report_title = _as_text(tree.get("report_title"), "tree.report_title")
    requirements_raw = _as_list(tree.get("requirements"), "tree.requirements")
    if not requirements_raw:
        raise ReportValidationError("tree.requirements must contain at least one original user requirement")
    requirement_ids: set[str] = set()
    requirements: list[dict[str, Any]] = []
    for index, raw in enumerate(requirements_raw):
        if not isinstance(raw, Mapping):
            raise ReportValidationError(f"tree.requirements[{index}] must be an object")
        requirement_id = _as_text(raw.get("id"), f"tree.requirements[{index}].id")
        if requirement_id in requirement_ids:
            raise ReportValidationError(f"duplicate requirement id: {requirement_id}")
        requirement_ids.add(requirement_id)
        input_id = _as_text(raw.get("input_id"), f"tree.requirements[{index}].input_id")
        if ledger.input(input_id) is None:
            raise ReportValidationError(f"tree.requirements[{index}] references unknown input: {input_id}")
        requirements.append(
            {
                "id": requirement_id,
                "input_id": input_id,
                "text": _as_text(raw.get("text"), f"tree.requirements[{index}].text"),
                "hard": bool(raw.get("hard", True)),
            }
        )

    nodes_raw = _as_list(tree.get("nodes"), "tree.nodes")
    if not nodes_raw:
        raise ReportValidationError("tree.nodes must contain at least one node")
    previous = _previous_nodes(ledger)
    nodes: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    for index, raw in enumerate(nodes_raw):
        if not isinstance(raw, Mapping):
            raise ReportValidationError(f"tree.nodes[{index}] must be an object")
        node_id = _as_text(raw.get("node_id"), f"tree.nodes[{index}].node_id")
        if node_id in node_ids:
            raise ReportValidationError(f"duplicate node_id: {node_id}")
        node_ids.add(node_id)
        kind = str(raw.get("kind") or "").strip().lower()
        if kind not in NODE_KINDS:
            raise ReportValidationError(f"tree.nodes[{node_id}].kind must be section or writable")
        parent_id = raw.get("parent_id")
        if parent_id is not None and (not isinstance(parent_id, str) or not parent_id.strip()):
            raise ReportValidationError(f"tree.nodes[{node_id}].parent_id must be null or a node id")
        node: dict[str, Any] = {
            "node_id": node_id,
            "parent_id": parent_id.strip() if isinstance(parent_id, str) else None,
            "kind": kind,
            "title": _as_text(raw.get("title"), f"tree.nodes[{node_id}].title"),
        }
        relations = _as_list(raw.get("relations"), f"tree.nodes[{node_id}].relations")
        normalized_relations: list[dict[str, str]] = []
        for relation in relations:
            if not isinstance(relation, Mapping):
                raise ReportValidationError(f"tree.nodes[{node_id}].relations items must be objects")
            normalized_relations.append(
                {
                    "node_id": _as_text(relation.get("node_id"), f"tree.nodes[{node_id}].relations[].node_id"),
                    "type": _as_text(relation.get("type"), f"tree.nodes[{node_id}].relations[].type"),
                }
            )
        gap_ids = _as_list(raw.get("gap_ids"), f"tree.nodes[{node_id}].gap_ids")
        normalized_gap_ids: list[str] = []
        for gap_id in gap_ids:
            gap = ledger.require_gap(_as_text(gap_id, f"tree.nodes[{node_id}].gap_ids[]"))
            normalized_gap_ids.append(str(gap["gap_id"]))
        node["gap_ids"] = normalized_gap_ids

        if kind == "writable":
            node["question"] = _as_text(raw.get("question"), f"tree.nodes[{node_id}].question")
            arguments = _as_list(raw.get("argument"), f"tree.nodes[{node_id}].argument")
            if not arguments:
                raise ReportValidationError(f"tree.nodes[{node_id}].argument must contain at least one step")
            node["argument"] = [_as_text(item, f"tree.nodes[{node_id}].argument[]") for item in arguments]
            node["conclusion"] = _as_text(raw.get("conclusion"), f"tree.nodes[{node_id}].conclusion")
            constraints = raw.get("constraints") or {}
            if not isinstance(constraints, Mapping):
                raise ReportValidationError(f"tree.nodes[{node_id}].constraints must be an object")
            node["constraints"] = {str(key): value for key, value in constraints.items()}
            status = str(raw.get("status") or "").strip().lower()
            if status not in NODE_STATUSES:
                raise ReportValidationError(f"tree.nodes[{node_id}].status must be one of {sorted(NODE_STATUSES)}")
            node["status"] = status
            node["requirement_ids"] = _validate_requirement_ids(raw.get("requirement_ids"), requirement_ids, node_id)
            node["evidence"] = _validate_evidence(raw.get("evidence"), ledger, node_id)
            node["relations"] = normalized_relations
        else:
            node["relations"] = normalized_relations
            if raw.get("status") is not None:
                node["status"] = str(raw.get("status")).strip().lower()
        nodes.append(node)

    for node in nodes:
        parent_id = node["parent_id"]
        if parent_id is not None and parent_id not in node_ids:
            raise ReportValidationError(f"tree.nodes[{node['node_id']}] references unknown parent: {parent_id}")
        for relation in node.get("relations") or []:
            if relation["node_id"] not in node_ids:
                raise ReportValidationError(f"tree.nodes[{node['node_id']}] relation references unknown node: {relation['node_id']}")
    _validate_acyclic(nodes)

    removed_raw = _as_list(tree.get("removed_node_ids"), "tree.removed_node_ids")
    removed: list[str] = []
    for node_id in removed_raw:
        value = _as_text(node_id, "tree.removed_node_ids[]")
        if value not in previous:
            raise ReportValidationError(f"removed_node_ids references a node absent from the previous tree: {value}")
        if value in node_ids:
            raise ReportValidationError(f"node {value} cannot be both present and removed")
        removed.append(value)
    missing_removals = sorted(set(previous) - node_ids - set(removed))
    if missing_removals:
        raise ReportValidationError(f"previous nodes were dropped without removed_node_ids: {', '.join(missing_removals)}")

    write_requests = _validate_write_requests(tree.get("write_requests"), {node["node_id"]: node for node in nodes})
    normalized = {
        **tree,
        "report_title": report_title,
        "requirements": requirements,
        "nodes": nodes,
        "removed_node_ids": removed,
        "write_requests": write_requests,
        "gap_outcomes": _as_list(tree.get("gap_outcomes"), "tree.gap_outcomes"),
        "user_decisions": _as_list(tree.get("user_decisions"), "tree.user_decisions"),
        "feedback_dispositions": _as_list(tree.get("feedback_dispositions"), "tree.feedback_dispositions"),
        "draft_dispositions": _as_list(tree.get("draft_dispositions"), "tree.draft_dispositions"),
    }
    return normalized


def _validate_requirement_ids(value: Any, known: set[str], node_id: str) -> list[str]:
    items = _as_list(value, f"tree.nodes[{node_id}].requirement_ids")
    if not items:
        raise ReportValidationError(f"tree.nodes[{node_id}].requirement_ids must not be empty")
    result: list[str] = []
    for item in items:
        requirement_id = _as_text(item, f"tree.nodes[{node_id}].requirement_ids[]")
        if requirement_id not in known:
            raise ReportValidationError(f"tree.nodes[{node_id}] references unknown requirement: {requirement_id}")
        result.append(requirement_id)
    return list(dict.fromkeys(result))


def _validate_evidence(value: Any, ledger: ReportLedger, node_id: str) -> list[dict[str, Any]]:
    items = _as_list(value, f"tree.nodes[{node_id}].evidence")
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(items):
        if not isinstance(raw, Mapping):
            raise ReportValidationError(f"tree.nodes[{node_id}].evidence[{index}] must be an object")
        material_id = _as_text(raw.get("material_id"), f"tree.nodes[{node_id}].evidence[{index}].material_id")
        material = ledger.material(material_id)
        if material is None:
            raise ReportValidationError(f"tree.nodes[{node_id}] references unknown material: {material_id}")
        source_ids = [_as_text(item, f"tree.nodes[{node_id}].evidence[{index}].source_ids[]") for item in _as_list(raw.get("source_ids"), f"tree.nodes[{node_id}].evidence[{index}].source_ids")]
        known_source = str(material.get("source_id") or "")
        if source_ids and known_source and known_source not in source_ids:
            raise ReportValidationError(f"tree.nodes[{node_id}] evidence source_ids do not include {known_source}")
        locator = raw.get("locator") or {}
        if not isinstance(locator, Mapping):
            raise ReportValidationError(f"tree.nodes[{node_id}].evidence[{index}].locator must be an object")
        normalized_locator: dict[str, Any] = {}
        if locator.get("lines") is not None:
            normalized_locator["lines"] = _validate_lines(
                locator.get("lines"),
                int(material.get("lines") or 0),
                f"tree.nodes[{node_id}].evidence[{index}].locator.lines",
            )
        result.append(
            {
                "material_id": material_id,
                "source_ids": source_ids or ([known_source] if known_source else []),
                "locator": normalized_locator,
                "use": _as_text(raw.get("use"), f"tree.nodes[{node_id}].evidence[{index}].use"),
            }
        )
    return result


def _validate_acyclic(nodes: list[dict[str, Any]]) -> None:
    parents = {node["node_id"]: node["parent_id"] for node in nodes}
    for node_id in parents:
        seen: set[str] = set()
        current: str | None = node_id
        while current is not None:
            if current in seen:
                raise ReportValidationError(f"tree contains a parent cycle at node {current}")
            seen.add(current)
            current = parents.get(current)


def _validate_write_requests(value: Any, nodes: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    items = _as_list(value, "tree.write_requests")
    keys: set[str] = set()
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(items):
        if not isinstance(raw, Mapping):
            raise ReportValidationError(f"tree.write_requests[{index}] must be an object")
        key = _as_text(raw.get("key"), f"tree.write_requests[{index}].key")
        if key in keys:
            raise ReportValidationError(f"duplicate write request key: {key}")
        keys.add(key)
        node_ids = _as_list(raw.get("node_ids"), f"tree.write_requests[{index}].node_ids")
        if not node_ids:
            raise ReportValidationError(f"tree.write_requests[{key}] requires node_ids")
        normalized_node_ids: list[str] = []
        for node_id in node_ids:
            value_id = _as_text(node_id, f"tree.write_requests[{key}].node_ids[]")
            node = nodes.get(value_id)
            if node is None:
                raise ReportValidationError(f"tree.write_requests[{key}] references unknown node: {value_id}")
            if node.get("kind") != "writable":
                raise ReportValidationError(f"tree.write_requests[{key}] references a non-writable node: {value_id}")
            normalized_node_ids.append(value_id)
        result.append(
            {
                "key": key,
                "node_ids": normalized_node_ids,
                "feedback_wanted": bool(raw.get("feedback_wanted", False)),
            }
        )
    return result


def _requirements_map(tree: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(item.get("id")): item for item in tree.get("requirements") or [] if isinstance(item, Mapping)}


def node_spec_payload(node: Mapping[str, Any], requirements: Mapping[str, Mapping[str, Any]], nodes: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Return the content-bearing node basis used for draft applicability."""
    requirement_ids = list(node.get("requirement_ids") or [])
    requirement_context = [
        {
            "id": str(requirement_id),
            "text": str((requirements.get(str(requirement_id)) or {}).get("text") or ""),
            "hard": bool((requirements.get(str(requirement_id)) or {}).get("hard", True)),
            "input_id": str((requirements.get(str(requirement_id)) or {}).get("input_id") or ""),
        }
        for requirement_id in requirement_ids
    ]
    parent = nodes.get(str(node.get("parent_id"))) if node.get("parent_id") else None
    relation_context = []
    for relation in node.get("relations") or []:
        if not isinstance(relation, Mapping):
            continue
        target = nodes.get(str(relation.get("node_id")))
        relation_context.append(
            {
                "node_id": str(relation.get("node_id") or ""),
                "type": str(relation.get("type") or ""),
                "title": str((target or {}).get("title") or ""),
                "question": str((target or {}).get("question") or ""),
            }
        )
    return {
        "title": str(node.get("title") or ""),
        "question": str(node.get("question") or ""),
        "argument": list(node.get("argument") or []),
        "conclusion": str(node.get("conclusion") or ""),
        "evidence": list(node.get("evidence") or []),
        "constraints": dict(node.get("constraints") or {}),
        "requirement_ids": requirement_ids,
        "requirement_context": requirement_context,
        "parent": {
            "node_id": str(node.get("parent_id") or ""),
            "title": str((parent or {}).get("title") or ""),
            "kind": str((parent or {}).get("kind") or ""),
        },
        "relation_context": relation_context,
    }


def node_fingerprints(tree: Mapping[str, Any]) -> dict[str, str]:
    requirements = _requirements_map(tree)
    nodes = {str(node.get("node_id")): node for node in tree.get("nodes") or [] if isinstance(node, Mapping)}
    result: dict[str, str] = {}
    for node_id, node in nodes.items():
        payload = node_spec_payload(node, requirements, nodes)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        result[node_id] = report_store.sha256_text(encoded)
    return result


def tree_change_summary(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> dict[str, Any]:
    previous = previous or {}
    old_requirements = _requirements_map(previous)
    new_requirements = _requirements_map(current)
    old_nodes = {str(node.get("node_id")): node for node in previous.get("nodes") or [] if isinstance(node, Mapping)}
    new_nodes = {str(node.get("node_id")): node for node in current.get("nodes") or [] if isinstance(node, Mapping)}
    requirement_changes = {
        "added": sorted(set(new_requirements) - set(old_requirements)),
        "removed": sorted(set(old_requirements) - set(new_requirements)),
        "changed": sorted(
            key
            for key in set(old_requirements) & set(new_requirements)
            if {
                "text": str(old_requirements[key].get("text") or ""),
                "hard": bool(old_requirements[key].get("hard", True)),
                "input_id": str(old_requirements[key].get("input_id") or ""),
            }
            != {
                "text": str(new_requirements[key].get("text") or ""),
                "hard": bool(new_requirements[key].get("hard", True)),
                "input_id": str(new_requirements[key].get("input_id") or ""),
            }
        ),
    }
    node_changes = {
        "added": sorted(set(new_nodes) - set(old_nodes)),
        "removed": sorted(set(old_nodes) - set(new_nodes)),
        "title_changed": sorted(key for key in set(old_nodes) & set(new_nodes) if str(old_nodes[key].get("title") or "") != str(new_nodes[key].get("title") or "")),
        "moved": sorted(key for key in set(old_nodes) & set(new_nodes) if old_nodes[key].get("parent_id") != new_nodes[key].get("parent_id")),
        "relations_changed": sorted(key for key in set(old_nodes) & set(new_nodes) if list(old_nodes[key].get("relations") or []) != list(new_nodes[key].get("relations") or [])),
    }
    return {"from_version": previous.get("version"), "to_version": current.get("version"), "requirements": requirement_changes, "nodes": node_changes}


def write_tree_candidate(
    tree: Mapping[str, Any],
    ledger: ReportLedger,
) -> tuple[dict[str, Any], int, Path]:
    """Write the immutable candidate tree file without switching the ledger."""
    version = ledger.next_tree_version()
    candidate = dict(tree)
    candidate["version"] = version
    candidate["accepted_at"] = utc_now_iso()
    path = report_store.write_tree_version(ledger.paths, version, candidate)
    return candidate, version, path


def commit_tree(
    candidate: Mapping[str, Any],
    *,
    version: int,
    path: Path,
    ledger: ReportLedger,
) -> None:
    """Switch the authoritative tree after the Artifact view has been registered."""
    content = path.read_text(encoding="utf-8")
    previous_tree: Mapping[str, Any] | None = None
    previous_ref = ledger.payload.get("tree")
    if isinstance(previous_ref, Mapping) and previous_ref.get("path"):
        try:
            previous_tree = json.loads(Path(str(previous_ref["path"])).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous_tree = None
    ledger.payload["tree"] = {
        "version": version,
        "path": str(path),
        "virtual_path": ledger.paths.virtual(path),
        "sha256": report_store.sha256_text(content),
        "node_ids": [node["node_id"] for node in candidate["nodes"]],
        "accepted_at": candidate["accepted_at"],
        "last_changes": tree_change_summary(previous_tree, candidate),
    }
    ledger.payload["nodes"] = {
        node["node_id"]: {
            "node_id": node["node_id"],
            "parent_id": node.get("parent_id"),
            "kind": node.get("kind"),
            "title": node.get("title"),
            "status": node.get("status"),
        }
        for node in candidate["nodes"]
    }
    ledger.attach_nodes_to_gaps(candidate["nodes"])
    ledger.save()


def accept_tree(
    payload: Mapping[str, Any],
    ledger: ReportLedger,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate, persist, and switch the current tree version.

    Returns ``(tree, gap_outcomes, user_decisions)`` after all validations pass.
    The production path uses :func:`write_tree_candidate` + Artifact
    registration + :func:`commit_tree` so the ledger only switches after the
    Artifact view succeeds.
    """
    tree = validate_tree(payload, ledger)
    user_decisions = ledger.apply_user_decisions(tree.get("user_decisions") or [])
    gap_outcomes = ledger.apply_gap_outcomes(tree.get("gap_outcomes") or [])
    candidate, version, path = write_tree_candidate(tree, ledger)
    commit_tree(candidate, version=version, path=path, ledger=ledger)
    return candidate, gap_outcomes, user_decisions


def tree_summary_for_prompt(tree: Mapping[str, Any] | None) -> str:
    if not isinstance(tree, Mapping):
        return "No outline tree has been accepted yet."
    nodes = tree.get("nodes") if isinstance(tree.get("nodes"), list) else []
    lines = [f"Accepted tree version {tree.get('version')}: {tree.get('report_title')}", "Nodes:"]
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        lines.append(f"- {node.get('node_id')} [{node.get('kind')}/{node.get('status')}] {node.get('title')} (parent={node.get('parent_id')})")
    return "\n".join(lines)


def tree_path_from_ledger(ledger: ReportLedger) -> Path | None:
    tree = ledger.payload.get("tree")
    if not isinstance(tree, Mapping) or not tree.get("path"):
        return None
    return Path(str(tree["path"]))
