"""Read-only report estimates and hash-bound, operator-supplied Word calibration.

This command never runs a model or claims to execute Microsoft Word. Actual
Word records can be collected with scripts/measure_report_words.ps1 on Windows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

from deerflow.sp.report.length import COUNTER_VERSION, estimate_text, estimate_words


def _docx_text(path: Path) -> str:
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    parts = []
    with ZipFile(path) as package:
        for name in ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml"):
            if name not in package.namelist():
                continue
            root = ET.fromstring(package.read(name))
            for paragraph in root.iter(ns + "p"):
                parts.append("".join(node.text or "" if node.tag == ns + "t" else "\n" if node.tag in {ns + "br", ns + "tab"} else "" for node in paragraph.iter()))
    return "\n".join(parts)


def _records(path: Path | None) -> dict[str, dict]:
    if path is None:
        return {}
    records = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(records, list):
        raise ValueError("Word records must be a JSON array")
    indexed = {}
    for record in records:
        if not isinstance(record, dict) or record.get("measurement_kind") != "microsoft_word_compute_statistics":
            raise ValueError("Expected operator Word ComputeStatistics records")
        if type(record.get("measured_word_count")) is not int or record["measured_word_count"] < 0:
            raise ValueError("measured_word_count must be a nonnegative integer")
        digest = record.get("file_sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Word record requires a lowercase SHA256 hash")
        if not isinstance(record.get("word_version"), str) or not record["word_version"]:
            raise ValueError("Word record requires word_version provenance")
        if digest in indexed:
            raise ValueError("Duplicate Word record document hash")
        indexed[digest] = record
    return indexed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", type=Path, nargs="+")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--word-measurements", type=Path, help="Operator Word records matched by exact document SHA256; not model-supplied counts")
    args = parser.parse_args()
    try:
        protected = [p.resolve() for p in args.paths]
        if args.word_measurements:
            protected.append(args.word_measurements.resolve())
        if args.output and args.output.resolve() in protected:
            raise ValueError("output must differ from all source/measurement files")
        records = _records(args.word_measurements)
        rows = []
        for path in args.paths:
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if path.suffix.lower() == ".docx":
                estimated = estimate_text(_docx_text(path))
            elif path.suffix.lower() in {".md", ".markdown"}:
                estimated = estimate_words(data.decode("utf-8"))
            elif path.suffix.lower() == ".txt":
                estimated = estimate_text(data.decode("utf-8"))
            else:
                raise ValueError(f"Unsupported input type: {path.suffix}")
            actual = records.get(digest)
            if args.word_measurements and actual is None:
                raise ValueError(f"No Word record matches the exact document hash: {path.name}")
            row = {"path": str(path.resolve()), "file_sha256": digest, "estimated_words": estimated, "counter_version": COUNTER_VERSION, "measured_word_count": None, "measurement_source": "local_estimate_only"}
            if actual is not None:
                row.update(measured_word_count=actual["measured_word_count"], measurement_source="operator_supplied_word_record", word_version=actual["word_version"], estimate_error=estimated - actual["measured_word_count"])
            rows.append(row)
        result = {"scope": "whole_document", "local_word_execution": False, "note": "Estimates are not exact Word statistics; imported records are operator-supplied and hash-bound. No automatic counter recalibration.", "reports": rows}
        text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(text, encoding="utf-8")
        else:
            print(text, end="")
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
