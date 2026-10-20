"""Researcher material capture built on the runtime's configured fetch tool.

The wrapper never performs HTTP itself.  It invokes the exact ``web_fetch``
instance assembled for this delegation, saves the returned text before any
model-facing summary or outer output budget can truncate it, and records stable
source/material identities in the report ledger.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from langchain.tools import tool

from deerflow.sp.report import store as report_store
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError, utc_now_iso

MATERIAL_PREVIEW_CHARS = 1200
MATERIAL_MAX_CHARS = 8_000_000


def normalize_source_url(url: str) -> str:
    """Conservatively normalize a URL for source identity.

    Scheme/host case and the fragment are not meaningful for retrieval; path,
    query order, and query values are preserved so different API calls are not
    collapsed into one source.
    """
    raw = str(url or "").strip()
    if not raw:
        raise ReportValidationError("url must be a non-empty string")
    parts = urlsplit(raw)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        raise ReportValidationError("url must be an absolute http(s) URL")
    scheme = parts.scheme.lower()
    host = parts.hostname.lower() if parts.hostname else ""
    if not host:
        raise ReportValidationError("url must include a host")
    port = parts.port
    netloc = host if port in (None, 80 if scheme == "http" else 443) else f"{host}:{port}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def _provider_scope(fetch_tool: Any, text: str) -> tuple[str, Any]:
    """Return ``(content_scope, upstream_truncation)`` for one fetch return."""
    module = ""
    for candidate in (
        type(fetch_tool).__module__,
        getattr(getattr(fetch_tool, "func", None), "__module__", ""),
        getattr(getattr(fetch_tool, "coroutine", None), "__module__", ""),
    ):
        if isinstance(candidate, str) and candidate:
            module = candidate
            break
    if "jina_ai" in module:
        return "fetch_tool_return", True
    if "tavily" in module:
        return "fetch_tool_return", True
    if "direct_fetch" in module:
        return "fetch_tool_return", "<truncated>" in text
    if "<truncated>" in text:
        return "fetch_tool_return", True
    return "fetch_tool_return", "unknown"


def _is_transport_error(value: Any) -> str | None:
    if isinstance(value, Mapping) and value.get("error"):
        return str(value.get("error"))
    if not isinstance(value, str):
        return "fetch tool returned a non-text result"
    text = value.strip()
    if not text:
        return "fetch tool returned empty text"
    if text.lower().startswith("error:"):
        return text[:1000]
    return None


def invoke_fetch_tool(fetch_tool: Any, url: str) -> Any:
    """Invoke the configured fetch tool through its standard tool interface."""
    return fetch_tool.invoke({"url": url})


def _source_for_url(ledger: ReportLedger, normalized_url: str, *, url: str, title: str | None) -> dict[str, Any]:
    sources = ledger.payload.setdefault("sources", [])
    for source in sources:
        if isinstance(source, Mapping) and source.get("normalized_url") == normalized_url:
            if title:
                source["title"] = title
            return dict(source)
    source_id = ledger.next_source_id()
    source = {
        "source_id": source_id,
        "url": url,
        "normalized_url": normalized_url,
        "title": title,
        "first_seen_at": utc_now_iso(),
    }
    sources.append(source)
    return source


def _existing_material(ledger: ReportLedger, source_id: str, content_sha: str) -> dict[str, Any] | None:
    for material in ledger.materials():
        if material.get("source_id") == source_id and material.get("sha256") == content_sha:
            return material
    return None


def build_fetch_material_tool(
    *,
    paths: report_store.ReportPaths,
    action_id: str,
    gap_ids: list[str],
    fetch_tool: Any,
):
    """Create the Researcher-bound material capture tool for one delegation."""

    @tool("report_fetch_material", parse_docstring=True)
    def report_fetch_material(url: str, title: str | None = None, locator_note: str | None = None) -> str:
        """Fetch an exact URL with the configured web_fetch tool and save its returned text.

        Use this instead of web_fetch for report research. The saved material is
        readable by path by both Researcher and Outline; the returned preview is
        never a substitute for the saved text. A transport error is reported
        explicitly and is not registered as evidence.

        Args:
            url: Exact public HTTP(S) URL to retrieve.
            title: Optional human-readable source title.
            locator_note: Optional note about the relevant section or query.
        """
        try:
            normalized = normalize_source_url(url)
        except ReportValidationError as exc:
            return json.dumps({"saved": False, "error": f"invalid_url: {exc}"}, ensure_ascii=False)

        try:
            ledger = ReportLedger.load(paths)
        except Exception as exc:  # noqa: BLE001 - tool boundary must report, not crash
            return json.dumps({"saved": False, "error": f"report_ledger_unavailable: {exc}"}, ensure_ascii=False)
        action = ledger.action(action_id)
        if action is None:
            return json.dumps({"saved": False, "error": f"research action was not begun: {action_id}"}, ensure_ascii=False)

        try:
            raw = invoke_fetch_tool(fetch_tool, str(url).strip())
        except Exception as exc:  # noqa: BLE001 - transport failure is data, not a tool crash
            ledger.record_tool_execution(action_id, tool="report_fetch_material", ok=False, detail=f"{type(exc).__name__}: {exc}")
            ledger.save()
            return json.dumps(
                {"saved": False, "error": f"fetch_failed: {type(exc).__name__}: {exc}", "url": url},
                ensure_ascii=False,
            )

        error = _is_transport_error(raw)
        if error is not None:
            ledger.record_tool_execution(action_id, tool="report_fetch_material", ok=False, detail=error)
            ledger.save()
            return json.dumps({"saved": False, "error": f"fetch_failed: {error}", "url": url}, ensure_ascii=False)

        text = str(raw)
        if len(text) > MATERIAL_MAX_CHARS:
            ledger.record_tool_execution(action_id, tool="report_fetch_material", ok=False, detail="material exceeded the maximum saved size")
            ledger.save()
            return json.dumps({"saved": False, "error": "fetch return exceeded the maximum saveable size", "chars": len(text)}, ensure_ascii=False)

        content_sha = report_store.sha256_text(text)
        try:
            source = _source_for_url(ledger, normalized, url=str(url).strip(), title=str(title).strip() if isinstance(title, str) and title.strip() else None)
            source_id = str(source["source_id"])
            existing = _existing_material(ledger, source_id, content_sha)
            if existing is not None:
                ledger.record_tool_execution(action_id, tool="report_fetch_material", ok=True, detail=f"reused {existing.get('material_id')}")
                ledger.save()
                return json.dumps(
                    _material_payload(existing, duplicate_of=str(existing.get("material_id")), preview=str(existing.get("preview") or "")),
                    ensure_ascii=False,
                    indent=2,
                )

            material_id = ledger.next_material_id()
            path = paths.material_file(source_id, content_sha)
            report_store.write_text_atomic(path, text)
            read_back = path.read_text(encoding="utf-8")
            if read_back != text:
                return json.dumps({"saved": False, "error": "material verification failed: file content differs from tool return"}, ensure_ascii=False)

            preview = " ".join(text.split())[:MATERIAL_PREVIEW_CHARS]
            content_scope, upstream_truncation = _provider_scope(fetch_tool, text)
            material = {
                "material_id": material_id,
                "source_id": source_id,
                "url": str(url).strip(),
                "normalized_url": normalized,
                "title": str(title).strip() if isinstance(title, str) and title.strip() else source.get("title"),
                "locator_note": str(locator_note).strip() if isinstance(locator_note, str) and locator_note.strip() else None,
                "path": str(path),
                "virtual_path": paths.virtual(path),
                "chars": len(text),
                "lines": report_store.line_count(text),
                "sha256": content_sha,
                "content_scope": content_scope,
                "upstream_truncation": upstream_truncation,
                "preview": preview,
                "fetched_at": utc_now_iso(),
                "action_id": action_id,
                "gap_ids": list(gap_ids),
            }
            ledger.payload.setdefault("materials", []).append(material)
            ledger.record_tool_execution(action_id, tool="report_fetch_material", ok=True, detail=f"saved {material_id}")
            ledger.save()
            return json.dumps(_material_payload(material, duplicate_of=None, preview=preview), ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001 - persistence failure must not look like a research result
            try:
                ledger.record_tool_execution(action_id, tool="report_fetch_material", ok=False, detail=f"material_save_failed: {exc}")
                ledger.save()
            except Exception:
                pass
            return json.dumps({"saved": False, "error": f"material_save_failed: {type(exc).__name__}: {exc}"}, ensure_ascii=False)

    return report_fetch_material


def _material_payload(material: Mapping[str, Any], *, duplicate_of: str | None, preview: str) -> dict[str, Any]:
    return {
        "saved": True,
        "source_id": material.get("source_id"),
        "material_id": material.get("material_id"),
        "path": material.get("virtual_path"),
        "chars": material.get("chars"),
        "lines": material.get("lines"),
        "sha256": material.get("sha256"),
        "content_scope": material.get("content_scope"),
        "upstream_truncation": material.get("upstream_truncation"),
        "title": material.get("title"),
        "url": material.get("url"),
        "preview": preview,
        "duplicate_of": duplicate_of,
    }


def material_host_path(ledger: ReportLedger, material_id: str) -> Path | None:
    material = ledger.material(material_id)
    if material is None or not material.get("path"):
        return None
    return Path(str(material["path"]))
