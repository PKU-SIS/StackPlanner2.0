"""Stage a structured Outline proposal at its host-owned delivery path."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from langchain.tools import tool
from pydantic import StrictInt

from deerflow.sp.report import store
from deerflow.sp.report.draft_choices import serialize_draft_choices
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError
from deerflow.sp.report.tree import validate_tree_with_diagnostics


def normalize_evidence_sources(tree: dict[str, Any], ledger: ReportLedger):
    """One deterministic pass: material identity owns its canonical source ID."""
    corrected = deepcopy(tree)
    changes = []
    known = {row.get("source_id") for row in ledger.payload.get("sources", [])}
    for node in corrected.get("nodes", []) if isinstance(corrected.get("nodes"), list) else []:
        if not isinstance(node, dict) or not isinstance(node.get("evidence"), list):
            continue
        for index, evidence in enumerate(node["evidence"]):
            if not isinstance(evidence, dict) or not isinstance(evidence.get("material_id"), str):
                continue
            material = ledger.material(evidence["material_id"])
            canonical = material.get("source_id") if material else None
            supplied = evidence.get("source_ids")
            if canonical in known and canonical and isinstance(supplied, list) and supplied and all(isinstance(x, str) for x in supplied) and canonical not in supplied:
                changes.append({"node_id": node.get("node_id"), "evidence_index": index, "material_id": evidence["material_id"], "before": supplied, "after": [canonical]})
                evidence["source_ids"] = [canonical]
    return corrected, changes


def _apply_node_choices(tree, ledger, keep_nodes, rewrite_nodes, drop_nodes, decision_reason):
    if any(v is not None for v in (keep_nodes, rewrite_nodes, drop_nodes)):
        return serialize_draft_choices(tree, ledger, keep_nodes=keep_nodes, rewrite_nodes=rewrite_nodes, drop_nodes=drop_nodes, reason=decision_reason)
    if (ledger.payload.get("length_contract") or {}).get("compact_outline") and any(d.get("is_current") for d in ledger.payload.get("drafts") or []):
        raise ReportValidationError("Compact revision requires explicit keep_nodes/rewrite_nodes/drop_nodes and decision_reason; the host generates draft_dispositions")
    return tree


def build_submit_outline_tool(*, paths: store.ReportPaths, delivery_path: Path, normalize_sources: bool = False):
    if not delivery_path.resolve().is_relative_to((paths.directory / "outline").resolve()):
        raise ReportValidationError("Outline delivery must stay in the bound report directory")

    @tool("report_submit_outline", parse_docstring=True)
    def submit_outline(tree: dict[str, Any], keep_nodes: list[str] | None = None, rewrite_nodes: list[str] | None = None, drop_nodes: list[str] | None = None, decision_reason: str | None = None) -> str:
        """Stage one complete tree object; the host serializes JSON and checks it.

        Prefer this over embedding JSON text in write_file.content. Include all
        root fields, original requirements and nodes. This stages a proposal;
        final acceptance still occurs through the normal report handler.
        Invalid proposals are retained for correction and return diagnostics.

        Args:
            tree: Complete tree object, not a JSON string or response envelope.
            keep_nodes: Explicit current-draft node IDs to preserve.
            rewrite_nodes: Explicit current-draft node IDs to rewrite.
            drop_nodes: Explicit removed current-draft node IDs to drop.
            decision_reason: Business reason for the node choices. With choices,
                leave tree.draft_dispositions empty; host generates its fields.
        """
        ledger = ReportLedger.load(paths)
        try:
            tree = _apply_node_choices(tree, ledger, keep_nodes, rewrite_nodes, drop_nodes, decision_reason)
        except ReportValidationError as exc:
            return json.dumps({"saved": False, "validation_ok": False, "error": "invalid_node_choices", "detail": str(exc)})
        corrections = []
        if normalize_sources:
            original = tree
            tree, corrections = normalize_evidence_sources(tree, ledger)
            if corrections:
                original_path = delivery_path.with_suffix(".source-ids-original.json")
                if not original_path.exists():
                    store.write_text_atomic(original_path, json.dumps(original, ensure_ascii=False, indent=2) + "\n")
        content = json.dumps(tree, ensure_ascii=False, indent=2) + "\n"
        store.write_text_atomic(delivery_path, content)
        if store.read_text(delivery_path) != content:
            raise store.ReportStoreError("Outline delivery read-back differs from the submitted tree")
        try:
            validate_tree_with_diagnostics(tree, ledger)
        except ReportValidationError as exc:
            return json.dumps({"saved": True, "validation_ok": False, "error": "invalid_outline_tree", "detail": str(exc), "tree_path": paths.virtual(delivery_path), "source_id_corrections": corrections}, ensure_ascii=False)
        return json.dumps(
            {"saved": True, "validation_ok": True, "source_id_corrections": corrections, "tree_path": paths.virtual(delivery_path), "next": "Return the compact file-delivery envelope; the normal host gate still decides acceptance."},
            ensure_ascii=False,
        )

    return submit_outline


def build_patch_outline_budget_tool(*, paths: store.ReportPaths, delivery_path: Path):
    """Stage a length-only proposal; the ordinary acceptance handler commits it."""
    if not delivery_path.resolve().is_relative_to((paths.directory / "outline").resolve()):
        raise ReportValidationError("Outline budget delivery must stay in the bound outline directory")

    @tool("report_patch_outline_budget", parse_docstring=True)
    def patch_outline_budget(
        base_tree_version: StrictInt,
        base_tree_sha256: str,
        node_budgets: list[dict[str, Any]],
        draft_dispositions: list[dict[str, Any]] | None = None,
        write_requests: list[dict[str, Any]] | None = None,
        user_decisions: list[dict[str, Any]] | None = None,
        feedback_dispositions: list[dict[str, Any]] | None = None,
        keep_nodes: list[str] | None = None,
        rewrite_nodes: list[str] | None = None,
        drop_nodes: list[str] | None = None,
        decision_reason: str | None = None,
    ) -> str:
        """Stage a complete tree from a small, version-bound word-budget proposal.

        Only target_words and max_words on existing writable nodes may change.
        Requirements, evidence, headings and source identities are copied from
        the hash-verified accepted tree. This tool never commits the proposal.
        After success return the normal file-delivery envelope. Existing
        draft dispositions and write requests are still required for rewrites;
        final acceptance performs all ordinary business validation.

        Args:
            base_tree_version: Exact accepted tree version from the packet.
            base_tree_sha256: Exact accepted tree SHA256 from the packet.
            node_budgets: Nonempty objects with node_id, target_words, max_words only.
            draft_dispositions: Existing keep/rewrite dispositions with node identity.
            write_requests: Existing single-node requests for affected nodes.
            user_decisions: Explicitly authorized existing input dispositions only.
            feedback_dispositions: Existing feedback dispositions for affected nodes.
            keep_nodes: Explicit current-draft nodes to preserve.
            rewrite_nodes: Explicit current-draft nodes to rewrite.
            drop_nodes: Explicit removed nodes; budget-only patches cannot remove nodes.
            decision_reason: Reason for choices; leave draft_dispositions empty.
        """
        from deerflow.sp.report.length import node_budget

        with store.report_lock(paths):
            ledger = ReportLedger.load(paths)
            base = ledger.payload.get("tree") or {}
            if not ledger.payload.get("length_contract"):
                return json.dumps({"saved": False, "error": "length_control_disabled"})
            if type(base_tree_version) is not int or base_tree_version != base.get("version") or base_tree_sha256 != base.get("sha256"):
                return json.dumps({"saved": False, "error": "stale_budget_patch"})
            try:
                content = store.read_text(Path(base["path"]))
                if store.sha256_text(content) != base_tree_sha256:
                    raise ReportValidationError("accepted tree hash mismatch")
                tree = json.loads(content)
                nodes = {n["node_id"]: n for n in tree["nodes"]}
                if not node_budgets:
                    raise ReportValidationError("node_budgets must be nonempty")
                seen = set()
                for item in node_budgets:
                    if not isinstance(item, dict) or set(item) != {"node_id", "target_words", "max_words"}:
                        raise ReportValidationError("budget patch permits node_id, target_words and max_words only")
                    node = nodes.get(item["node_id"])
                    if node is None or node.get("kind") != "writable" or item["node_id"] in seen:
                        raise ReportValidationError("budget patch must name unique existing writable nodes")
                    node_budget({"node_id": item["node_id"], "constraints": item})
                    node["constraints"].update(target_words=item["target_words"], max_words=item["max_words"])
                    seen.add(item["node_id"])
                tree.update(
                    version=base_tree_version + 1,
                    gap_outcomes=[],
                    removed_node_ids=[],
                    draft_dispositions=draft_dispositions or [],
                    write_requests=write_requests or [],
                    user_decisions=user_decisions or [],
                    feedback_dispositions=feedback_dispositions or [],
                    budget_patch_base={"version": base_tree_version, "sha256": base_tree_sha256},
                )
                tree.pop("accepted_at", None)
                tree = _apply_node_choices(tree, ledger, keep_nodes, rewrite_nodes, drop_nodes, decision_reason)
                store.write_text_atomic(delivery_path, json.dumps(tree, ensure_ascii=False, indent=2) + "\n")
                validate_tree_with_diagnostics(tree, ledger)
            except (ReportValidationError, OSError, ValueError, KeyError, TypeError) as exc:
                return json.dumps({"saved": delivery_path.exists(), "validation_ok": False, "error": "invalid_budget_patch", "detail": str(exc)}, ensure_ascii=False)
            return json.dumps({"saved": True, "validation_ok": True, "tree_path": paths.virtual(delivery_path), "next": "Return the normal file-delivery envelope. Normal host acceptance and draft applicability checks still apply."})

    return patch_outline_budget
