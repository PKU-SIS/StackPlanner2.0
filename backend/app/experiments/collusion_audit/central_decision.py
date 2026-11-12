"""Opt-in Central request projection; facts, native policy and state stay intact."""

import httpx
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, SystemMessage

DECISION_GUIDANCE = """
【围串标任务的Central决策表达要求，仅约束本次控制请求】
每轮只决定当前必要的下一步，不在控制轮重新完成整条规则的详细证据分析或预写报告。
先检查<sp-task-context>中的workflow_status、最新受托结果、partial/blocked与真实缺口，以及当前可用产物；计算成功、完整读取、候选数量都不是审核完成或存在风险的证明。
如需补查，明确具体缺口、必要来源及可观察的交付标准。不要复制已有完整规则、统计、长引文或候选表，也不要仅因候选很多或存在未读材料就反复委派。
已有证据能够支持报告，或剩余局限无法通过继续取证合理解决时，可自主决定进入报告阶段，要求如实披露局限；不得把未核写成已核。不可用历史分析、旧报告结论或规则经验。
reason用简短文字交代本次依据和目的，task通常用2至4句，expected_output只列必要验收标准；需要独立并行工作时仍可原生并行委派。
详细HTML排版、模板、写文件与阶段协议会在相应受托角色的完整上下文中提供，不需在控制参数预写报告或重复这些细则。
完整规则、证据正文、来源和已有任务记忆保留，详细分析可交给适合的受托角色；不得为了简短省略独立发现或必要约束。
是否补查、角色、阶段、并行范围、是否报告及是否询问用户，仍按原生SP策略自主决定；不是固定流水线，不修改动作schema、阶段门或JSON处理。
"""


def central_model_overrides():
    return {
        "reasoning_effort": "low",
        "max_tokens": 8192,
        "timeout": httpx.Timeout(connect=10, read=510, write=30, pool=30),
        "stream_chunk_timeout": 510,
    }


def central_reader_guidance(full):
    # Remove only the adapter-owned layout block; keep tool capabilities,
    # evidence handling and coverage instructions verbatim.
    marker = "【本任务实际工具能力，仅用于调度信息，不进入读者报告】"
    if marker not in full:
        return full
    return (
        "【最终交付目标】独立中文HTML报告，使用自然企业名、文档名和真实来源定位，呈现完整规则、检查范围、全部实质发现、双端证据、正常解释及排除、局限和建议；不暴露内部标识。不把取证摘要当最终报告。Reporter会收到完整HTML要求和模板。\n"
        + marker
        + full.split(marker, 1)[1]
    )


def central_skill(skill):
    # Exact prefixes of adapter-owned technical paragraphs only. General rule,
    # warnings, source evidence and user text never pass through this filter.
    prefixes = ("最终报告使用 write_file(", "报告只需一个简明中文", "写入文件后保留原生简短协议：", "原生报告文件名中的<action_id>")
    return "\n".join(line for line in skill.splitlines() if not line.startswith(prefixes))


class CentralDecisionMiddleware(AgentMiddleware):
    def __init__(self, profile):
        self.profile = profile

    def project(self, request):
        original = self.profile.user_input()
        compact = self.profile.user_input(central=True)
        messages = []
        replaced = 0
        for message in request.messages:
            if isinstance(message, HumanMessage) and isinstance(message.content, str) and message.content.count(original) == 1:
                messages.append(message.model_copy(update={"content": message.content.replace(original, compact, 1)}))
                replaced += 1
            else:
                messages.append(message)
        system = request.system_message
        content = system.content if system else ""
        if isinstance(content, str):
            content = content if DECISION_GUIDANCE in content else content + "\n" + DECISION_GUIDANCE
        else:
            content = [*content, {"type": "text", "text": DECISION_GUIDANCE}]
        trace = getattr(self.profile, "trace", None)
        if trace:
            trace.event("collusion.central_projection", replaced_task_messages=replaced, original_task_chars=len(original), central_task_chars=len(compact), full_sources_unchanged=True)
        return request.override(messages=messages, system_message=SystemMessage(content=content))

    def wrap_model_call(self, request, handler):
        return handler(self.project(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self.project(request))


def verify_central_settings(config):
    """Inspect the real SDK payload locally; never makes a provider request."""
    from deerflow.models import create_chat_model

    model = create_chat_model(name=config.models[0].name, thinking_enabled=True, app_config=config, attach_tracing=False, **central_model_overrides())
    payload = model._get_request_payload([HumanMessage("configuration validation only")])
    assert payload["reasoning_effort"] == "low"
    assert payload.get("max_completion_tokens", payload.get("max_tokens")) == 8192
    assert payload["extra_body"] == {"enable_thinking": True}
    assert model.max_retries == model.root_async_client.max_retries == 0
    assert model.request_timeout.read == model.stream_chunk_timeout == 510
    return {
        "reasoning_effort": payload["reasoning_effort"],
        "max_completion_tokens": 8192,
        "thinking": True,
        "sdk_retries": model.max_retries,
        "http_connect_seconds": model.request_timeout.connect,
        "http_read_seconds": model.request_timeout.read,
        "stream_chunk_timeout_seconds": model.stream_chunk_timeout,
    }
