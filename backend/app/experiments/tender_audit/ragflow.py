"""Dataset-bound RAGFlow retrieval with legacy request parameters."""

import json
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from .observability import AuditTrace

VISIBLE_FIELDS_POLICY = "canonical_content_source_metadata_v1"


def visible_chunk(chunk: dict) -> dict:
    """Keep source facts/table lineage; omit duplicate text and search indexes."""
    return {key: value for key, value in chunk.items() if key != "content_with_weight" and not key.endswith(("_ltks", "_tks")) and (key == "content" or value not in (None, "", [], {}))}


class RagQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=4000)


@dataclass(frozen=True)
class RagflowSettings:
    base_url: str
    api_key: str = field(repr=False)
    page_size: int = 15
    timeout: float = 120
    similarity_threshold: float = 0.0
    vector_similarity_weight: float = 0.7
    top_k: int = 1024
    keyword: bool = False
    highlight: bool = False

    @property
    def retrieval_url(self):
        value = self.base_url.rstrip("/")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:
            raise ValueError("RAGFlow URL must be an HTTP(S) server or retrieval URL without credentials/query")
        return value if value.endswith("/api/v1/retrieval") else value + "/api/v1/retrieval"

    def public_dict(self):
        return {key: value for key, value in vars(self).items() if key != "api_key"}


class RagflowClient:
    def __init__(
        self,
        settings: RagflowSettings,
        dataset_id: str,
        trace: AuditTrace,
        *,
        transport=None,
    ):
        if not settings.api_key or not dataset_id.strip():
            raise ValueError("RAG-only requires a credential and one bound dataset")
        self.settings, self.dataset_id, self.trace = settings, dataset_id, trace
        self.transport = transport
        self.chunks: list[dict] = []

    async def search(self, query: str) -> dict:
        payload = {
            "question": query,
            "dataset_ids": [self.dataset_id],
            "page": 1,
            "page_size": self.settings.page_size,
            "similarity_threshold": self.settings.similarity_threshold,
            "vector_similarity_weight": self.settings.vector_similarity_weight,
            "top_k": self.settings.top_k,
            "keyword": self.settings.keyword,
            "highlight": self.settings.highlight,
        }
        started = time.monotonic()
        record = {
            "tool": "local_search_tool",
            "tool_call_id": uuid.uuid4().hex,
            "query": query,
            "url": self.settings.retrieval_url,
            "payload": payload,
        }
        self.trace.event("tool.started", **record)
        raw = None
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout, trust_env=False, transport=self.transport) as client:
                response = await client.post(
                    self.settings.retrieval_url,
                    json=payload,
                    headers={"Authorization": "Bearer " + self.settings.api_key},
                )
            record["http_status"] = response.status_code
            response.raise_for_status()
            raw = response.json()
            if raw.get("code") not in (None, 0):
                raise RuntimeError(f"RAGFlow business error {raw.get('code')}: {raw.get('message') or raw.get('msg')}")
            chunks = []
            for source in (raw.get("data") or {}).get("chunks", []):
                chunk = {
                    **source,
                    "id": str(source.get("id") or source.get("chunk_id") or ""),
                    "content": source.get("content_with_weight") or source.get("content") or "",
                }
                if not chunk["id"] or not isinstance(chunk["content"], str):
                    raise ValueError("RAGFlow returned a chunk without an ID or textual content")
                chunks.append(chunk)
            with self.trace.lock:
                self.chunks.extend(chunks)
            result = {
                "dataset_id": self.dataset_id,
                "chunks": chunks,
                "chunk_count": len(chunks),
            }
            record.update(
                status="completed",
                chunk_count=len(chunks),
                chunk_ids=[item["id"] for item in chunks],
            )
            return result
        except Exception as exc:
            record.update(status="failed", error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            record["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
            self.trace.record_tool(record, raw)

    def tool(self):
        async def retrieve(query: str) -> str:
            result = await self.search(query)
            visible = {
                **result,
                "chunks": [visible_chunk(chunk) for chunk in result["chunks"]],
            }
            text = json.dumps(visible, ensure_ascii=False)
            self.trace.append(
                "tool_visible_results.jsonl",
                {
                    "tool": "local_search_tool",
                    "query": query,
                    "content": text,
                    "externalized": False,
                    "fields_policy": VISIBLE_FIELDS_POLICY,
                    "unprojected_chars": len(json.dumps(result, ensure_ascii=False)),
                    "visible_chars": len(text),
                },
            )
            return text

        return StructuredTool.from_function(
            coroutine=retrieve,
            name="local_search_tool",
            description="检索本次唯一绑定的招标书知识库。参数只有 query。返回真实 chunk ID、原文、位置和相似度；没有外网搜索能力。",
            args_schema=RagQuery,
        )
