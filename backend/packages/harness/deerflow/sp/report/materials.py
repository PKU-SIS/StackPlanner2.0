"""Researcher material capture built on the runtime's configured fetch tool.

The wrapper never performs HTTP itself.  It invokes the exact ``web_fetch``
instance assembled for this delegation, saves the returned text before any
model-facing summary or outer output budget can truncate it, and records stable
source/material identities in the report ledger.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import threading
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


def _fetch_module(fetch_tool: Any) -> str:
    for candidate in (
        getattr(fetch_tool, "coroutine", None),
        getattr(fetch_tool, "func", None),
        type(fetch_tool),
    ):
        candidate_module = getattr(candidate, "__module__", "")
        if isinstance(candidate_module, str) and candidate_module and candidate_module != "langchain_core.tools.structured":
            return candidate_module
    return ""


def _provider_scope(fetch_tool: Any, text: str) -> tuple[str, Any]:
    """Return ``(content_scope, upstream_truncation)`` for one fetch return."""
    module = _fetch_module(fetch_tool)
    if "jina_ai" in module:
        return "fetch_tool_return", True
    if "tavily" in module:
        return "fetch_tool_return", True
    if "direct_fetch" in module:
        return "fetch_tool_return", "<truncated>" in text
    if "<truncated>" in text:
        return "fetch_tool_return", True
    return "fetch_tool_return", "unknown"


def _looks_like_html(text: str) -> bool:
    head = text.lstrip()[:4096].lower()
    return head.startswith(("<!doctype html", "<html", "<head", "<body")) or "<html" in head


def _readability_markdown(raw_html: str) -> tuple[str | None, str | None]:
    """Extract readable Markdown without registering placeholder text as content."""
    try:
        from deerflow.utils.readability import ReadabilityExtractor

        article = ReadabilityExtractor().extract_article(raw_html)
        markdown = article.to_markdown().strip()
    except Exception as exc:  # noqa: BLE001 - extraction failure is a material failure
        return None, f"{type(exc).__name__}: {exc}"
    lowered = markdown.lower()
    if not markdown or "no content could be extracted" in lowered or "no content available" in lowered:
        return None, "readability extraction returned no content"
    return markdown, None


def _direct_fetch_options() -> dict[str, Any]:
    from deerflow.config import get_app_config

    config = get_app_config().get_tool_config("web_fetch")
    extra = dict(config.model_extra or {}) if config is not None else {}

    def positive_int(value: Any, default: int) -> int:
        if isinstance(value, bool):
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    def positive_float(value: Any, default: float) -> float:
        if isinstance(value, bool):
            return default
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    def boolean(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}:
            return True
        if isinstance(value, str) and value.strip().lower() in {"0", "false", "no", "off"}:
            return False
        return default

    return {
        "timeout": positive_float(extra.get("timeout"), 20.0),
        "max_bytes": positive_int(extra.get("max_bytes"), 1_048_576),
        "max_chars": positive_int(extra.get("max_chars"), 20_000),
        "allow_private_addresses": boolean(extra.get("allow_private_addresses"), False),
        "trust_env": boolean(extra.get("trust_env"), False),
    }


def _fetch_material_text(fetch_tool: Any, url: str) -> tuple[str | None, str, Any, str | None]:
    """Fetch raw text, optionally extracting HTML before report-side truncation.

    The ordinary ``web_fetch`` tool keeps its existing behavior.  This report
    path only uses direct_fetch's public function when that provider is the one
    configured, inherits its timeout/byte/access options, and extracts readable
    Markdown before applying the material-save bound.
    """
    module = _fetch_module(fetch_tool)
    if "direct_fetch" not in module:
        raw = invoke_fetch_tool(fetch_tool, url)
        error = _is_transport_error(raw)
        if error is not None:
            return None, "fetch_tool_return", "unknown", error
        text = str(raw)
        content_scope, truncation = _provider_scope(fetch_tool, text)
        return text, content_scope, truncation, None

    from deerflow.community.direct_fetch.tools import fetch_public_url

    options = _direct_fetch_options()
    # Fetch the raw payload without the ordinary 20k display cap; the report
    # extraction below determines the readable body.  max_bytes remains the
    # authoritative transport bound.
    raw = _run_coroutine_sync(
        lambda: fetch_public_url(
            url,
            timeout=options["timeout"],
            max_bytes=options["max_bytes"],
            max_chars=options["max_bytes"],
            allow_private_addresses=options["allow_private_addresses"],
            trust_env=options["trust_env"],
        )
    )
    error = _is_transport_error(raw)
    if error is not None:
        return None, "fetch_tool_return", "unknown", error
    raw_text = str(raw)
    raw_truncated = "<truncated>" in raw_text
    if _looks_like_html(raw_text):
        markdown, extraction_error = _readability_markdown(raw_text)
        if extraction_error is not None:
            return None, "extracted_markdown", raw_truncated, f"html_extraction_failed: {extraction_error}"
        return markdown, "extracted_markdown", raw_truncated, None
    # JSON/plain text and already-Markdown responses keep their content path.
    return raw_text, "fetch_tool_return", raw_truncated, None


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


def _run_coroutine_sync(factory: Any) -> Any:
    """Run an async tool coroutine from a sync tool without nested-loop errors."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    result: dict[str, Any] = {}
    context = contextvars.copy_context()

    def runner() -> None:
        try:
            result["value"] = context.run(asyncio.run, factory())
        except BaseException as exc:  # noqa: BLE001 - re-raised in caller thread
            result["error"] = exc

    thread = threading.Thread(target=runner, name="report-fetch-async", daemon=True)
    thread.start()
    thread.join()
    error = result.get("error")
    if error is not None:
        raise error
    return result.get("value")


