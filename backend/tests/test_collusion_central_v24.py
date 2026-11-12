"""Central owns recovery; cloud effort and concise inputs remain role-scoped."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from test_collusion_audit import scoped_profile as scoped_profile

from deerflow.agents.middlewares.llm_error_handling_middleware import LLMErrorHandlingMiddleware
from deerflow.sp.central_resilience import SPCentralResilienceMiddleware


def request():
    return ModelRequest(
        model=SimpleNamespace(),
        messages=[HumanMessage("task")],
        state={"sp_current_stage": "implementation", "sp_current_action": None, "sp_active_delegate_id": None, "sp_loop_run_id": "run"},
        runtime=SimpleNamespace(context={"run_id": "run"}),
        model_settings={"max_tokens": 8192},
    )


def outer():
    from deerflow.config import get_app_config

    result = LLMErrorHandlingMiddleware(app_config=get_app_config(), propagate_model_errors=True)
    result.request_timeout_seconds = 0.002
    return result


def test_outer_deadline_cannot_cancel_owned_central_call():
    error = outer()
    central = SPCentralResilienceMiddleware(initial_timeout_seconds=0.1, retry_timeout_seconds=0.1, manage_transport_errors=True)
    calls = []

    async def handler(req):
        calls.append(req)
        await asyncio.sleep(0.01)
        return ModelResponse(result=[AIMessage(content="continue")])

    async def run():
        return await error.awrap_model_call(request(), lambda req: central.awrap_model_call(req, handler))

    assert asyncio.run(run()).result[0].content == "continue"
    assert len(calls) == 1


def test_transport_failure_retries_once_without_generic_retry():
    error = outer()
    central = SPCentralResilienceMiddleware(manage_transport_errors=True)
    calls = []

    async def handler(req):
        calls.append(req)
        if len(calls) == 1:
            raise httpx.ReadTimeout("synthetic")
        return ModelResponse(result=[AIMessage(content="continue")])

    async def run():
        return await error.awrap_model_call(request(), lambda req: central.awrap_model_call(req, handler))

    assert asyncio.run(run()).result[0].content == "continue"
    assert len(calls) == 2


def test_failure_checkpoint_then_explicit_error_has_no_outer_retry():
    error = outer()
    central = SPCentralResilienceMiddleware(initial_timeout_seconds=0.002, retry_timeout_seconds=0.002, manage_transport_errors=True)
    calls = []

    async def handler(req):
        calls.append(req)
        await asyncio.sleep(1)

    async def run():
        result = await error.awrap_model_call(request(), lambda req: central.awrap_model_call(req, handler))
        assert result.result[0].tool_calls[0]["name"] == "sp_think"
        with pytest.raises(TimeoutError):
            await error.awrap_model_call(request(), lambda req: central.awrap_model_call(req, handler))

    asyncio.run(run())
    assert len(calls) == 4


def test_cancellation_and_fatal_errors_are_not_retried():
    error = outer()
    central = SPCentralResilienceMiddleware(manage_transport_errors=True)
    for exc in (asyncio.CancelledError(), ValueError("bad parameter")):
        calls = []

        async def handler(req):
            calls.append(req)
            raise exc

        async def run():
            with pytest.raises(type(exc)):
                await error.awrap_model_call(request(), lambda req: central.awrap_model_call(req, handler))

        asyncio.run(run())
        assert len(calls) == 1


def test_explicit_low_effort_reaches_cloud_payload_only_for_central(monkeypatch):
    from app.experiments.collusion_audit.central_decision import central_model_overrides
    from app.experiments.tender_audit.runner import experiment_config
    from deerflow.models import create_chat_model

    monkeypatch.setenv("DASHSCOPE_API_KEY", "unit-test-only")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
    cfg = experiment_config("aliyun-qwen3.8-27b")
    central = create_chat_model(name=cfg.models[0].name, thinking_enabled=True, app_config=cfg, attach_tracing=False, **central_model_overrides())
    payload = central._get_request_payload([HumanMessage("probe")])
    assert payload["reasoning_effort"] == "low"
    assert payload["extra_body"] == {"enable_thinking": True}
    assert payload["max_completion_tokens"] == 8192
    assert central.stream_chunk_timeout == 510
    assert central.request_timeout.read == 510 and central.request_timeout.connect == 10
    assert central.root_async_client.max_retries == 0
    for thinking in (False, True):
        child = create_chat_model(name=cfg.models[0].name, thinking_enabled=thinking, app_config=cfg, attach_tracing=False)
        p = child._get_request_payload([HumanMessage("probe")])
        assert p.get("reasoning_effort") is None
        assert p["extra_body"]["enable_thinking"] is thinking
        assert child.request_timeout == cfg.models[0].model_extra["timeout"]


def test_central_projection_preserves_raw_messages_and_different_user_turns():
    from app.experiments.collusion_audit.central_decision import CentralDecisionMiddleware

    original = "完整规则：判断标准。报告排版细则。原文证据甲乙。"
    compact = "完整规则：判断标准。中文HTML报告。原文证据甲乙。"
    profile = SimpleNamespace(user_input=lambda **kw: compact if kw.get("central") else original)
    middleware = CentralDecisionMiddleware(profile)
    message = HumanMessage("prefix\n" + original + "\nsuffix")
    req = request().override(messages=[message], system_message=SystemMessage("native policy"))
    projected = middleware.project(req)
    assert projected.messages[0].content == "prefix\n" + compact + "\nsuffix"
    assert req.messages[0] is message and message.content == "prefix\n" + original + "\nsuffix"
    assert projected.system_message.content.startswith("native policy")
    other = req.override(messages=[HumanMessage("real user correction")])
    assert middleware.project(other).messages[0].content == "real user correction"
    assert projected.tools == req.tools


def test_real_profile_keeps_complete_rules_sources_and_full_reporter_input(scoped_profile):
    import json

    from app.experiments.collusion_audit.profile import CollusionProfile

    p = CollusionProfile(
        scoped_profile.rule,
        scoped_profile.project,
        scoped_profile.outputs,
        scoped_profile.outputs / "cache-v24",
        scoped_profile.trace,
        quality_controls=True,
        rule_level_review=True,
        report_format="html",
        resilient_delivery=True,
        central_efficiency=True,
    )
    full = p.user_input()
    compact = p.user_input(central=True)
    assert len(compact) < len(full)
    assert json.dumps(p.rule, ensure_ascii=False, indent=2) in compact
    assert json.dumps(p.manifest, ensure_ascii=False, indent=2) in compact
    assert "约16条工具结果" not in compact
    assert p.user_input() == full
    ext = p.extensions()
    assert ext.central_recovery_owned is True and ext.central_model_overrides["reasoning_effort"] == "low"
    assert ext.configure_subagent == p.configure_subagent
    from deerflow.sp.extensions import SPExecutionExtensions

    assert SPExecutionExtensions().central_recovery_owned is False
    assert SPExecutionExtensions().central_model_overrides is None


def test_real_base_chain_switch_changes_only_lead_error_owner():
    from deerflow.agents.middlewares.tool_error_handling_middleware import build_lead_runtime_middlewares, build_subagent_runtime_middlewares
    from deerflow.config import get_app_config

    cfg = get_app_config()
    lead = build_lead_runtime_middlewares(app_config=cfg, propagate_model_errors=True)
    child = build_subagent_runtime_middlewares(app_config=cfg)
    normal = build_lead_runtime_middlewares(app_config=cfg)

    def error(chain):
        return next(m for m in chain if isinstance(m, LLMErrorHandlingMiddleware))

    assert error(lead).propagate_model_errors is True
    assert error(child).propagate_model_errors is False
    assert error(normal).propagate_model_errors is False


@pytest.mark.parametrize("status,expected", [(400, False), (401, False), (429, True), (500, True), (503, True)])
def test_only_temporary_http_statuses_are_recoverable(status, expected):
    response = httpx.Response(status, request=httpx.Request("POST", "https://example.invalid"))
    exc = httpx.HTTPStatusError("synthetic", request=response.request, response=response)
    assert SPCentralResilienceMiddleware(manage_transport_errors=True)._recoverable(exc) is expected
    assert SPCentralResilienceMiddleware()._recoverable(exc) is False


def test_actual_sp_factory_installs_owned_recovery_and_low_effort(monkeypatch):
    from app.experiments.collusion_audit.central_decision import central_model_overrides
    from app.experiments.tender_audit.runner import experiment_config
    from deerflow.agents.lead_agent import agent as lead
    from deerflow.sp.extensions import SPExecutionExtensions
    from deerflow.sp.runtime import make_sp_agent

    monkeypatch.setenv("DASHSCOPE_API_KEY", "unit-test-only")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
    cfg = experiment_config("aliyun-qwen3.8-27b")
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(metadata={})

    monkeypatch.setattr(lead, "create_agent", create)
    monkeypatch.setattr("deerflow.sp.runtime.build_sp_central_runtime_context", lambda *a, **kw: SimpleNamespace(agent_model=None, agent_name=None, available_skill_names=frozenset(), system_prompt_section=""))
    make_sp_agent(
        {"configurable": {"model_name": cfg.models[0].name, "user_id": "default", "sp_central_timeout_seconds": 180, "sp_central_retry_timeout_seconds": 480, "sp_central_retry_max_tokens": 8192}},
        app_config=cfg,
        extensions=SPExecutionExtensions(central_recovery_owned=True, central_model_overrides=central_model_overrides()),
    )
    assert captured["model"]._get_request_payload([HumanMessage("probe")])["reasoning_effort"] == "low"
    middlewares = captured["middleware"]
    error = next(m for m in middlewares if isinstance(m, LLMErrorHandlingMiddleware))
    recovery = next(m for m in middlewares if isinstance(m, SPCentralResilienceMiddleware))
    assert error.propagate_model_errors is True
    assert recovery.manage_transport_errors is True
    assert recovery.initial_timeout_seconds == 180 and recovery.retry_timeout_seconds == 480
    assert recovery.retry_max_tokens == 8192
