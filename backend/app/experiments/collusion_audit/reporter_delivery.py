"""Opt-in Reporter transport: exact references, bounded requests, native control."""

import asyncio
import json
import time
from contextvars import ContextVar

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage

DELIVERY_CONTEXT = ContextVar("collusion_reporter_delivery", default=None)
REF = "$sp_same_request"


def compact_frame(frame):
    seen = {}

    def visit(value, path):
        signature = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(signature) >= 256 and signature in seen:
            return {REF: seen[signature]}
        if isinstance(value, dict):
            result = {k: visit(v, path + "/" + str(k).replace("~", "~0").replace("/", "~1")) for k, v in value.items()}
        elif isinstance(value, list):
            result = [visit(v, path + "/" + str(i)) for i, v in enumerate(value)]
        else:
            result = value
        if len(signature) >= 256:
            seen[signature] = path
        return result

    return visit(frame, "")


def expand_frame(frame):
    def get(path):
        value = frame
        for part in path.split("/")[1:]:
            part = part.replace("~1", "/").replace("~0", "~")
            value = value[int(part)] if isinstance(value, list) else value[part]
        return value

    def visit(value):
        if isinstance(value, dict):
            if set(value) == {REF}:
                return visit(get(value[REF]))
            return {k: visit(v) for k, v in value.items()}
        if isinstance(value, list):
            return [visit(v) for v in value]
        return value

    return visit(frame)


class ReporterDeliveryMiddleware(AgentMiddleware):
    def __init__(self, action_id, trace, request_timeout=900):
        self.action_id, self.trace, self.request_timeout = action_id, trace, request_timeout

    def project(self, request):
        messages = []
        before = after = 0
        for message in request.messages:
            content = message.content
            if isinstance(message, HumanMessage) and isinstance(content, str) and "<sp-subagent-task>" in content:
                head, tail = content.split("<sp-subagent-task>", 1)
                try:
                    stripped = tail.lstrip()
                    frame, end = json.JSONDecoder().raw_decode(stripped)
                    if frame.get("action_id") == self.action_id:
                        compact = compact_frame(frame)
                        if expand_frame(compact) != frame:
                            raise ValueError("Reporter source reference roundtrip failed")
                        suffix = stripped[end:]
                        replacement = head + "<sp-subagent-task>\n" + json.dumps(compact, ensure_ascii=False, separators=(",", ":")) + suffix
                        replacement += "\n同一请求内的" + REF + "为JSON Pointer，引用本条任务中已完整出现的完全相同值；按原值理解，无需额外读取。完整规则、证据、不同判断和局限未删除。"
                        before += len(content)
                        after += len(replacement)
                        message = message.model_copy(update={"content": replacement})
                except json.JSONDecodeError:
                    pass
            messages.append(message)
        if before:
            self.trace.event("reporter.input_projection", action_id=self.action_id, before_chars=before, after_chars=after, lossless_roundtrip=True)
        return request.override(messages=messages)

    async def awrap_model_call(self, request, handler):
        from openai import APIConnectionError, APIStatusError

        request = self.project(request)
        context = {"trace": self.trace, "action_id": self.action_id, "normal_chars": 0}
        token = DELIVERY_CONTEXT.set(context)
        try:
            for attempt in range(2):
                started = time.monotonic()
                pending = set(self.trace.pending)
                self.trace.event("reporter.request.started", action_id=self.action_id, attempt=attempt + 1, timeout_seconds=self.request_timeout)
                try:
                    return await asyncio.wait_for(handler(request), timeout=self.request_timeout)
                except BaseException as exc:
                    for rid in set(self.trace.pending) - pending:
                        self.trace._finish(rid, exception=exc, terminal_source="reporter_request_guard")
                    temporary = isinstance(exc, APIConnectionError) or isinstance(exc, APIStatusError) and (exc.status_code == 429 or exc.status_code >= 500)
                    # Never replay a full timeout, an incomplete stream, or a call
                    # that already produced ordinary text/arguments.
                    retry = temporary and not context["normal_chars"] and time.monotonic() - started < 30 and attempt == 0
                    self.trace.event("reporter.request.error", action_id=self.action_id, error_type=type(exc).__name__, error=str(exc), retry=retry)
                    if not retry:
                        raise
                    await asyncio.sleep(1)
                finally:
                    self.trace.event("reporter.request.ended", action_id=self.action_id, attempt=attempt + 1, elapsed_seconds=time.monotonic() - started)
        finally:
            DELIVERY_CONTEXT.reset(token)
