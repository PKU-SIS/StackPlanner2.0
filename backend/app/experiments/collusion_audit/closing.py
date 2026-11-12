"""Opt-in per-delegation budget closing, before the native20-tool hard cap."""

import json
from collections.abc import Mapping

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, ToolMessage


class BudgetClosingMiddleware(AgentMiddleware):
    def __init__(self, trace=None, reserve_at=16, compact_results=False, report_path=None, close_repeated_reads=False, enforce_read_limits=True):
        self.enforce_read_limits = enforce_read_limits
        self.close_repeated_reads = close_repeated_reads
        self.report_path = report_path
        self._report_created = False
        self.trace = trace
        self.reserve_at = reserve_at
        self.compact_results = compact_results
        self._seen_tool_call_ids = set()

    def project(self, request):
        if self.report_path:
            state = getattr(request, "state", None)
            full_messages = state.get("messages", []) if isinstance(state, Mapping) else []
            for message in [*request.messages, *full_messages]:
                if not isinstance(message, ToolMessage) or message.name != "write_file" or getattr(message, "status", None) == "error" or not isinstance(message.content, str):
                    continue
                try:
                    receipt = json.loads(message.content)
                except (ValueError, TypeError):
                    continue
                if isinstance(receipt, dict) and receipt.get("created") == self.report_path and receipt.get("status") != "error":
                    self._report_created = True
            if self._report_created:
                if self.trace:
                    self.trace.event("collusion.report_write_closing", report_path=self.report_path, no_new_tool_calls=True, json_repair=False)
                note = HumanMessage(
                    content=(
                        "[本次报告文件已实际创建] 本次委派进入原生协议收尾，不再重复写入或重新读取刚创建的同一文件。"
                        "直接返回真实原生阶段结果和created_paths；若发现已知实质缺陷，标为partial并如实列出，后续是否修订及角色仍由Central决定。"
                        "该提示不验证报告内容、不自动标记通过、不修复JSON。"
                    ),
                    name="audit_report_created_notice",
                )
                return request.override(messages=[*request.messages, note], tools=[])
        if not self.enforce_read_limits:
            return request
        if self.close_repeated_reads:
            actual = [m for m in request.messages if isinstance(m, ToolMessage)]
            repeated = []
            readonly = {"read_file", "audit_collection_overview", "audit_read_result", "audit_read_complete", "project_read", "project_search", "knowledge_read"}
            for message in actual[-4:]:
                if message.name not in readonly or getattr(message, "status", None) == "error" or not isinstance(message.content, str):
                    continue
                try:
                    receipt = json.loads(message.content)
                except (ValueError, TypeError):
                    continue
                if isinstance(receipt, dict) and receipt.get("status") == "already_returned" and isinstance(receipt.get("result_ref"), str) and receipt["result_ref"]:
                    repeated.append((message.tool_call_id, receipt["result_ref"]))
            if len(actual) >= 6 and len(repeated) >= 3 and len({item[0] for item in repeated}) >= 3 and len({item[1] for item in repeated}) == 1 and actual[-1].tool_call_id == repeated[-1][0]:
                if self.trace:
                    self.trace.event("collusion.repeated_read_closing", result_ref=repeated[-1][1], repeated_receipts=len(repeated), no_new_tool_calls=True, json_repair=False)
                note = HumanMessage(
                    content=(
                        "[当前委派重复读取收尾] 最近4个真实工具回执中至少3次重复获得同一result_ref，且无新增内容；当前委派不再重复读取。"
                        "直接返回真实原生阶段协议，保留已取证事实和未解决范围；尚未完成的部分如实标为partial。"
                        "此提示不认定审核完成、不判风险、不修复JSON；后续是否继续补查、选择谁及何时生成报告仍由Central决定。"
                    ),
                    name="audit_repeated_read_closing",
                )
                return request.override(messages=[*request.messages, note], tools=[])
        count = sum(isinstance(m, ToolMessage) for m in request.messages)
        projected_count = count
        if self.compact_results:
            state = getattr(request, "state", None)
            full_messages = state.get("messages", []) if isinstance(state, Mapping) else []
            for message in [*request.messages, *full_messages]:
                if isinstance(message, ToolMessage):
                    self._seen_tool_call_ids.add(message.tool_call_id or message.id or str(id(message)))
            count = len(self._seen_tool_call_ids)
        text_volume = sum(len(str(m.content)) for m in request.messages if isinstance(m, ToolMessage))
        volume_closing = self.compact_results and count >= 4 and text_volume >= 200000
        if count < self.reserve_at and not volume_closing:
            if self.compact_results and count and text_volume >= 100000:
                note = HumanMessage(
                    content="[大结果已持久化] 完整数据无需转抄。需要字段时用facts-index；本次阶段终态目标约1200输出tokens，简短summary+artifact_content+原生metadata，未完成范围如实partial。具体下一动作由你自行判断。",
                    name="audit_compact_notice",
                    additional_kwargs={"hide_from_ui": True},
                )
                return request.override(messages=[*request.messages, note])
            return request
        if self.trace:
            self.trace.event(
                "collusion.budget_closing", tool_results=count, projected_tool_results=projected_count, count_basis="cumulative_call_ids" if self.compact_results else "request_messages", no_new_tool_calls=True, json_repair=False
            )
        note = HumanMessage(
            content=(
                "[本次委派预算收尾] 当前工具预算已进入预留收尾区。本次正常回复不再调用工具，直接返回真实原生阶段协议。"
                + (
                    "仅简要交付实际完成的关键事实、复核意见及未解决条件，不转抄长集合；未完成当前委派范围时标为partial并列出缺口。"
                    if self.compact_results
                    else "只交付已实际完成的判断记录；未完成范围标为partial并列出缺口。review_records可放artifact_metadata，由宿主持久化校验。"
                )
                + "不能仅说将要返回结果，不能声称未完成审核已完成。这不是JSON修复；若协议仍不合法则如实失败。后续角色和步骤仍由Central自行决定。"
            ),
            name="audit_budget_notice",
        )
        return request.override(messages=[*request.messages, note], tools=[])

    def wrap_model_call(self, request, handler):
        return handler(self.project(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self.project(request))
