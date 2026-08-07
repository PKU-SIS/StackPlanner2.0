"""Web search tool powered by Bocha / LangSearch."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from langchain.tools import tool

from deerflow.config import get_app_config

logger = logging.getLogger(__name__)

_BOCHA_ENDPOINT = "https://api.langsearch.com/v1/web-search"
_DEFAULT_MAX_RESULTS = 5
_MAX_RESULTS = 10
_api_key_warned = False


def _tool_config_extra(tool_name: str = "web_search") -> dict[str, Any]:
    config = get_app_config().get_tool_config(tool_name)
    return dict(config.model_extra or {}) if config is not None else {}


def _get_api_key(tool_name: str = "web_search") -> str | None:
    extra = _tool_config_extra(tool_name)
    value = extra.get("api_key")
    if isinstance(value, str) and value.strip():
        return value.strip()
    value = os.getenv("BOCHA_API_KEY") or os.getenv("LANGSEARCH_API_KEY")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _coerce_max_results(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_RESULTS
    if parsed <= 0:
        return _DEFAULT_MAX_RESULTS
    return min(parsed, _MAX_RESULTS)


def _coerce_positive_float(value: object, default: float) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _coerce_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _clean_query(query: str) -> str:
    query = query.strip()
    if len(query) > 500:
        query = query[:500]
    return query


def _missing_key_error(query: str) -> str:
    global _api_key_warned
    if not _api_key_warned:
        _api_key_warned = True
        logger.warning(
            "Bocha/LangSearch API key is not set. Set BOCHA_API_KEY or LANGSEARCH_API_KEY in your environment or provide api_key in config.yaml."
        )
    return json.dumps(
        {"error": "BOCHA_API_KEY is not configured", "query": query},
        ensure_ascii=False,
    )


def _request_error(query: str, message: str) -> str:
    return json.dumps({"error": message, "query": query}, ensure_ascii=False)


def _normalize_items(data: dict[str, Any], max_results: int) -> list[dict[str, str]]:
    raw_data = data.get("data")
    raw_items = None
    if isinstance(raw_data, dict):
        raw_pages = raw_data.get("webPages")
        if isinstance(raw_pages, dict):
            raw_items = raw_pages.get("value")
    if not isinstance(raw_items, list):
        return []

    results: list[dict[str, str]] = []
    for item in raw_items[:max_results]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("name") or item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        summary = str(item.get("summary") or "").strip()
        if not title and not url and not snippet and not summary:
            continue
        results.append(
            {
                "title": title,
                "url": url,
                "content": summary or snippet,
                "snippet": snippet,
                "summary": summary,
                "site_name": str(item.get("siteName") or "").strip(),
                "date_published": str(
                    item.get("datePublished") or item.get("dateLastCrawled") or ""
                ).strip(),
            }
        )
    return results


def _bocha_post(
    *,
    api_key: str,
    query: str,
    max_results: int,
    timeout: float,
    trust_env: bool,
    freshness: str,
) -> tuple[dict[str, Any] | None, str | None]:
    payload = {
        "query": query,
        "freshness": freshness,
        "summary": True,
        "count": max_results,
        "page": 1,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=timeout, trust_env=trust_env) as client:
            response = client.post(_BOCHA_ENDPOINT, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            return None, _request_error(
                query,
                "Bocha/LangSearch returned an unexpected response format",
            )
        return data, None
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Bocha/LangSearch API returned HTTP %s: %s",
            exc.response.status_code,
            (exc.response.text or "")[:500],
        )
        return None, _request_error(
            query,
            f"Bocha/LangSearch API error: HTTP {exc.response.status_code}",
        )
    except Exception as exc:
        logger.error(
            "Bocha/LangSearch request failed: %s: %s",
            type(exc).__name__,
            str(exc)[:500],
        )
        return None, _request_error(query, f"{type(exc).__name__}: {str(exc)[:500]}")


@tool("web_search", parse_docstring=True)
def web_search_tool(query: str, max_results: int = _DEFAULT_MAX_RESULTS) -> str:
    """Search the web using Bocha / LangSearch.

    Args:
        query: Search keywords describing what you want to find. Be specific for better results.
        max_results: Maximum number of search results to return. Default is 5, capped at 10.
    """
    extra = _tool_config_extra("web_search")
    if "max_results" in extra:
        max_results = extra.get("max_results", max_results)
    max_results = _coerce_max_results(max_results)
    query = _clean_query(query)

    api_key = _get_api_key("web_search")
    if not api_key:
        return _missing_key_error(query)

    timeout = _coerce_positive_float(extra.get("timeout"), 60.0)
    trust_env = _coerce_bool(extra.get("trust_env"), False)
    freshness = str(extra.get("freshness") or "noLimit")

    data, error_json = _bocha_post(
        api_key=api_key,
        query=query,
        max_results=max_results,
        timeout=timeout,
        trust_env=trust_env,
        freshness=freshness,
    )
    if error_json is not None:
        return error_json
    if data is None:
        return _request_error(query, "Bocha/LangSearch returned no data")

    results = _normalize_items(data, max_results)
    if not results:
        return json.dumps({"error": "No results found", "query": query}, ensure_ascii=False)

    return json.dumps(
        {
            "query": query,
            "total_results": len(results),
            "results": results,
        },
        indent=2,
        ensure_ascii=False,
    )
