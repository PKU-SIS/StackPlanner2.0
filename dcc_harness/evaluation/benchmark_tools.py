"""Public retrieval as ordinary native SP tools, with no DCC execution gates."""

from __future__ import annotations

import asyncio
import json
import threading
import weakref
from urllib.parse import urldefrag

from langchain.tools import ToolRuntime, tool

from dcc_harness.tools import InfrastructureError, WebTools

_clients = weakref.WeakKeyDictionary()
_lock = threading.Lock()


def _identity(runtime: ToolRuntime[dict | None]) -> str:
    context = runtime.context or {}
    identity = context.get("thread_id") or runtime.config.get("configurable", {}).get("thread_id")
    if not identity:
        raise ValueError("benchmark retrieval requires a native thread_id")
    return str(identity)


def _client(thread_id: str) -> WebTools:
    # Native specialists run on isolated event loops. Do not share an asyncio
    # semaphore/client across those loops, or share page caches across tasks.
    loop = asyncio.get_running_loop()
    with _lock:
        clients = _clients.setdefault(loop, {})
        return clients.setdefault(thread_id, WebTools())


def _fetch_observation(result: dict, source: str) -> dict:
    """Format both budget candidates and delivered reads with the same protocol."""
    result = dict(result)
    next_start = result.pop("next_start", None)
    result["next_url"] = f"{source}#sp-read-start={next_start}" if next_start is not None else None
    for key in ("worksheets", "archive_members"):
        if key in result:
            result[key] = [
                {**item, "read_url": f"{source}#sp-read-start={item['start']}"}
                if type(item.get("start")) is int else dict(item)
                for item in result[key]
            ]
    for key in ("worksheets_note", "archive_members_note"):
        if key in result:
            result[key] = result[key].replace(
                "Fetch this URL with start set to a worksheet's start",
                "Pass the worksheet's read_url to web_fetch",
            ).replace(
                "Fetch this same URL with start set to a member's start",
                "Pass the member's read_url to web_fetch",
            )
    if "read_progress_note" in result:
        result["read_progress_note"] = result["read_progress_note"].replace(
            "next_start", "next_url"
        ).replace("worksheet or archive-member offset", "worksheet or archive-member read_url")
    return result


async def _invoke(name: str, runtime: ToolRuntime[dict | None], arguments: dict) -> str:
    from deerflow.config.paths import get_paths
    from deerflow.config.tool_output_config import ToolOutputConfig

    thread_id = _identity(runtime)
    try:
        client = _client(thread_id)
        if name == "web_search":
            result = await client.web_search(**arguments)
            if result.get("results") == [] and not result.get("error"):
                # Native search/error middlewares use this marker to stop
                # repeated failed searches. Keep provider diagnostics intact.
                result = {**result, "error": "No results found", "retryable": False}
        else:
            try:
                source, fragment = urldefrag(arguments["url"].strip())
                start = 0
                if fragment.startswith("sp-read-start="):
                    offset = fragment.removeprefix("sp-read-start=")
                    if not offset.isascii() or not offset.isdecimal() or len(offset) > 18:
                        raise ValueError("invalid read offset")
                    start = int(offset)
            except ValueError:
                result = {"error": "Invalid read URL or offset. Use the exact next_url returned by web_fetch."}
            else:
                app_config = (runtime.context or {}).get("app_config")
                output = getattr(app_config, "tool_output", None) or ToolOutputConfig()
                limits = [value for value in (
                    output.tool_overrides.get("web_fetch", output.externalize_min_chars),
                    output.fallback_max_chars,
                ) if value > 0]
                budget = min(limits) if limits else None
                if not output.enabled or "web_fetch" in output.exempt_tools:
                    budget = None
                result = await client.fetch_url(
                    url=source, start=start, max_output_chars=budget,
                    output_size=lambda value: len(json.dumps(_fetch_observation(value, source), ensure_ascii=False, allow_nan=False)),
                )
                result = _fetch_observation(result, source)
    except InfrastructureError as exc:
        from dcc_harness.search_r1 import _record_infrastructure_failure

        _record_infrastructure_failure(name, exc)
        # The backend already uses credential-free error messages. Keep the
        # failure as a real tool observation; native SP decides how to recover.
        result = {"error": "WEB_SEARCH_UNAVAILABLE" if name == "web_search" else "retrieval_infrastructure_error", "detail": str(exc), "retryable": True}
    event = {"tool": name, "arguments": arguments, "result": result}
    path = get_paths().thread_dir(thread_id, user_id=(runtime.context or {}).get("user_id")) / "benchmark-tools.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
    return json.dumps(result, ensure_ascii=False, allow_nan=False)


@tool
async def web_search(query: str, runtime: ToolRuntime[dict | None], count: int = 8) -> str:
    """Search the configured public web index; returned provider/fallback status is explicit."""
    return await _invoke("web_search", runtime, {"query": query, "count": count})


@tool
async def web_fetch(url: str, runtime: ToolRuntime[dict | None]) -> str:
    """Read a public URL. Continue long sources by passing the returned next_url unchanged.

    Read offsets are encoded in next_url, not server query parameters. Cite the
    source URL in the result, which excludes the local read-position fragment.
    """
    return await _invoke("web_fetch", runtime, {"url": url})
