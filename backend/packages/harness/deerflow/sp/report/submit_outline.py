"""Stage a structured Outline proposal at its host-owned delivery path."""

import json
from pathlib import Path
from typing import Any

from langchain.tools import tool

from deerflow.sp.report import store
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError
from deerflow.sp.report.tree import validate_tree_with_diagnostics


def build_submit_outline_tool(*, paths: store.ReportPaths, delivery_path: Path):
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
        content = json.dumps(tree, ensure_ascii=False, indent=2) + "\n"
        store.write_text_atomic(delivery_path, content)
        if delivery_path.read_text(encoding="utf-8") != content:
            raise store.ReportStoreError("Outline delivery read-back differs from the submitted tree")
        try:
            validate_tree_with_diagnostics(tree, ledger)
        except ReportValidationError as exc:
            return json.dumps({"saved": True, "validation_ok": False, "error": "invalid_outline_tree", "detail": str(exc), "tree_path": paths.virtual(delivery_path)}, ensure_ascii=False)
        return json.dumps({"saved": True, "validation_ok": True, "tree_path": paths.virtual(delivery_path), "next": "Return the compact file-delivery envelope; the normal host gate still decides acceptance."}, ensure_ascii=False)

    return submit_outline
