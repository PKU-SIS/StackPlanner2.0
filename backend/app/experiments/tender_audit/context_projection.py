"""Compact duplicate delegation text for the model, preserving native task state."""

import json
import re
from datetime import UTC, datetime

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage

CONTEXT_PROJECTION_POLICY = "same_request_delegation_aliases_v1"
_TASK = re.compile(r"<sp-subagent-task>\s*(.*?)\s*</sp-subagent-task>", re.S)
_HELP = "带_ref的字段引用当前JSON中指定路径的完整内容；不需要外部读取。保留任务记忆条目的ID、优先级和状态，按完整用户要求、规则和人工反馈执行。"


def _duplicates(text, full):
    if not isinstance(text, str) or not isinstance(full, str) or not full:
        return False
    text, full = " ".join(text.split()), " ".join(full.split())
    if text == full:
        return True
    if text.endswith("...<truncated>"):
        prefix = text.removesuffix("...<truncated>")
        return len(prefix) >= 120 and full.startswith(prefix)
    return False


def project_delegation_messages(messages, *, action_id):
    before = len(json.dumps([m.model_dump() for m in messages], ensure_ascii=False))
    stats = {"policy": CONTEXT_PROJECTION_POLICY, "applied": False, "before_chars": before, "after_chars": before, "aliases": 0}
    projected = list(messages)
    for index, message in enumerate(messages):
        if not isinstance(message, HumanMessage) or not isinstance(message.content, str):
            continue
        matches = list(_TASK.finditer(message.content))
        if len(matches) != 1:
            continue
        match = matches[0]
        try:
            value = json.loads(match[1])
        except ValueError:
            continue
        if not isinstance(value, dict) or value.get("action_id") != action_id or "audit_context_reference_instructions" in value:
            continue
        context, protocol = value.get("context_refs"), value.get("protocol")
        if not isinstance(context, dict) or not isinstance(protocol, dict):
            continue
        latest = context.get("latest_user_input")
        aliases = 0
        if latest and context.get("original_query") == latest:
            context.pop("original_query")
            context["original_query_ref"] = "context_refs.latest_user_input"
            aliases += 1
        for source, target in [("objective", "task"), ("input_refs", "input_refs")]:
            if source in protocol and target in value and protocol[source] == value[target]:
                protocol.pop(source)
                protocol[source + "_ref"] = target
                aliases += 1
        if value.get("expected_output") and protocol.get("acceptance_criteria") == [value["expected_output"]]:
            protocol.pop("acceptance_criteria")
            protocol["acceptance_criteria_ref"] = "expected_output"
            aliases += 1
        memory = context.get("task_memory") or {}
        for entry in memory.get("entries", []) if isinstance(memory, dict) else []:
            if not isinstance(entry, dict):
                continue
            ref = None
            if entry.get("action") == "user_request" and entry.get("actor") == "human" and _duplicates(entry.get("content"), latest):
                ref = "context_refs.latest_user_input"
            if entry.get("action") == "delegate" and entry.get("actor") == "central" and _duplicates(entry.get("content"), f"Delegate to {value.get('subagent_type')}: {value.get('task', '')}"):
                ref = "task"
            if ref:
                entry.pop("content")
                entry["content_ref"] = ref
                aliases += 1
        if not aliases:
            continue
        value["audit_context_reference_instructions"] = _HELP
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        text = message.content[: match.start(1)] + body + message.content[match.end(1) :]
        if len(text) >= len(message.content):
            continue
        projected[index] = message.model_copy(update={"content": text})
        stats["aliases"] += aliases
    after = len(json.dumps([m.model_dump() for m in projected], ensure_ascii=False))
    if after >= before:
        return messages, stats
    stats.update(applied=True, after_chars=after)
    return projected, stats


class AuditDelegationRequestMiddleware(AgentMiddleware):
    def __init__(self, *, action_id, role, trace):
        super().__init__()
        self.action_id, self.role, self.trace = action_id, role, trace

    def _project(self, request):
        messages, stats = project_delegation_messages(request.messages, action_id=self.action_id)
        self.trace.append(
            "delegation_request_projections.jsonl",
            {"occurred_at": datetime.now(UTC).isoformat(), "action_id": self.action_id, "role": self.role, **stats, "before_messages": [m.model_dump() for m in request.messages], "after_messages": [m.model_dump() for m in messages]},
        )
        return request.override(messages=messages) if stats["applied"] else request

    def wrap_model_call(self, request, handler):
        return handler(self._project(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._project(request))
