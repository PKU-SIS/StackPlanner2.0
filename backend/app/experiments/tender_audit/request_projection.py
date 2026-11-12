"""Lossless evidence sharing inside one model request; never rewrite graph state."""

import hashlib
import json
from datetime import UTC, datetime

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

REQUEST_PROJECTION_POLICY = "same_request_lossless_sources_v2"
REFERENCE_HELP = (
    "本请求各检索结果的chunks是来源索引字符串，或{ref,set,drop}。全文见当前这条结果的audit_shared_sources；"
    "公共文档字段见audit_shared_source_metadata。还原命中：合并公共字段与对应来源，再删除drop字段并覆盖set字段（省略即为空）。"
    "所有原文、真实id、位置和表格信息均完整保留；最终证据使用来源真实id，不使用s开头的内部索引。无需额外读取。"
)


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def project_evidence_messages(messages, *, dataset_id: str):
    """Share identical full sources, retaining per-occurrence metadata deltas.

    Unsupported/malformed search results leave the complete request unchanged.
    Other message types and tool-call/result order are never modified.
    """
    before = len(_dump([message.model_dump() for message in messages]))
    stats = {"policy": REQUEST_PROJECTION_POLICY, "before_chars": before, "after_chars": before, "source_occurrences": 0, "unique_sources": 0, "applied": False}
    results = []
    for index, message in enumerate(messages):
        if not isinstance(message, ToolMessage) or message.name != "local_search_tool":
            continue
        try:
            value = json.loads(message.content)
        except (ValueError, TypeError):
            return messages, stats
        if not isinstance(value, dict) or "audit_shared_sources" in value or value.get("dataset_id") != dataset_id or not isinstance(value.get("chunks"), list):
            return messages, stats
        if any(not isinstance(c, dict) or not isinstance(c.get("content"), str) or not c.get("id") for c in value["chunks"]):
            return messages, stats
        results.append((index, message, value))
    if not results:
        return messages, stats
    sources, identities = {}, {}
    projected = list(messages)
    for index, message, value in results:
        references = []
        for chunk in value["chunks"]:
            identity = (str(chunk["id"]), str(chunk.get("document_id") or chunk.get("doc_id") or ""), hashlib.sha256(chunk["content"].encode()).hexdigest())
            if identity not in identities:
                identities[identity] = f"s{len(sources) + 1:04d}"
                sources[identities[identity]] = chunk
            ref = identities[identity]
            canonical = sources[ref]
            overrides = {k: v for k, v in chunk.items() if k not in canonical or _dump(canonical[k]) != _dump(v)}
            removed = [k for k in canonical if k not in chunk]
            hit = {"ref": ref}
            if overrides:
                hit["set"] = overrides
            if removed:
                hit["drop"] = removed
            references.append(hit if overrides or removed else ref)
        projected[index] = message.model_copy(update={"content": _dump({**value, "chunks": references})})
        stats["source_occurrences"] += len(references)
    last = results[-1][0]
    last_value = json.loads(projected[last].content)
    first = next(iter(sources.values()), {})
    common = {key: first[key] for key in ("dataset_id", "document_id", "document_keyword", "doc_id") if key in first and all(key in c and _dump(c[key]) == _dump(first[key]) for c in sources.values())}
    factored = {ref: {key: value for key, value in chunk.items() if key not in common} for ref, chunk in sources.items()}
    projected[last] = projected[last].model_copy(update={"content": _dump({**last_value, "audit_shared_source_metadata": common, "audit_shared_sources": factored, "audit_source_reference_instructions": REFERENCE_HELP})})
    stats["unique_sources"] = len(sources)
    after = len(_dump([message.model_dump() for message in projected]))
    if after >= before:
        return messages, stats
    stats.update(after_chars=after, applied=True)
    return projected, stats


class AuditEvidenceRequestMiddleware(AgentMiddleware):
    """An experiment-only final request projection, after native middleware."""

    def __init__(self, *, dataset_id, trace):
        super().__init__()
        self.dataset_id, self.trace = dataset_id, trace

    def _project(self, request):
        messages, stats = project_evidence_messages(request.messages, dataset_id=self.dataset_id)
        self.trace.append(
            "model_request_projections.jsonl",
            {"occurred_at": datetime.now(UTC).isoformat(), **stats, "before_messages": [m.model_dump() for m in request.messages], "after_messages": [m.model_dump() for m in messages]},
        )
        return request.override(messages=messages) if stats["applied"] else request

    def wrap_model_call(self, request, handler):
        return handler(self._project(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._project(request))
