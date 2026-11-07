"""Opt-in transport conversion: preserve original evidence and labels, change access only."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from .pipeline import atomic_json, digest, document_mode, encoded, read_jsonl, render


def export_file_bundle(source: Path, output: Path, config: dict) -> dict:
    from .releases import copy_bundle, validate_ready_bundle

    source, output = Path(source).resolve(), Path(output).resolve()
    if output == source or output.is_relative_to(source):
        raise ValueError("file output must be outside the source bundle")
    if output.exists():
        raise FileExistsError(f"file bundle already exists: {output}")
    if document_mode(config) != "files":
        raise ValueError("file export requires document_mode: files")
    manifest, _ = validate_ready_bundle(source)
    previous = {row["id"]: row for row in read_jsonl(source / "public/tasks.jsonl")}
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".dcc-files-", dir=output.parent))
    try:
        copy_bundle(source, staging)
        # Only the fresh copy loses its index; the source RAGFlow ledger stays intact.
        (staging / "index.json").unlink(missing_ok=True)
        derived = {
            **manifest,
            "document_mode": "files",
            "file_search_source": {
                "manifest_sha256": digest((source / "manifest.json").read_bytes()),
                "tasks_sha256": digest((source / "public/tasks.jsonl").read_bytes()),
                "document_mode": document_mode(manifest),
                "configuration_sha256": digest(encoded(config)),
            },
        }
        atomic_json(staging / "manifest.json", derived)
        tasks = render(staging, config)
        for task in tasks:
            if previous[task["id"]].get("group_id"):
                task["group_id"] = previous[task["id"]]["group_id"]
        (staging / "public/tasks.jsonl").write_bytes(b"".join(encoded(row) for row in tasks))
        validate_ready_bundle(staging)
        if output.exists():
            raise FileExistsError(f"file bundle already exists: {output}")
        os.rename(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return derived
