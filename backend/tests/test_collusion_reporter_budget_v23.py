"""Opt-in Reporter capacity preserves native defaults, tools and thinking."""

import sys
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage
from test_collusion_audit import scoped_profile as scoped_profile

from deerflow.subagents.config import SubagentConfig


def test_native_budget_fields_are_default_off_and_reject_invalid_values():
    cfg = SubagentConfig("legacy", "unchanged")
    assert cfg.model_max_tokens is None
    assert cfg.model_timeout_seconds is None
    assert cfg.model_request_deadline_seconds is None
    for field, value in [("model_max_tokens", 0), ("model_max_tokens", True), ("model_max_tokens", 1.2), ("model_timeout_seconds", -1), ("model_request_deadline_seconds", float("nan")), ("model_request_deadline_seconds", float("inf"))]:
        with pytest.raises(ValueError):
            SubagentConfig("bad", "test", **{field: value})


def test_only_explicit_quality_reporter_gets_budget_not_other_roles(scoped_profile):
    from deerflow.sp.subagents import SPSubagentTask

    original = SubagentConfig("sp-reporter", "report", tools=["write_file"], skills=["stackplanner-reporting"])
    task = SPSubagentTask("r", "reporter", "report", "report")
    scoped_profile.prepare_task(task)
    default = scoped_profile.configure_subagent(task, original)
    assert default.model_max_tokens is None
    scoped_profile.quality_controls = True
    scoped_profile.reporter_max_tokens = 16384
    scoped_profile.reporter_request_timeout = 480.0
    configured = scoped_profile.configure_subagent(task, original)
    assert configured.model_max_tokens == 16384
    assert configured.model_timeout_seconds == 480
    assert configured.model_request_deadline_seconds == 480
    assert original.model_max_tokens is None
    assert task.subagent_type == "reporter" and task.task == "report"
    assert configured.skills == original.skills
    for role in ["coder", "researcher", "perception", "memory_recaller"]:
        t = SPSubagentTask("x", role, "review", "test")
        scoped_profile.prepare_task(t)
        c = scoped_profile.configure_subagent(t, SubagentConfig("sp-" + role, "test"))
        assert c.model_max_tokens is None and c.model_timeout_seconds is None and c.model_request_deadline_seconds is None


def test_model_metadata_cannot_enable_budget_override(scoped_profile):
    from deerflow.sp.subagents import SPSubagentTask

    scoped_profile.quality_controls = True
    t = SPSubagentTask("x", "reporter", "report", "test", metadata={"model_max_tokens": 99999, "reporter_request_timeout": 9999})
    scoped_profile.prepare_task(t)
    c = scoped_profile.configure_subagent(t, SubagentConfig("sp-reporter", "test"))
    assert c.model_max_tokens is None and c.model_request_deadline_seconds is None


@pytest.fixture
def real_executor_module():
    import test_subagent_executor as existing_tests

    # Reuse the repository's real-executor isolation loader. conftest installs
    # a MagicMock at the normal import path to break unrelated circular imports.
    loader = existing_tests._setup_executor_classes.__wrapped__()
    next(loader)
    try:
        yield sys.modules["deerflow.subagents.executor"]
    finally:
        next(loader, None)


@pytest.mark.parametrize("override", [False, True])
def test_executor_scope_changes_only_selected_model_capacity(monkeypatch, override, real_executor_module):
    module = real_executor_module

    class Guard:
        request_timeout_seconds = 300.0

    monkeypatch.setitem(sys.modules, "deerflow.agents.middlewares.llm_error_handling_middleware", SimpleNamespace(LLMErrorHandlingMiddleware=Guard))
    guard = Guard()
    seen = {}
    monkeypatch.setattr(module, "create_chat_model", lambda **kw: seen.update(model=kw) or object())
    monkeypatch.setattr(module, "create_agent", lambda **kw: seen.update(agent=kw) or object())
    monkeypatch.setitem(sys.modules, "deerflow.agents.middlewares.tool_error_handling_middleware", SimpleNamespace(build_subagent_runtime_middlewares=lambda **kw: [guard]))
    executor = object.__new__(module.SubagentExecutor)
    executor.app_config = SimpleNamespace()
    executor.model_name = "qwen"
    executor.max_tokens_per_step = 8192
    executor.memory_agent_name = None
    executor.tools = []
    executor.tool_call_key_fields = {}
    executor.extra_middlewares = []
    executor._should_enable_tool_thinking = lambda app, tools: True
    kw = {"model_max_tokens": 16384, "model_timeout_seconds": 480.0, "model_request_deadline_seconds": 480.0} if override else {}
    executor.config = SubagentConfig("sp-reporter", "test", **kw)
    executor._create_agent()
    assert seen["model"]["max_tokens"] == (16384 if override else 8192)
    assert seen["model"].get("timeout") == (480 if override else None)
    assert seen["model"]["thinking_enabled"] is True
    assert guard.request_timeout_seconds == (480 if override else 300)
    assert executor.max_tokens_per_step == 8192


def test_real_vllm_payload_accepts_capacity_with_thinking_preserved():
    from deerflow.models.vllm_provider import VllmChatModel

    model = VllmChatModel(model="qwen3.8-27b", base_url="http://127.0.0.1:8075/v1", api_key="EMPTY", max_tokens=16384, timeout=480, max_retries=0, extra_body={"chat_template_kwargs": {"enable_thinking": True}})
    payload = model._get_request_payload([HumanMessage("source and report contract")])
    assert payload["max_completion_tokens"] == 16384
    assert payload["extra_body"]["chat_template_kwargs"]["enable_thinking"] is True
    assert model.request_timeout == 480 and model.root_async_client.max_retries == 0
