"""Optional Central-only compact expression; no source or scheduling changes."""

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage


class CompactControlScopeMiddleware(AgentMiddleware):
    def __init__(self, *, enabled=False):
        self.enabled = enabled

    def project(self, request):
        if not self.enabled or any(getattr(m, "name", None) == "audit_control_scope_notice" for m in request.messages):
            return request
        note = HumanMessage(
            content=(
                "[宿主控制表达提示，不是新人工消息] 当前完整规则、代码事实、选定证据正文和报告契约会自动传给受托代理。"
                "控制轮只作必要的阶段决策，避免在控制请求里重新逐条展开已提供的全部候选分析或预写完整报告。需要的详细复核仍由你自主决定是否委派、交给谁。sp_delegate的task通常只需2至4句说明受托范围、必要的真实已有run_id/集合名及输出种类，reason与expected_output简短。"
                "不在控制参数中重复抄写完整规则、统计表、长引文或预计报告正文；这些原数据仍全部保留在真实上下文。"
                "当前任务交付是规则级报告，不要求把工具已完整提供的逐条明细重新转录为另一张阶段表作为报告前置。保留完整来源文件和证据正文；阶段交接只需新增判断、真实索引路径、覆盖和未决问题，不以重复抄表代替复核。"
                "信息不足仍如实处理，不造run_id，不省略必要范围；角色、stage、复核和下一步完全由你按原生策略选择。"
                "本提示不修改工具集合、输出预算或时间限制，不修复JSON。"
            ),
            name="audit_control_scope_notice",
            additional_kwargs={"hide_from_ui": True},
        )
        return request.override(messages=[*request.messages, note])

    def wrap_model_call(self, request, handler):
        return handler(self.project(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self.project(request))
