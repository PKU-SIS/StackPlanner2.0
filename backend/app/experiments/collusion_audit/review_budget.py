"""Opt-in context deduplication and normal handoff before native budget caps."""

import hashlib

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, ToolMessage


class ReviewBudgetMiddleware(AgentMiddleware):
    def __init__(self, trace=None, token_budget=2_000_000, reserve_tokens=200_000, expected_grade=None):
        self.expected_grade = expected_grade
        self.trace = trace
        self.token_budget = token_budget
        self.reserve_tokens = reserve_tokens
        self.spent_tokens = 0
        self.last_input_tokens = 0

    def project(self, request):
        messages = []
        seen = {}
        removed = 0
        for message in request.messages:
            if isinstance(message, ToolMessage) and isinstance(message.content, str) and len(message.content) > 1000 and getattr(message, "status", None) != "error":
                key = (message.name, hashlib.sha256(message.content.encode()).hexdigest())
                if key in seen:
                    message = message.model_copy(update={"content": "本次上下文已完整包含相同工具结果，请复用较早的工具回执 " + str(seen[key]) + "；原始完整结果仍保留在工具轨迹中。"})
                    removed += 1
                else:
                    seen[key] = message.tool_call_id
            messages.append(message)
        closing = self.spent_tokens + self.last_input_tokens + self.reserve_tokens >= self.token_budget
        if closing:
            messages.append(
                HumanMessage(
                    content="【本轮委派预留收尾】实际累计用量接近原生预算，当前回复不再调用工具。按原生阶段协议简要交接真实新增事实、判断修正和未完成范围；未完成标partial，不宣称审核完成。后续是否补查或生成披露局限的报告仍由Central自主决定。只返回一个原生阶段JSON对象，以{开始、以}结束，不输出代码围栏、工具调用、完整索引长表或“现在返回”的占位句。",
                    name="audit_review_budget_notice",
                )
            )
        force_none = closing or not request.tools
        if self.trace and (removed or closing or force_none):
            self.trace.event(
                "collusion.review_budget",
                duplicate_bodies_removed=removed,
                spent_tokens=self.spent_tokens,
                next_input_estimate=self.last_input_tokens,
                reserve_tokens=self.reserve_tokens,
                normal_handoff_requested=closing,
                provider_tool_choice_none=force_none,
            )
        overrides = {"tools": []} if closing else {}
        if force_none and self.expected_grade:
            messages.append(
                HumanMessage(
                    content="【报告写入后的原生阶段协议】这次回复的artifact_metadata还须显式包含evidence_level字段，其逐字原值为："
                    + self.expected_grade
                    + "。保留实际created_paths、completion_status和evidence_gaps；不要把静态等级当作风险意见，不输出HTML正文或占位句。",
                    name="audit_report_grade_closing",
                )
            )
        if force_none:
            overrides["model_settings"] = {**(getattr(request, "model_settings", None) or {}), "tool_choice": "none"}
        return request.override(messages=messages, **overrides)

    def observe(self, response):
        for message in getattr(response, "result", []) or []:
            usage = getattr(message, "usage_metadata", None) or {}
            self.spent_tokens += usage.get("total_tokens", 0) or 0
            self.last_input_tokens = usage.get("input_tokens", 0) or self.last_input_tokens
        return response

    def wrap_model_call(self, request, handler):
        return self.observe(handler(self.project(request)))

    async def awrap_model_call(self, request, handler):
        return self.observe(await handler(self.project(request)))
