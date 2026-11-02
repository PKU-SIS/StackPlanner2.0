"""Stage a structured Outline proposal at its host-owned delivery path."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from langchain.tools import tool

from deerflow.sp.report import store
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


def build_submit_outline_tool(*, paths: store.ReportPaths, delivery_path: Path, normalize_sources: bool = False):
    if not delivery_path.resolve().is_relative_to((paths.directory / "outline").resolve()):
        raise ReportValidationError("Outline delivery must stay in the bound report directory")

    @tool("report_submit_outline", parse_docstring=True)
    def submit_outline(tree: dict[str, Any]) -> str:
        """Stage one complete tree object; the host serializes JSON and checks it.

        Prefer this over embedding JSON text in write_file.content. Include all
        root fields, original requirements and nodes. This stages a proposal;
        final acceptance still occurs through the normal report handler.
        Invalid proposals are retained for correction and return diagnostics.

        Args:
            tree: Complete tree object, not a JSON string or response envelope.
        """
        ledger = ReportLedger.load(paths)
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
        if delivery_path.read_text(encoding="utf-8") != content:
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
