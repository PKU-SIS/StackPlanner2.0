"""OpenAI-compatible streamed delivery with exact partial output, no repair."""

import asyncio
import json
import re
import time
import uuid
from datetime import UTC, datetime

from langchain_openai import ChatOpenAI
from pydantic import Field

from .reporter_delivery import DELIVERY_CONTEXT


class IncompleteReporterStream(RuntimeError):
    pass


class ReporterOutputIdleTimeout(TimeoutError):
    pass


def content_prefix(arguments):
    """Decode only complete JSON string characters already received for content.

    This is an observer preview, never an executable tool call or final artifact.
    No missing quote, escape, tag, JSON value or byte is inserted.
    """
    match = re.search(r'"content"\s*:\s*"', arguments)
    if not match:
        return ""
    data = arguments[match.end() :]
    out = []
    i = 0
    escapes = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
    while i < len(data):
        char = data[i]
        if char == '"':
            break
        if char != "\\":
            out.append(char)
            i += 1
            continue
        if i + 1 >= len(data):
            break
        char = data[i + 1]
        if char in escapes:
            out.append(escapes[char])
            i += 2
            continue
        if char != "u" or i + 6 > len(data):
            break
        try:
            value = int(data[i + 2 : i + 6], 16)
        except ValueError:
            break
        if 0xD800 <= value <= 0xDBFF:
            if i + 12 > len(data) or data[i + 6 : i + 8] != "\\u":
                break
            try:
                low = int(data[i + 8 : i + 12], 16)
            except ValueError:
                break
            if not 0xDC00 <= low <= 0xDFFF:
                break
            out.append(chr(0x10000 + ((value - 0xD800) << 10) + (low - 0xDC00)))
            i += 12
        elif 0xDC00 <= value <= 0xDFFF:
            break
        else:
            out.append(chr(value))
            i += 6
    return "".join(out)


