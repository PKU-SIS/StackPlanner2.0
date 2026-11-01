"""Report-scoped evidence reads by ledger identity, without model-copied hashes."""

import hashlib
import json
from pathlib import Path
from typing import Annotated

from langchain.tools import tool
from pydantic import Field

from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.report.store import ReportPaths


def build_read_material_tool(*, paths: ReportPaths):
    @tool("report_read_material", parse_docstring=True)
    def read_material(material_id: Annotated[str, Field(pattern=r"^m[1-9][0-9]*$")], start_line: Annotated[int, Field(ge=1)] = 1, end_line: Annotated[int | None, Field(ge=1)] = None) -> str:
        """Read saved evidence by material ID, returning source ID and numbered lines.

        Prefer this tool over copying hashed file paths into read_file. Read at
        most 200 lines / 10,000 content characters per call; use next_start_line
        for a further range. A truncated final line is explicitly marked.

        Args:
            material_id: Exact saved material ID, for example m47, not S47.
            start_line: First original line, starting at 1.
            end_line: Last requested line, inclusive; defaults to 120 lines.
        """

        def error(code):
            return json.dumps({"error": code, "material_id": material_id})

        ledger = ReportLedger.load(paths)
        material = ledger.material(material_id)
        if material is None:
            return error("unknown_material_id")
        path = Path(str(material.get("path") or "")).resolve()
        if not path.is_relative_to((paths.directory / "materials").resolve()):
            return error("invalid_material_path")
        try:
            raw = path.read_bytes()
        except OSError:
            return error("material_file_unavailable")
        if hashlib.sha256(raw).hexdigest() != material.get("sha256"):
            return error("material_hash_mismatch")
        lines = raw.decode("utf-8").splitlines()
        total = len(lines)
        requested_end = end_line if end_line is not None else start_line + 119
        if start_line > total or requested_end < start_line:
            return json.dumps({"error": "invalid_line_range", "material_id": material_id, "total_lines": total})
        stop = min(requested_end, total, start_line + 199)
        selected = []
        chars = 0
        last = start_line - 1
        partial = False
        for number in range(start_line, stop + 1):
            line = f"{number}: {lines[number - 1]}"
            if chars + len(line) > 10_000 and selected:
                break
            if len(line) > 10_000:
                line = line[:10_000]
                partial = True
            selected.append(line)
            chars += len(line) + 1
            last = number
        return json.dumps(
            {
                "material_id": material_id,
                "source_id": material["source_id"],
                "url": material.get("url"),
                "path": material.get("virtual_path"),
                "lines": [start_line, last],
                "total_lines": total,
                "content": "\n".join(selected),
                "content_truncated": last < total or partial,
                "last_line_truncated": partial,
                "next_start_line": last + 1 if last < total else None,
            },
            ensure_ascii=False,
        )

    return read_material
