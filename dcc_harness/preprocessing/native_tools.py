"""Thin native SP retrieval tool; each call uses the thread's staged scope."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from langchain.tools import ToolRuntime, tool

from .ragflow import RagflowClient, retrieve


def _retrieve_for_thread(thread_id: str, question: str, page: int, page_size: int) -> str:
    from deerflow.config.paths import get_paths
    from deerflow.runtime.user_context import get_effective_user_id

    uploads = get_paths().sandbox_uploads_dir(thread_id, user_id=get_effective_user_id())
    context = json.loads((uploads / "dcc-context.json").read_text())
    if context.get("document_mode") == "inline":
        raise ValueError("inline evidence is already supplied in full and does not use RAGFlow")
    config = json.loads(Path(os.environ["DCC_CONFIG"]).read_text())["ragflow"]
    # Resolve a relative private key path against the configuration, consistently
    # with the CLI. Prompt files never contain the key or its filesystem path.
    if config.get("api_key_file"):
        config["api_key_file"] = str((Path(os.environ["DCC_CONFIG"]).resolve().parent / config["api_key_file"]).resolve())
    client = RagflowClient(config)
    try:
        result = retrieve(client, context["retrieval"], question, page=page, page_size=page_size, options=config.get("retrieval_options", {}))
    finally:
        client.session.close()
    chunks = [
        {key: chunk[key] for key in ("id", "document_id", "doc_id", "document_name", "docnm_kwd", "content", "similarity", "positions") if key in chunk}
        for chunk in result["chunks"]
    ]
    return json.dumps({"total": result.get("total", len(chunks)), "page": page, "chunks": chunks}, ensure_ascii=False)


@tool
async def ragflow_retrieve(question: str, runtime: ToolRuntime[dict | None], page: int = 1, page_size: int = 10) -> str:
    """Search the current DCC sample's indexed documents via RAGFlow; return cited evidence chunks. Read the sample's rule files separately with native file tools."""
    context = runtime.context or {}
    thread_id = context.get("thread_id") or runtime.config.get("configurable", {}).get("thread_id")
    if not thread_id:
        raise ValueError("DCC retrieval requires a native thread_id")
    return await asyncio.to_thread(_retrieve_for_thread, str(thread_id), question, page, page_size)
