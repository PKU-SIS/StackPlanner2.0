"""DeepSeek's read-only Search/Fetch APIs, isolated from ambient proxy state."""

import ipaddress
import json
import os
import time
from typing import Annotated, Any
from urllib.parse import urlsplit

import httpx
from langchain.tools import tool
from pydantic import Field

from deerflow.config import get_app_config

TOOLS_BASE_URL = "https://api.deepseek.com/v1/tools"


def _options(name: str) -> dict[str, Any]:
    config = get_app_config().get_tool_config(name)
    return dict(config.model_extra or {}) if config is not None else {}


def _post(name: str, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    options = _options(name)
    api_key = str(options.get("api_key") or os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        return {"error": "DEEPSEEK_API_KEY_MISSING", "retryable": False}
    try:
        timeout = min(120.0, max(1.0, float(options.get("timeout", 30))))
    except (TypeError, ValueError):
        timeout = 30.0
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        for attempt in range(2):
            try:
                response = client.post(f"{TOOLS_BASE_URL}/{operation}", headers=headers, json=payload)
            except httpx.TransportError as exc:
                if attempt == 0:
                    time.sleep(1)
                    continue
                return {"error": "DEEPSEEK_TRANSPORT_ERROR", "message": type(exc).__name__, "retryable": True}
            retryable = response.status_code in {408, 429} or response.status_code >= 500
            if retryable and attempt == 0:
                time.sleep(1)
                continue
            try:
                data = response.json()
            except ValueError:
                return {"error": "DEEPSEEK_INVALID_RESPONSE", "status_code": response.status_code, "retryable": False}
            if response.status_code != 200:
                error = data.get("error") if isinstance(data, dict) else None
                error = error if isinstance(error, dict) else {}
                return {
                    "error": str(error.get("code") or "DEEPSEEK_REQUEST_FAILED").replace(api_key, "<redacted>"),
                    "message": str(error.get("message") or "Request failed").replace(api_key, "<redacted>"),
                    "status_code": response.status_code,
                    "retryable": retryable,
                }
            return data if isinstance(data, dict) else {"error": "DEEPSEEK_INVALID_RESPONSE", "retryable": False}
    return {"error": "DEEPSEEK_REQUEST_FAILED", "retryable": False}


@tool("web_search", parse_docstring=True)
def web_search_tool(query: Annotated[str, Field(min_length=1, max_length=128)], max_results: Annotated[int, Field(ge=1, le=50)] = 10) -> str:
    """Search related pages and return their Markdown bodies when available.

    Args:
        query: Specific search terms, at most 128 Unicode characters.
        max_results: Number of candidate documents to retrieve, default 10.
    """
    if not query.strip():
        return json.dumps({"error": "invalid_query", "retryable": False})
    result = _post("web_search", "search", {"query": query, "count": max_results, "need_content": True})
    if "error" not in result and not isinstance(result.get("results"), list):
        result = {"error": "DEEPSEEK_INVALID_RESPONSE", "retryable": False}
    result.setdefault("query", query)
    return json.dumps(result, ensure_ascii=False)


@tool("web_fetch", parse_docstring=True)
def web_fetch_tool(url: str) -> str:
    """Fetch one known public page as Markdown, only when more content is needed.

    Args:
        url: Exact public HTTP(S) URL returned by search, a read page or the user.
    """
    try:
        parsed = urlsplit(url)
        if len(url) > 8192 or parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError("expected a public HTTP(S) URL without credentials")
        if parsed.hostname == "localhost" or parsed.hostname.endswith(".localhost"):
            raise ValueError("private targets are not allowed")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError("private targets are not allowed")
    except ValueError as exc:
        return f"Error: invalid_url: {exc}"
    result = _post("web_fetch", "fetch", {"url": url})
    if result.get("error"):
        return "Error: " + json.dumps(result, ensure_ascii=False)
    content = result.get("content")
    return content if isinstance(content, str) and content.strip() else "Error: DeepSeek Fetch returned no usable Markdown"
