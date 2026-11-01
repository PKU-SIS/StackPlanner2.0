"""Persist content-bearing search results before model-facing truncation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Annotated, Any

from langchain.tools import tool
from pydantic import Field

from deerflow.sp.report import store as report_store
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError, utc_now_iso
from deerflow.sp.report.materials import MATERIAL_MAX_CHARS, _material_payload, _run_coroutine_sync, _save_material_text, _source_for_url, normalize_source_url

SEARCH_DISPLAY_CHARS = 3000
SEARCH_RESPONSE_CHARS = 10_000


def _display_response(query: str, results: list[dict[str, Any]]) -> str:
    """Keep normal ten-result responses below the 12k externalization trigger.

    Full bodies are already saved. Avoid repeating the same preview, body,
    summary, URL and provenance several times and forcing a read-back loop.
    Identities and original paths always survive excerpt shortening.
    """
    saved = [row for row in results if row.get("saved")]
    materials = [{key: row.get(key) for key in ("source_id", "material_id", "path", "url", "title", "lines")} for row in saved]
    for summary_chars, body_chars in ((600, 600), (500, 300), (400, 150), (250, 0), (100, 0)):
        displayed = []
        for row in results:
            entry = {key: row.get(key) for key in ("saved", "source_id", "material_id") if key in row}
            if not row.get("saved"):
                entry.update({key: row.get(key) for key in ("url", "title", "error")})
            entry["summary"] = str(row.get("summary") or row.get("snippet") or "")[:summary_chars]
            entry["content"] = str(row.get("content") or "")[:body_chars]
            entry["content_truncated"] = bool(row.get("content_truncated")) or len(str(row.get("content") or "")) > body_chars
            displayed.append(entry)
        response = json.dumps({"query": query, "total_results": len(results), "saved_count": len(saved), "materials": materials, "results": displayed}, ensure_ascii=False)
        if len(response) <= SEARCH_RESPONSE_CHARS:
            return response
    # Exceptional huge URLs/catalogs retain exact identities even if the outer
    # middleware must externalize them. Never cut a source URL or drop a source.
    return response


def _active_action(paths: report_store.ReportPaths, action_id: str) -> ReportLedger:
    ledger = ReportLedger.load(paths)
    action = ledger.action(action_id)
    if action is None or action.get("status") != "in_flight":
        raise ReportValidationError("research action is not accepting search materials")
    return ledger


def _capture(paths: report_store.ReportPaths, action_id: str, gap_ids: list[str], row: Mapping[str, Any]) -> dict[str, Any]:
    candidate = {key: row.get(key) for key in ("url", "title", "summary", "snippet")}
    text = row.get("content")
    if not isinstance(text, str) or not text.strip():
        return {**candidate, "saved": False, "error": "no_body; use Fetch only if this candidate is needed"}
    if len(text) > MATERIAL_MAX_CHARS:
        return {**candidate, "saved": False, "error": "body exceeds material size limit"}
    try:
        url = str(row.get("url") or "").strip()
        normalized = normalize_source_url(url)
        title = str(row.get("title") or "").strip() or None
        with report_store.report_lock(paths):
            ledger = _active_action(paths, action_id)
            source = _source_for_url(ledger, normalized, url=url, title=title)
            material, reused = _save_material_text(
                ledger,
                paths,
                text=text,
                source_id=str(source["source_id"]),
                fields={
                    "url": url,
                    "normalized_url": normalized,
                    "title": title,
                    "content_scope": "search_return_markdown",
                    "upstream_truncation": "unknown",
                    "fetched_at": utc_now_iso(),
                    "action_id": action_id,
                    "gap_ids": list(gap_ids),
                },
            )
            ledger.save()
        # Preserve the exact body on disk; model-facing content is only a bounded
        # excerpt with explicit provenance. No second Fetch is required to save it.
        payload = _material_payload(material, duplicate_of=str(material["material_id"]) if reused else None, preview=str(material["preview"]))
        return {**candidate, **payload, "content": text[:SEARCH_DISPLAY_CHARS], "content_truncated": len(text) > SEARCH_DISPLAY_CHARS}
    except Exception as exc:  # noqa: BLE001 - one bad result must not erase valid sibling materials
        return {**candidate, "saved": False, "error": f"material_save_failed: {type(exc).__name__}: {exc}"}


def build_search_material_tool(*, paths: report_store.ReportPaths, action_id: str, gap_ids: list[str], search_tool: Any):
    """Bind a content-bearing provider to one real report research action."""

    @tool("web_search", parse_docstring=True)
    def web_search(query: Annotated[str, Field(min_length=1, max_length=128)], max_results: Annotated[int, Field(ge=1, le=50)] = 10) -> str:
        """Search and save available page bodies as citable materials in one call.

        Use the returned summaries and body excerpts directly for the synthesis.
        Each saved result already has a source id and readable original path;
        do not fetch it again. Fetch only missing or insufficient bodies, and
        read saved originals only where exact content is still needed.

        Args:
            query: Specific search terms, at most 128 Unicode characters.
            max_results: Candidate count, default 10.
        """
        try:
            _active_action(paths, action_id)
            args = {"query": query, "max_results": max_results}
            if callable(getattr(search_tool, "coroutine", None)):
                raw = _run_coroutine_sync(lambda: search_tool.ainvoke(args))
            else:
                raw = search_tool.invoke(args)
            result = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(result, Mapping):
                raise ValueError("search returned a non-object response")
            error = result.get("error")
            rows = result.get("results")
            if not error and (not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows)):
                raise ValueError("search results must be a list of objects")
        except Exception as exc:  # noqa: BLE001 - transport and action errors are observable tool results
            result = {"error": f"search_failed: {type(exc).__name__}: {exc}"}
            error = result["error"]
            rows = []
        if error:
            try:
                with report_store.report_lock(paths):
                    ledger = _active_action(paths, action_id)
                    ledger.record_tool_execution(action_id, tool="web_search", ok=False, detail=str(error))
                    ledger.save()
            except Exception:  # noqa: BLE001 - preserve the original failure when action is closed
                pass
            return json.dumps(dict(result), ensure_ascii=False)

        results = [_capture(paths, action_id, gap_ids, row) for row in rows]
        saved = [row for row in results if row.get("saved")]
        try:
            with report_store.report_lock(paths):
                ledger = _active_action(paths, action_id)
                ledger.record_tool_execution(action_id, tool="web_search", ok=True, detail=f"returned {len(results)} candidates; saved/reused {len(saved)} bodies")
                ledger.save()
        except Exception as exc:  # noqa: BLE001 - late close/persistence failure must not look successful
            return json.dumps({"error": f"search_record_failed: {type(exc).__name__}: {exc}"}, ensure_ascii=False)
        return _display_response(query, results)

    return web_search
