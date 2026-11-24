"""Copy this message's declared uploads into native, versioned report materials."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from deerflow.config.paths import get_paths
from deerflow.sp.report import store as native
from deerflow.sp.report.ledger import utc_now_iso
from deerflow.sp.report.materials import MATERIAL_MAX_CHARS, _save_material_text
from deerflow.uploads.manager import upload_virtual_path

_TEXT = {".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".rst", ".xml", ".html", ".log"}
_CONVERTIBLE = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}


def _read_upload_text(uploads: Path, filename: str) -> str:
    # Open each untrusted directory through its parent's descriptor, so an
    # upload or user-data symlink cannot redirect reads into another thread.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptors = []
    try:
        descriptors.append(os.open(uploads.parent.parent, flags))
        for component in ("user-data", "uploads"):
            descriptors.append(os.open(component, flags, dir_fd=descriptors[-1]))
        descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptors[-1])
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError("upload is not a regular file")
            data = handle.read(MATERIAL_MAX_CHARS * 4 + 1)
        if len(data) > MATERIAL_MAX_CHARS * 4:
            raise ValueError("upload exceeds native material capacity")
        text = data.decode("utf-8")
        if not text.strip() or len(text) > MATERIAL_MAX_CHARS:
            raise ValueError("upload is empty or exceeds native material capacity")
        return text
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def ingest_message_uploads(ledger, message, owner):
    """Caller holds the new report lock; no models, network or arbitrary paths."""
    files = message.additional_kwargs.get("files") or []
    if not isinstance(files, list):
        return []
    uploads = get_paths().sandbox_uploads_dir(owner["thread_id"], user_id=owner["user_id"])
    results = []
    for item in files:
        if not isinstance(item, dict):
            continue
        filename = item.get("filename")
        try:
            if not isinstance(filename, str) or filename in {"", ".", ".."} or "/" in filename or "\\" in filename:
                raise ValueError("invalid upload filename")
            if item.get("path") != upload_virtual_path(filename):
                raise ValueError("upload metadata is outside the declared file")
            suffix = Path(filename).suffix.lower()
            if suffix in _TEXT:
                content_name = filename
            elif suffix in _CONVERTIBLE:
                content_name = Path(filename).with_suffix(".md").name
            else:
                raise ValueError("upload needs a text conversion")
            text = _read_upload_text(uploads, content_name)
            digest = native.sha256_text(text)
        except (OSError, ValueError, NotImplementedError):
            results.append({"filename": filename, "status": "unavailable", "hint": "No readable declared UTF-8 upload or existing Markdown conversion. Do not invent its contents."})
            continue
        source = next((s for s in ledger.payload["sources"] if s.get("source_kind") == "user_upload" and s.get("upload_path") == item["path"] and s.get("content_sha256") == digest), None)
        if source is None:
            source = {
                "source_id": ledger.next_source_id(),
                "source_kind": "user_upload",
                "title": filename,
                "upload_path": item["path"],
                "content_path": upload_virtual_path(content_name),
                "content_sha256": digest,
                "input_id": ledger.inputs()[-1]["input_id"],
                "source_message_id": message.id,
                "first_seen_at": utc_now_iso(),
            }
            ledger.payload["sources"].append(source)
        material, _ = _save_material_text(
            ledger,
            ledger.paths,
            text=text,
            source_id=source["source_id"],
            fields={
                "source_kind": "user_upload",
                "title": filename,
                "content_scope": "converted_user_upload" if content_name != filename else "user_upload",
                "upstream_truncation": "unknown" if content_name != filename else False,
                "created_at": utc_now_iso(),
            },
        )
        result = {"filename": filename, "status": "saved", "material_id": material["material_id"], "source_id": source["source_id"], "sha256": material["sha256"], "lines": material["lines"]}
        if result not in results:
            results.append(result)
    return results
