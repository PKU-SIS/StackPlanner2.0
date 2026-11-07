"""Thin native SP retrieval tool; each call uses the thread's staged scope."""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

from langchain.tools import ToolRuntime, tool

from .ragflow import RagflowClient, retrieve


def _render_retrieval_output(output: dict) -> str:
    coverage = output.get("source_coverage")
    if isinstance(coverage, dict) and coverage.get("mode") == "document":
        total, page, page_size = output["total"], output["page"], coverage["page_size"]
        total_pages = (total + page_size - 1) // page_size
        has_more = page < total_pages
        # These are service pagination facts, not an audit/visibility verdict.
        # Keep the original chunks and coverage intact, after the small header.
        output = {
            **{
                key: value
                for key, value in output.items()
                if key not in {"pagination", "chunks", "source_coverage"}
            },
            "pagination": {
                "total_chunks": total,
                "returned_chunks": len(output["chunks"]),
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "has_more": has_more,
                "next_page": page + 1 if has_more else None,
                "end_of_indexed_document": not has_more,
                "out_of_range": page > total_pages,
            },
            "chunks": output["chunks"],
            "source_coverage": coverage,
        }
    return json.dumps(output, ensure_ascii=False, indent=2)


def _retrieve_for_thread(
    thread_id: str,
    question: str,
    page: int,
    page_size: int,
    *,
    user_id: str,
    document_id: str | None = None,
) -> str:
    from deerflow.config.paths import get_paths

    uploads = get_paths().sandbox_uploads_dir(thread_id, user_id=user_id)
    context = json.loads((uploads / "dcc-context.json").read_text())
    if context.get("document_mode") == "inline":
        raise ValueError(
            "inline evidence is already supplied in full and does not use RAGFlow"
        )
    scope = context["retrieval"]
    if page < 1 or not 1 <= page_size <= 100:
        raise ValueError("page must be positive and page_size must be 1..100")
    cache = None
    if document_id is not None:
        if document_id not in scope["document_ids"] or len(scope["dataset_ids"]) != 1:
            raise ValueError("document_id is outside the current sample scope")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", document_id):
            raise ValueError("unsupported document identifier in current scope")
        # Host-only thread cache, outside the sandbox's user-data mount. A
        # specialist must not be able to author its own source attestation.
        cache = (
            uploads.parent.parent
            / ".dcc-document-cache"
            / f"{document_id}-{page}-{page_size}.json"
        )
        if cache.is_file():
            return _render_retrieval_output(json.loads(cache.read_text()))
    config = json.loads(Path(os.environ["DCC_CONFIG"]).read_text())["ragflow"]
    # Resolve a relative private key path against the configuration, consistently
    # with the CLI. Prompt files never contain the key or its filesystem path.
    if config.get("api_key_file"):
        config["api_key_file"] = str(
            (
                Path(os.environ["DCC_CONFIG"]).resolve().parent / config["api_key_file"]
            ).resolve()
        )
    client = RagflowClient(config)
    try:
        result = (
            client.document_chunks(
                scope["dataset_ids"][0], document_id, page, page_size
            )
            if document_id is not None
            else retrieve(
                client,
                context["retrieval"],
                question,
                page=page,
                page_size=page_size,
                options=config.get("retrieval_options", {}),
            )
        )
    finally:
        client.session.close()
    chunks = [
        {
            key: chunk[key]
            for key in (
                "id",
                "document_id",
                "doc_id",
                "document_name",
                "docnm_kwd",
                "content",
                "similarity",
                "positions",
            )
            if key in chunk
        }
        for chunk in result["chunks"]
    ]
    for source, chunk in zip(result["chunks"], chunks):
        for key in ("document_name", "docnm_kwd", "document_keyword"):
            name = source.get(key)
            if isinstance(name, str) and name.strip():
                chunk["document_name"] = name
                break
    output = {"total": result.get("total", len(chunks)), "page": page, "chunks": chunks}
    if document_id is not None:
        doc = result.get("doc") or {}
        output["source_coverage"] = {
            "mode": "document",
            "document_id": document_id,
            "page": page,
            "page_size": page_size,
            "total": output["total"],
            "returned": len(chunks),
            "chunk_ids": [chunk.get("id") for chunk in chunks],
            "document_bytes": doc.get("size"),
            "snapshot_update_time": doc.get("update_time"),
        }
    rendered = _render_retrieval_output(output)
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(rendered)
        temporary.replace(cache)
    return rendered


@tool
async def ragflow_retrieve(
    question: str,
    runtime: ToolRuntime[dict | None],
    page: int = 1,
    page_size: int = 10,
    document_id: str | None = None,
) -> str:
    """Search only the current sample's indexed documents and return cited chunks.

    The JSON result has total matching chunks, the current page, and chunks for
    that page. page is 1-indexed; page_size is 1-100 (default 10). When total is
    larger than the returned chunk count, inspect later pages with the same
    query and page_size as needed for the task's evidence coverage. One page
    does not guarantee whole-document coverage; incomplete retrieval cannot prove a fact is
    absent from a document. Read rule files separately with read_file. Every
    page keeps the current sample's fixed document scope.

    For a complete source audit, supply document_id from dcc-context.json and
    page_size=100: this enumerates that document's original indexed chunks,
    without query filtering. Read all pages and all content; question is only
    a description in this mode. It caches the first snapshot per thread/page,
    retaining source update time. An externalized preview is not a full read.
    In document mode, pagination precedes the original chunk content and reports
    total_chunks, returned_chunks, total_pages, has_more, next_page,
    end_of_indexed_document, and out_of_range. total is a chunk count, not a
    page count; page_size is a requested limit, not the actual returned count.
    An empty out_of_range page is beyond the indexed document, not a missing
    source page. Follow next_page only when has_more. These transport facts do
    not certify that earlier pages were read or the substantive audit completed.
    """
    from deerflow.runtime.user_context import resolve_runtime_user_id

    context = runtime.context or {}
    thread_id = context.get("thread_id") or runtime.config.get("configurable", {}).get(
        "thread_id"
    )
    if not thread_id:
        raise ValueError("DCC retrieval requires a native thread_id")
    kwargs = {"user_id": resolve_runtime_user_id(runtime)}
    if document_id is not None:
        kwargs["document_id"] = document_id
    return await asyncio.to_thread(
        _retrieve_for_thread,
        str(thread_id),
        question,
        page,
        page_size,
        **kwargs,
    )
