"""Local, thread-safe observable-call tracing; no provider-private reasoning."""

import json
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage

_PRIVATE = {
    "reasoning",
    "reasoning_content",
    "thinking",
    "thinking_content",
    "chain_of_thought",
    "hidden_reasoning",
}
_CREDENTIALS = {
    "api_key",
    "authorization",
    "cookie",
    "password",
    "secret",
    "access_token",
    "refresh_token",
}


class AuditTrace(BaseCallbackHandler):
    """One instance shared by Central and isolated specialist callback managers."""

    run_inline = True

    def __init__(self, directory: Path, *, secrets: tuple[str, ...] = ()):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.secrets = tuple(value for value in secrets if value)
        self.lock = threading.RLock()
        self.pending: dict[str, dict] = {}
        self.finished: set[str] = set()
        self.model_records: list[dict] = []
        self.tool_records: list[dict] = []
        self.last_progress = time.monotonic()

    def safe(self, value: Any) -> Any:
        if isinstance(value, BaseMessage):
            return self.safe(
                {
                    "type": value.type,
                    "content": value.content,
                    "tool_calls": getattr(value, "tool_calls", []),
                }
            )
        if isinstance(value, dict):
            if value.get("type") in {"thinking", "redacted_thinking", "reasoning"}:
                return {"type": "private_content_omitted"}
            return {str(k): "<REDACTED>" if str(k).lower() in _CREDENTIALS else self.safe(v) for k, v in value.items() if str(k).lower() not in _PRIVATE}
        if isinstance(value, (tuple, list)):
            return [self.safe(item) for item in value]
        if value is None or isinstance(value, (int, float, bool)):
            return value
        text = str(value)
        for secret in self.secrets:
            text = text.replace(secret, "<REDACTED>")
        text = re.sub(r"\b(?:sk-|ragflow-)[A-Za-z0-9_-]{8,}", "<REDACTED>", text)
        return re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer <REDACTED>", text)

    def append(self, filename: str, value: dict) -> None:
        with self.lock:
            with (self.directory / filename).open("a", encoding="utf-8") as output:
                output.write(json.dumps(self.safe(value), ensure_ascii=False) + "\n")

    def event(self, event_type: str, **payload) -> None:
        self.last_progress = time.monotonic()
        self.append(
            "events.jsonl",
            {
                "event_type": event_type,
                "occurred_at": datetime.now(UTC).isoformat(),
                **payload,
            },
        )

    @staticmethod
    def caller(tags) -> str:
        for tag in tags or []:
            if tag.startswith("subagent:"):
                return tag.split(":", 1)[1].removeprefix("sp-")
        for tag in tags or []:
            if tag.startswith("middleware:"):
                return tag
        return "central"

    def on_chat_model_start(
        self,
        serialized,
        messages,
        *,
        run_id,
        parent_run_id=None,
        tags=None,
        metadata=None,
        **kwargs,
    ):
        rid = str(run_id)
        with self.lock:
            if rid in self.pending or rid in self.finished:
                return
            self.pending[rid] = {
                "call_id": rid,
                "parent_call_id": str(parent_run_id) if parent_run_id else None,
                "agent": self.caller(tags),
                "inputs": self.safe(messages),
                "started_at": datetime.now(UTC).isoformat(),
                "started_perf": time.monotonic(),
                "first_token_ms": None,
                "model": (metadata or {}).get("ls_model_name"),
                "parameters": self.safe(kwargs.get("invocation_params", {})),
            }
            self.append(
                "model_requests.jsonl",
                {k: v for k, v in self.pending[rid].items() if k != "started_perf"},
            )
        self.event("model.started", call_id=rid, agent=self.caller(tags))

    def on_llm_new_token(self, token, *, run_id, **kwargs):
        self.last_progress = time.monotonic()
        with self.lock:
            record = self.pending.get(str(run_id))
            if record is not None and record["first_token_ms"] is None:
                record["first_token_ms"] = round((time.monotonic() - record["started_perf"]) * 1000, 3)

    def _finish(
        self,
        rid: str,
        *,
        response=None,
        error: str | None = None,
        exception: BaseException | None = None,
        terminal_source: str = "provider_callback",
    ):
        with self.lock:
            if rid in self.finished:
                return
            self.finished.add(rid)
            record = self.pending.pop(
                rid,
                {"call_id": rid, "agent": "unknown", "started_perf": time.monotonic()},
            )
            record["duration_ms"] = round((time.monotonic() - record.pop("started_perf")) * 1000, 3)
            record.update(
                status="failed" if error or exception is not None else "completed",
                error=self.safe(error or (type(exception).__name__ if exception is not None else None)),
                ended_at=datetime.now(UTC).isoformat(),
                terminal_source=terminal_source,
            )
            if exception is not None:
                record["error_type"] = type(exception).__name__
                causes, seen = [], {id(exception)}
                cause = exception.__cause__ or exception.__context__
                while cause is not None and id(cause) not in seen and len(causes) < 8:
                    seen.add(id(cause))
                    causes.append({"type": type(cause).__name__, "message": self.safe(str(cause))})
                    cause = cause.__cause__ or cause.__context__
                record["error_causes"] = causes
            outputs, usage = [], {}
            if response is not None:
                for group in response.generations:
                    for generation in group:
                        message = getattr(generation, "message", None)
                        outputs.append(self.safe(message if message is not None else getattr(generation, "text", "")))
                        if getattr(message, "usage_metadata", None):
                            usage = dict(message.usage_metadata)
                        metadata = getattr(message, "response_metadata", {}) or {}
                        record["model"] = metadata.get("model_name") or metadata.get("model") or record.get("model")
                        record["finish_reason"] = metadata.get("finish_reason")
                if not usage:
                    provider = (response.llm_output or {}).get("token_usage") or (response.llm_output or {}).get("usage") or {}
                    if provider:
                        usage = {
                            "input_tokens": provider.get("prompt_tokens", provider.get("input_tokens")),
                            "output_tokens": provider.get("completion_tokens", provider.get("output_tokens")),
                            "total_tokens": provider.get("total_tokens"),
                        }
            record["outputs"] = outputs
            record["usage"] = usage or None
            self.model_records.append(record)
            self.append("model_calls.jsonl", record)
            self.event(
                "model.finished",
                call_id=rid,
                agent=record["agent"],
                status=record["status"],
            )

    def on_llm_end(self, response, *, run_id, **kwargs):
        self._finish(str(run_id), response=response)

    def on_llm_error(self, error, *, run_id, **kwargs):
        self._finish(str(run_id), error=str(error), exception=error)

    def close_pending(self, reason: str):
        for rid in list(self.pending):
            self._finish(rid, error=reason, terminal_source="run_cleanup")

    def record_tool(self, record: dict, raw: dict | None = None):
        with self.lock:
            self.tool_records.append(record)
            self.append("tool_calls.jsonl", record)
            if raw is not None:
                self.append(
                    "tool_raw_results.jsonl",
                    {"tool_call_id": record["tool_call_id"], "response": raw},
                )
        self.event("tool.finished", **record)

    def metrics(self, duration_seconds: float) -> dict:
        agents, totals = (
            {},
            {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "duration_ms": 0.0,
            },
        )
        complete = bool(self.model_records)
        for record in self.model_records:
            agent = agents.setdefault(
                record["agent"],
                {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "duration_ms": 0.0,
                },
            )
            usage = record.get("usage") or {}
            complete = complete and all(usage.get(key) is not None for key in ("input_tokens", "output_tokens", "total_tokens"))
            for bucket in (agent, totals):
                bucket["calls"] += 1
                bucket["duration_ms"] += record["duration_ms"]
                for key in ("input_tokens", "output_tokens", "total_tokens"):
                    bucket[key] += usage.get(key) or 0
        rag = [r for r in self.tool_records if r.get("tool") == "local_search_tool"]
        count = sum(r.get("chunk_count", 0) for r in rag)
        unique = len({chunk_id for r in rag for chunk_id in r.get("chunk_ids", [])})
        return {
            "duration_seconds": duration_seconds,
            "token_usage_complete": complete,
            "token_source": "provider usage; missing values are unknown (sums are known lower bounds)",
            "call_count_unit": "LangChain model invocations; SDK HTTP retries are not separate calls",
            "agents": agents,
            "llm_totals": totals,
            "tools": {
                "ragflow_calls": len(rag),
                "ragflow_failed_calls": sum(r["status"] != "completed" for r in rag),
                "ragflow_duration_ms": sum(r["duration_ms"] for r in rag),
                "returned_chunks": count,
                "unique_chunks": unique,
                "duplicate_ratio": 1 - unique / count if count else 0,
            },
        }
