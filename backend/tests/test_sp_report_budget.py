"""Report Outline gets a task-scoped token budget without mutating shared config."""

from __future__ import annotations

from deerflow.agents.middlewares.token_budget_middleware import TokenBudgetMiddleware
from deerflow.agents.middlewares.tool_error_handling_middleware import build_subagent_runtime_middlewares
from deerflow.config.app_config import AppConfig
from deerflow.config.model_config import ModelConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.config.subagents_config import SubagentsAppConfig
from deerflow.config.token_budget_config import TokenBudgetConfig
from deerflow.sp.runtime import app_config_for_report_outline
from deerflow.sp.subagents import SPSubagentTask


def _app_config() -> AppConfig:
    model = ModelConfig(
        name="test-model",
        display_name="test-model",
        description=None,
        use="langchain_openai:ChatOpenAI",
        model="test-model",
        max_tokens=None,
        supports_thinking=False,
        supports_reasoning_effort=False,
        when_thinking_enabled=None,
        when_thinking_disabled=None,
        thinking=None,
        supports_vision=False,
    )
    return AppConfig(
        models=[model],
        sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"),
        subagents=SubagentsAppConfig(
            token_budget=TokenBudgetConfig(enabled=True, max_tokens=120_000),
            report_outline_token_budget=TokenBudgetConfig(enabled=True, max_tokens=240_000),
        ),
    )


def _task(report_role: str | None = None) -> SPSubagentTask:
    return SPSubagentTask(
        action_id="a-outline",
        subagent_type="outline",
        task="plan",
        description="plan",
        metadata={"report_role": report_role} if report_role else {},
    )


def _budget(middlewares: list[object]) -> int:
    middleware = next(item for item in middlewares if isinstance(item, TokenBudgetMiddleware))
    return middleware._config.max_tokens


def test_report_outline_config_copy_drives_real_middleware_budget():
    app_config = _app_config()
    report_config = app_config_for_report_outline(app_config, _task("outline"))

    assert report_config is not app_config
    assert app_config.subagents.get_token_budget_for("sp-outline").max_tokens == 120_000
    assert report_config.subagents.get_token_budget_for("sp-outline").max_tokens == 240_000

    report_middlewares = build_subagent_runtime_middlewares(
        app_config=report_config,
        model_name="test-model",
        agent_name="sp-outline",
    )
    ordinary_middlewares = build_subagent_runtime_middlewares(
        app_config=app_config,
        model_name="test-model",
        agent_name="sp-outline",
    )

    assert _budget(report_middlewares) == 240_000
    assert _budget(ordinary_middlewares) == 120_000
    assert app_config.subagents.get_token_budget_for("sp-researcher").max_tokens == 120_000


def test_non_report_task_returns_shared_config_and_hard_stop_semantics_remain():
    app_config = _app_config()
    assert app_config_for_report_outline(app_config, _task()) is app_config

    report_config = app_config_for_report_outline(app_config, _task("outline"))
    middlewares = build_subagent_runtime_middlewares(
        app_config=report_config,
        model_name="test-model",
        agent_name="sp-outline",
    )
    budget = next(item for item in middlewares if isinstance(item, TokenBudgetMiddleware))
    assert budget._config.enabled is True
    assert budget._config.hard_stop_threshold == 1.0