class StreamRecorder:
    def __init__(self, context):
        self.context = context
        self.trace = context["trace"] if context else None
        self.action_id = context["action_id"] if context else "unscoped"
        self.stream_id = uuid.uuid4().hex
        self.started = time.monotonic()
        self.last_normal = self.started
        self.stats = {"chunks": 0, "normal_chars": 0, "thinking_chars": 0, "finish_reason": None, "usage": None, "provider_request_id": None}
        self.arguments = {}
        self.names = {}
        if self.trace:
            self.base = self.trace.directory / "reporter-streams" / self.action_id / self.stream_id
            self.base.parent.mkdir(parents=True, exist_ok=True)

    def event(self, kind, **data):
        if self.trace:
            self.trace.event("reporter.stream." + kind, stream_id=self.stream_id, action_id=self.action_id, **data)

    def record(self, chunk):
        observed = chunk.message.additional_kwargs.pop("_delivery_observation", {})
        self.stats["chunks"] += 1
        self.stats["thinking_chars"] += observed.get("thinking_chars", 0)
        for key in ["provider_request_id", "usage", "finish_reason"]:
            if observed.get(key) is not None:
                self.stats[key] = observed[key]
        content = chunk.text or ""
        tool_chunks = chunk.message.tool_call_chunks
        normal = len(content) + sum(len(t.get("args") or "") for t in tool_chunks)
        if normal:
            self.last_normal = time.monotonic()
            self.stats["normal_chars"] += normal
            if self.context:
                self.context["normal_chars"] = self.context.get("normal_chars", 0) + normal
        if self.trace:
            row = {"at": datetime.now(UTC).isoformat(), "elapsed_seconds": time.monotonic() - self.started, "content": content, "tool_call_chunks": tool_chunks, **observed}
            self.trace.append(str(self.base.relative_to(self.trace.directory)) + ".delta.jsonl", row)
            if content:
                with self.base.with_suffix(".content.partial.txt").open("a") as f:
                    f.write(self.trace.safe(content))
            for tool in tool_chunks:
                index = str(tool.get("index", 0))
                self.names[index] = tool.get("name") or self.names.get(index)
                self.arguments[index] = self.arguments.get(index, "") + (tool.get("args") or "")
                self.base.with_name(self.base.name + f".tool-{index}.arguments.partial.txt").write_text(self.trace.safe(self.arguments[index]))
                if self.names.get(index) == "write_file":
                    preview = content_prefix(self.arguments[index])
                    if preview:
                        self.base.with_name(self.base.name + f".tool-{index}.html.partial").write_text(self.trace.safe(preview))
            self.base.with_suffix(".status.json").write_text(
                json.dumps(
                    {"stream_id": self.stream_id, "action_id": self.action_id, "status": "streaming", "elapsed_seconds": time.monotonic() - self.started, "normal_idle_seconds": time.monotonic() - self.last_normal, **self.stats},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            )

    def finish(self, status, error=None):
        self.event("ended", status=status, error_type=type(error).__name__ if error else None, error=str(error) if error else None, elapsed_seconds=time.monotonic() - self.started, **self.stats)
        if self.trace:
            self.base.with_suffix(".status.json").write_text(
                json.dumps({"stream_id": self.stream_id, "action_id": self.action_id, "status": status, "error": str(error) if error else None, "elapsed_seconds": time.monotonic() - self.started, **self.stats}, ensure_ascii=False, indent=2)
                + "\n"
            )


class StreamingReporterModel(ChatOpenAI):
    normal_output_timeout: float = Field(default=300, exclude=True, gt=0)

    def _convert_chunk_to_generation_chunk(self, chunk, *args, **kwargs):
        result = super()._convert_chunk_to_generation_chunk(chunk, *args, **kwargs)
        if result is not None:
            choices = chunk.get("choices") or []
            delta = choices[0].get("delta") or {} if choices else {}
            result.message.additional_kwargs["_delivery_observation"] = {
                "thinking_chars": len(delta.get("reasoning_content") or delta.get("reasoning") or ""),
                "provider_request_id": chunk.get("id"),
                "usage": chunk.get("usage"),
                "finish_reason": choices[0].get("finish_reason") if choices else None,
            }
        return result

    async def _astream(self, *args, **kwargs):
        recorder = StreamRecorder(DELIVERY_CONTEXT.get())
        payload = self._get_request_payload(
            args[0] if args else kwargs["messages"], stream=True, stream_options={"include_usage": True}, **{k: v for k, v in kwargs.items() if k not in {"messages", "run_manager", "stream", "stream_options"}}
        )
        await asyncio.to_thread(
            recorder.event,
            "started",
            parameters={k: v for k, v in payload.items() if k not in {"messages", "tools"}},
            tools=[t.get("function", {}).get("name") for t in payload.get("tools", [])],
            normal_output_timeout_seconds=self.normal_output_timeout,
        )
        iterator = super()._astream(*args, **kwargs).__aiter__()
        error = None
        try:
            while True:
                remaining = self.normal_output_timeout - (time.monotonic() - recorder.last_normal)
                if remaining <= 0:
                    raise ReporterOutputIdleTimeout(f"No ordinary text or tool argument progress for {self.normal_output_timeout:g}s")
                try:
                    chunk = await asyncio.wait_for(anext(iterator), timeout=remaining)
                except StopAsyncIteration:
                    break
                except TimeoutError as exc:
                    raise ReporterOutputIdleTimeout(f"No ordinary text or tool argument progress for {self.normal_output_timeout:g}s") from exc
                await asyncio.to_thread(recorder.record, chunk)
                yield chunk
            if not recorder.stats["normal_chars"] or recorder.stats["finish_reason"] not in {"stop", "tool_calls", "function_call", "length", "content_filter"}:
                raise IncompleteReporterStream("Stream ended without usable ordinary output and/or a recorded finish_reason; partial output retained, not repaired")
        except BaseException as exc:
            error = exc
            raise
        finally:
            try:
                await iterator.aclose()
            finally:
                await asyncio.to_thread(recorder.finish, "failed" if error else "completed", error)