def invoke_fetch_tool(fetch_tool: Any, url: str) -> Any:
    """Invoke the configured fetch tool through its standard sync/async interface."""
    coroutine = getattr(fetch_tool, "coroutine", None)
    if callable(coroutine):
        return _run_coroutine_sync(lambda: fetch_tool.ainvoke({"url": url}))
    func = getattr(fetch_tool, "func", None)
    if callable(func) or hasattr(fetch_tool, "invoke"):
        return fetch_tool.invoke({"url": url})
    raise TypeError("configured web_fetch tool exposes neither a sync nor async interface")


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


def _save_material_text(
    ledger: ReportLedger,
    paths: report_store.ReportPaths,
    *,
    text: str,
    source_id: str,
    fields: Mapping[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Persist one exact text payload and return (material, reused).

    Shared by URL capture and user-input registration so both paths use the
    same content identity, file naming, read-back verification, and preview
    construction. The caller must hold the report lock and save the ledger.
    """
    content_sha = report_store.sha256_text(text)
    existing = _existing_material(ledger, source_id, content_sha)
    if existing is not None:
        return existing, True

    material_id = ledger.next_material_id()
    path = paths.material_file(source_id, content_sha)
    report_store.write_text_atomic(path, text)
    read_back = path.read_text(encoding="utf-8")
    if read_back != text:
        raise OSError("material verification failed: file content differs from tool return")

    preview = " ".join(text.split())[:MATERIAL_PREVIEW_CHARS]
    material = {
        "material_id": material_id,
        "source_id": source_id,
        "path": str(path),
        "virtual_path": paths.virtual(path),
        "chars": len(text),
        "lines": report_store.line_count(text),
        "sha256": content_sha,
        "preview": preview,
        **dict(fields),
    }
    ledger.payload.setdefault("materials", []).append(material)
    return material, False


def _source_for_user_input(
    ledger: ReportLedger,
    input_item: Mapping[str, Any],
    *,
    title: str | None,
) -> dict[str, Any]:
    """Return the stable source record for one host-saved user input."""
    input_id = str(input_item.get("input_id") or "").strip()
    sources = ledger.payload.setdefault("sources", [])
    for source in sources:
        if isinstance(source, Mapping) and source.get("source_kind") == "user_input" and source.get("input_id") == input_id:
            if title:
                source["title"] = title
            return dict(source)
    source_id = ledger.next_source_id()
    source = {
        "source_id": source_id,
        "source_kind": "user_input",
        "input_id": input_id,
        "input_path": input_item.get("virtual_path"),
        "title": title or f"User input {input_id}",
        "first_seen_at": utc_now_iso(),
    }
    sources.append(source)
    return source


def build_register_user_material_tool(*, paths: report_store.ReportPaths):
    """Create the Outline-bound tool that registers a saved user input."""

    @tool("report_save_user_material", parse_docstring=True)
    def report_save_user_material(input_id: str, title: str | None = None) -> str:
        """Register one existing user input as a citable report material.

        Use this only when the user's own input contains evidence needed by the
        plan. The host reads the already-saved input file; do not retype or
        summarize the source text, and do not invent a URL. This does not
        perform research and does not require a Researcher action. The returned
        material_id and source_id are the only identities to cite.

        Args:
            input_id: Existing input_id from report_packet.inputs.
            title: Optional human-readable source title.
        """
        try:
            ledger = ReportLedger.load(paths)
        except Exception as exc:  # noqa: BLE001 - tool boundary must report, not crash
            return json.dumps({"saved": False, "error": f"report_ledger_unavailable: {exc}"}, ensure_ascii=False)

        input_item = ledger.input(str(input_id))
        if input_item is None:
            return json.dumps(
                {"saved": False, "error": f"unknown input_id: {input_id}"},
                ensure_ascii=False,
            )
        input_path = Path(str(input_item.get("path") or ""))
        if not str(input_path) or not input_path.is_file():
            return json.dumps(
                {"saved": False, "error": f"user input file is unavailable: {input_id}"},
                ensure_ascii=False,
            )

        clean_title = str(title).strip() if isinstance(title, str) and title.strip() else None
        try:
            with report_store.report_lock(paths):
                ledger = ReportLedger.load(paths)
                input_item = ledger.input(str(input_id))
                if input_item is None:
                    return json.dumps(
                        {"saved": False, "error": f"unknown input_id: {input_id}"},
                        ensure_ascii=False,
                    )
                text = Path(str(input_item.get("path") or "")).read_text(encoding="utf-8")
                if not text.strip():
                    return json.dumps(
                        {"saved": False, "error": f"user input is empty: {input_id}"},
                        ensure_ascii=False,
                    )
                if len(text) > MATERIAL_MAX_CHARS:
                    return json.dumps(
                        {"saved": False, "error": "user input exceeded the maximum saveable size"},
                        ensure_ascii=False,
                    )
                source = _source_for_user_input(ledger, input_item, title=clean_title)
                material, reused = _save_material_text(
                    ledger,
                    paths,
                    text=text,
                    source_id=str(source["source_id"]),
                    fields={
                        "input_id": str(input_id),
                        "source_kind": "user_input",
                        "title": clean_title or source.get("title"),
                        "content_scope": "user_input",
                        "upstream_truncation": False,
                        "created_at": utc_now_iso(),
                    },
                )
                ledger.save()
        except Exception as exc:  # noqa: BLE001 - persistence failure must be explicit
            return json.dumps(
                {"saved": False, "error": f"material_save_failed: {type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )

        return json.dumps(
            _material_payload(
                material,
                duplicate_of=str(material.get("material_id")) if reused else None,
                preview=str(material.get("preview") or ""),
            ),
            ensure_ascii=False,
            indent=2,
        )

    return report_save_user_material


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
        """Fetch an exact URL with the configured web_fetch tool and save its content.

        Use this instead of web_fetch for report research. HTML pages are
        extracted to readable Markdown before saving; JSON, plain text, and
        already-Markdown responses keep their content path. The saved material is
        readable by path by both Researcher and Outline; the returned preview is
        never a substitute for the saved text. A transport, extraction, or
        truncation failure is reported explicitly and is not registered as a
        complete source.

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
            text, content_scope, upstream_truncation, fetch_error = _fetch_material_text(fetch_tool, str(url).strip())
        except Exception as exc:  # noqa: BLE001 - transport failure is data, not a tool crash
            return _record_material_failure(
                paths,
                action_id,
                f"fetch_failed: {type(exc).__name__}: {exc}",
                url=str(url).strip(),
            )

        if fetch_error is not None:
            return _record_material_failure(paths, action_id, f"fetch_failed: {fetch_error}", url=str(url).strip())

        if text is None or not text.strip():
            return _record_material_failure(paths, action_id, "fetch_failed: fetch returned no usable content", url=str(url).strip())
        if len(text) > MATERIAL_MAX_CHARS:
            return _record_material_failure(
                paths,
                action_id,
                "fetch return exceeded the maximum saveable size",
                url=str(url).strip(),
            )

        try:
            with report_store.report_lock(paths):
                ledger = ReportLedger.load(paths)
                action = ledger.action(action_id)
                if action is None:
                    return json.dumps({"saved": False, "error": f"research action was not begun: {action_id}"}, ensure_ascii=False)
                if action.get("status") != "in_flight":
                    return json.dumps(
                        {"saved": False, "error": f"research action is no longer accepting materials: {action.get('status')}"},
                        ensure_ascii=False,
                    )
                source = _source_for_url(ledger, normalized, url=str(url).strip(), title=str(title).strip() if isinstance(title, str) and title.strip() else None)
                material, reused = _save_material_text(
                    ledger,
                    paths,
                    text=text,
                    source_id=str(source["source_id"]),
                    fields={
                        "url": str(url).strip(),
                        "normalized_url": normalized,
                        "title": str(title).strip() if isinstance(title, str) and title.strip() else source.get("title"),
                        "locator_note": str(locator_note).strip() if isinstance(locator_note, str) and locator_note.strip() else None,
                        "content_scope": content_scope,
                        "upstream_truncation": upstream_truncation,
                        "fetched_at": utc_now_iso(),
                        "action_id": action_id,
                        "gap_ids": list(gap_ids),
                    },
                )
                detail = f"reused {material.get('material_id')}" if reused else f"saved {material.get('material_id')}"
                ledger.record_tool_execution(action_id, tool="report_fetch_material", ok=True, detail=detail)
                ledger.save()
                return json.dumps(
                    _material_payload(
                        material,
                        duplicate_of=str(material.get("material_id")) if reused else None,
                        preview=str(material.get("preview") or ""),
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as exc:  # noqa: BLE001 - persistence failure must not look like a research result
            return _record_material_failure(
                paths,
                action_id,
                f"material_save_failed: {type(exc).__name__}: {exc}",
                url=str(url).strip(),
            )

    return report_fetch_material


def _record_material_failure(paths: report_store.ReportPaths, action_id: str, error: str, *, url: str | None = None) -> str:
    """Record a failed attempt without registering material evidence."""
    try:
        with report_store.report_lock(paths):
            ledger = ReportLedger.load(paths)
            ledger.record_tool_execution(action_id, tool="report_fetch_material", ok=False, detail=error)
            ledger.save()
    except Exception as exc:  # noqa: BLE001 - the tool still reports the original failure
        error = f"{error}; failure record unavailable: {type(exc).__name__}: {exc}"
    payload: dict[str, Any] = {"saved": False, "error": error}
    if url:
        payload["url"] = url
    return json.dumps(payload, ensure_ascii=False)


def _material_payload(material: Mapping[str, Any], *, duplicate_of: str | None, preview: str) -> dict[str, Any]:
    return {
        "saved": True,
        "source_id": material.get("source_id"),
        "material_id": material.get("material_id"),
        "input_id": material.get("input_id"),
        "source_kind": material.get("source_kind"),
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
