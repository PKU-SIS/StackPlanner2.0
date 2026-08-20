"""DR2 runtime factory for the StackPlanner orchestration graph."""

from __future__ import annotations

import logging
import os
import re
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from deerflow.config.app_config import AppConfig, get_app_config
from deerflow.sp.agent_tools import (
    SPAcknowledgementMiddleware,
    SPControlActionMiddleware,
    SPFinishAvailabilityMiddleware,
    SPTerminalActionMiddleware,
    SPThinkLabelMiddleware,
    build_sp_control_tools,
)
from deerflow.sp.central import CENTRAL_AGENT_ACTION_PROMPT
from deerflow.sp.central.runtime_context import SPCentralRuntimeContext, build_sp_central_runtime_context
from deerflow.sp.subagents import (
    DR2SubagentExecutorAdapter,
    SPSubagentExecutorProtocol,
    SPSubagentResult,
    SPSubagentStatus,
    SPSubagentTask,
)

logger = logging.getLogger(__name__)

STACKPLANNER_ASSISTANT_ID = "stackplanner"
_NON_TOOL_OUTPUT_FORMAT_HINTS = frozenset(
    {
        "markdown",
        "md",
        "report",
        "json",
        "html",
        "pdf",
    }
)
_TOOL_NAME_ALIASES = {
    "node": "bash",
    "nodejs": "bash",
    "python": "bash",
    "python3": "bash",
    "sh": "bash",
    "shell": "bash",
    "terminal": "bash",
    "zsh": "bash",
}
_NO_SKILL_SENTINELS = frozenset(
    {
        "none",
        "no skill",
        "no skills",
        "n/a",
        "na",
        "null",
        "无",
        "无技能",
    }
)
SP_SUBAGENT_REGISTRY_NAMES = {
    "researcher": "sp-researcher",
    "coder": "sp-coder",
    "reporter": "sp-reporter",
    "outline": "sp-outline",
    "perception": "sp-perception",
    "memory_recaller": "sp-memory-recaller",
}

# Host credentials are never forwarded wholesale. This allowlist is consulted
# only when Central explicitly selects the matching Skill for one delegation;
# SubagentExecutor then independently verifies that the loaded Skill is public
# and declares the secret before binding it to sandbox commands.
_PLATFORM_SKILL_SECRET_ENV: dict[str, tuple[str, ...]] = {
    "video-generation": ("MINIMAX_API_KEY",),
}

_VIDEO_GENERATION_INTENT = re.compile(
    r"(?:"
    r"(?:生成|制作|创建|做|合成|渲染).{0,24}(?:视频|短片|影片|动画)"
    r"|(?:视频|短片|影片|动画).{0,24}(?:生成|制作|创建|合成|渲染)"
    r"|(?:generate|create|make|produce|render).{0,48}\b(?:video|movie|animation|clip)\b"
    r"|\b(?:video|movie|animation|clip)\b.{0,48}(?:generation|generator|create|make|render)"
    r"|\b(?:minimax|hailuo)\b"
    r")",
    re.IGNORECASE | re.DOTALL,
)
_DIRECT_VIDEO_OUTPUT_INTENT = re.compile(
    r"(?:"
    r"(?:生成|制作|创建|做|合成|渲染).{0,32}(?:视频|短片|影片|动画)"
    r"|(?:generate|create|make|produce|render).{0,64}\b(?:video|movie|animation|clip)\b"
    r")",
    re.IGNORECASE | re.DOTALL,
)
_CHART_VISUALIZATION_INTENT = re.compile(
    r"(?:"
    r"图表|折线图|曲线图|柱状图|条形图|饼图|散点图|面积图|雷达图|热力图|趋势图|"
    r"箱线图|漏斗图|桑基图|词云|流程图|思维导图|数据可视化|可视化(?:数据|结果|趋势)?"
    r"|\bchart(?:s)?\b|\bplot(?:s|ting)?\b|\bdata visualization\b|\bvisuali[sz](?:e|ation|ing)\b"
    r"|\b(?:line|bar|column|pie|scatter|area|radar|boxplot|funnel|sankey) graph\b"
    r")",
    re.IGNORECASE | re.DOTALL,
)

FRESH_USER_TURN_PROMPT = """
<fresh_user_turn>
This is a new user turn after the previous run ended abnormally. Treat the current
user message as authoritative and do not resume the previous unfinished report,
delegation, search, or tool call unless the user explicitly asks to continue it.
For greetings and simple questions, answer directly without web search, delegation,
or repeated internal thinking.
</fresh_user_turn>
""".strip()


def _runtime_config(config: RunnableConfig) -> dict[str, Any]:
    merged = dict(config.get("configurable", {}) or {})
    context = config.get("context", {}) or {}
    if isinstance(context, Mapping):
        merged.update(context)
    return merged


def _resolve_model_name(config: RunnableConfig, app_config: AppConfig, *, agent_model: str | None = None) -> str:
    if not app_config.models:
        raise ValueError("StackPlanner requires at least one configured chat model.")
    runtime = _runtime_config(config)
    requested = runtime.get("model_name") or runtime.get("model") or agent_model
    if requested and app_config.get_model_config(str(requested)) is not None:
        return str(requested)
    if requested:
        logger.warning("StackPlanner model %r is not configured; using %s", requested, app_config.models[0].name)
    return app_config.models[0].name


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _platform_skill_secrets_for_task(task: SPSubagentTask) -> dict[str, str]:
    requested = task.metadata.get("skill_names")
    if not isinstance(requested, list):
        return {}
    env_names = {env_name for skill_name in requested if isinstance(skill_name, str) for env_name in _PLATFORM_SKILL_SECRET_ENV.get(skill_name, ())}
    return {name: value for name in env_names if (value := os.environ.get(name))}


def _infer_task_skill_selection(
    task: SPSubagentTask,
    *,
    available_skill_names: frozenset[str] | None,
) -> None:
    """Fill an omitted Skill selection for unambiguous platform capabilities.

    Central remains free to choose Skills explicitly. This fallback only runs
    when it omitted ``metadata.skill_names`` and the requested capability has a
    configured, enabled platform Skill. Mutating the task here ensures the
    executor policy, secret binding, and progress events all observe the same
    validated selection.
    """
    if not available_skill_names:
        return
    text = "\n".join(part for part in (task.task, task.description, task.expected_output) if isinstance(part, str) and part.strip())
    raw_selection = task.metadata.get("skill_names")
    if "skill_names" not in task.metadata:
        inferred: list[str] = []
        if "video-generation" in available_skill_names and _VIDEO_GENERATION_INTENT.search(text):
            inferred.append("video-generation")
        if "chart-visualization" in available_skill_names and _CHART_VISUALIZATION_INTENT.search(text):
            inferred.append("chart-visualization")
        if inferred:
            task.metadata["skill_names"] = inferred
            logger.info("Auto-selected SP Skills %s for delegated task %s", inferred, task.action_id)
        return

    # ``find-skills`` is a discovery fallback, not an execution path. If
    # Central asks it to discover video generation while the enabled platform
    # catalog already contains that exact capability, route straight to it.
    if raw_selection == ["find-skills"] and "video-generation" in available_skill_names and _DIRECT_VIDEO_OUTPUT_INTENT.search(text):
        task.metadata["skill_names"] = ["video-generation"]
        logger.info("Replaced redundant find-skills delegation with video-generation for task %s", task.action_id)
        return
    if raw_selection == ["find-skills"] and "chart-visualization" in available_skill_names and _CHART_VISUALIZATION_INTENT.search(text):
        task.metadata["skill_names"] = ["chart-visualization"]
        logger.info("Replaced redundant find-skills delegation with chart-visualization for task %s", task.action_id)


def _task_stream_id(task: SPSubagentTask) -> str:
    parent_tool_call_id = task.metadata.get("__sp_parent_tool_call_id")
    return str(parent_tool_call_id) if parent_tool_call_id else task.action_id


def _apply_task_skill_policy(
    subagent_config: Any,
    task: SPSubagentTask,
    *,
    available_skill_names: frozenset[str] | None,
):
    """Apply the CentralAgent's requested Skill whitelist to one delegate."""
    raw_skill_names = task.metadata.get("skill_names")
    if raw_skill_names is None:
        configured = subagent_config.skills
        if configured is None or available_skill_names is None:
            return subagent_config
        filtered = [name for name in configured if name in available_skill_names]
        return replace(subagent_config, skills=filtered) if filtered != configured else subagent_config

    if not isinstance(raw_skill_names, list) or any(not isinstance(name, str) or not name.strip() for name in raw_skill_names):
        raise ValueError("SP action metadata.skill_names must be a list of non-empty Skill names")
    requested = list(dict.fromkeys(name.strip() for name in raw_skill_names if name.strip().lower() not in _NO_SKILL_SENTINELS))
    if not requested:
        # Small local models often serialize an omitted optional capability as
        # ["none"]. Treat that as no override, not as an invented Skill.
        task.metadata.pop("skill_names", None)
        configured = subagent_config.skills
        if configured is None or available_skill_names is None:
            return subagent_config
        filtered = [name for name in configured if name in available_skill_names]
        return replace(subagent_config, skills=filtered) if filtered != configured else subagent_config
    task.metadata["skill_names"] = requested
    if available_skill_names is None:
        raise ValueError("SP Skill catalog is unavailable; cannot validate metadata.skill_names")
    unavailable = sorted(set(requested) - set(available_skill_names))
    if unavailable:
        raise ValueError(f"SP action requested unavailable or disabled Skills: {unavailable}")
    return replace(subagent_config, skills=requested)


def _apply_task_tool_policy(
    subagent_config: Any,
    task: SPSubagentTask,
    *,
    available_tools: list[Any],
):
    """Apply a validated Central-requested Tool allowlist to one delegate."""
    raw_tool_names = task.metadata.get("tool_names")
    if raw_tool_names is None:
        return subagent_config
    if not isinstance(raw_tool_names, list) or any(not isinstance(name, str) or not name.strip() for name in raw_tool_names):
        raise ValueError("SP action metadata.tool_names must be a list of non-empty Tool names")

    requested = list(dict.fromkeys(_TOOL_NAME_ALIASES.get(name.strip().lower(), name.strip()) for name in raw_tool_names))
    task.metadata["tool_names"] = requested
    format_hints = {name for name in requested if name.lower() in _NON_TOOL_OUTPUT_FORMAT_HINTS}
    if format_hints:
        # Qwen occasionally places the requested output format in tool_names
        # (for example ["markdown"]). Treat that as an absent allowlist so the
        # role's safe defaults remain available; unknown executable-looking
        # names still fail closed below.
        logger.info(
            "Ignoring non-Tool output format hints %s for delegated task %s",
            sorted(format_hints),
            task.action_id,
        )
        return subagent_config
    available_names = {str(getattr(tool, "name", "")) for tool in available_tools}
    unavailable = sorted(set(requested) - available_names)
    if unavailable:
        raise ValueError(f"SP action requested unavailable Tools: {unavailable}")

    denied = set(subagent_config.disallowed_tools or [])
    role_allowlist = set(subagent_config.tools) if subagent_config.tools is not None else None
    forbidden = sorted(name for name in requested if name in denied or (role_allowlist is not None and name not in role_allowlist))
    if forbidden:
        raise ValueError(f"SP action requested Tools forbidden for {subagent_config.name}: {forbidden}")
    return replace(subagent_config, tools=requested)


@dataclass(slots=True)
class DR2SPExecutorProvider:
    """Create DR2 SubagentExecutor instances bound to the active graph state."""

    app_config: AppConfig
    parent_model: str
    runnable_config: RunnableConfig
    available_skill_names: frozenset[str] | None = None
    memory_agent_name: str | None = None
    user_id: str | None = None
    _tools: list[Any] | None = field(default=None, init=False, repr=False)
    _tools_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def _available_tools(self) -> list[Any]:
        if self._tools is None:
            with self._tools_lock:
                if self._tools is None:
                    from deerflow.tools import get_available_tools

                    self._tools = get_available_tools(
                        model_name=self.parent_model,
                        subagent_enabled=False,
                        app_config=self.app_config,
                    )
        return self._tools

    def __call__(self, state: Mapping[str, Any], runtime: Runtime) -> SPSubagentExecutorProtocol:
        context = _mapping(getattr(runtime, "context", None))
        metadata = _mapping(self.runnable_config.get("metadata"))
        writer = getattr(runtime, "stream_writer", None)
        journal = context.get("__run_journal")
        parent_abort_event = context.get("__run_abort_event")
        report_usage = getattr(journal, "record_external_llm_usage_records", None)

        def executor_factory(task: SPSubagentTask):
            from deerflow.subagents import SubagentExecutor, get_subagent_config

            registry_name = SP_SUBAGENT_REGISTRY_NAMES.get(task.subagent_type)
            subagent_config = get_subagent_config(registry_name, app_config=self.app_config) if registry_name else None
            if subagent_config is None or not subagent_config.internal:
                raise ValueError(f"Unknown StackPlanner subagent type: {task.subagent_type}")
            _infer_task_skill_selection(
                task,
                available_skill_names=self.available_skill_names,
            )
            available_tools = self._available_tools()
            subagent_config = _apply_task_skill_policy(
                subagent_config,
                task,
                available_skill_names=self.available_skill_names,
            )
            subagent_config = _apply_task_tool_policy(
                subagent_config,
                task,
                available_tools=available_tools,
            )
            thread_id = str(context.get("thread_id") or task.thread_id or "") or None
            run_id = str(context.get("run_id") or task.run_id or "") or None
            stream_task_id = _task_stream_id(task)
            skill_names = task.metadata.get("skill_names")
            skill_step_offset = len(skill_names) if isinstance(skill_names, list) else 0

            def observe_step(message: dict[str, Any], message_index: int) -> None:
                if callable(writer):
                    writer(
                        {
                            "type": "task_running",
                            "task_id": stream_task_id,
                            "message": message,
                            "message_index": skill_step_offset + message_index,
                            "subagent_type": task.subagent_type,
                        }
                    )

            return SubagentExecutor(
                config=subagent_config,
                tools=available_tools,
                app_config=self.app_config,
                parent_model=self.parent_model,
                sandbox_state=state.get("sandbox"),
                thread_data=state.get("thread_data"),
                thread_id=thread_id,
                trace_id=str(metadata.get("trace_id") or uuid.uuid4().hex[:8]),
                user_id=str(context.get("user_id") or self.user_id) if context.get("user_id") or self.user_id else None,
                user_role=str(context.get("user_role")) if context.get("user_role") else None,
                oauth_provider=str(context.get("oauth_provider")) if context.get("oauth_provider") else None,
                oauth_id=str(context.get("oauth_id")) if context.get("oauth_id") else None,
                run_id=run_id,
                channel_user_id=str(context.get("channel_user_id")) if context.get("channel_user_id") else None,
                deerflow_trace_id=str(context.get("deerflow_trace_id")) if context.get("deerflow_trace_id") else None,
                memory_agent_name=self.memory_agent_name if task.subagent_type == "memory_recaller" else None,
                user_scoped_skills=True,
                step_observer=observe_step,
                token_usage_observer=((lambda record: report_usage([record])) if callable(report_usage) else None),
                force_isolated_loop=True,
                platform_skill_secrets=_platform_skill_secrets_for_task(task),
                parent_abort_event=parent_abort_event,
            )

        def observe_raw_result(raw_result: Any) -> None:
            usage_records = list(getattr(raw_result, "token_usage_records", None) or [])
            if callable(report_usage) and usage_records:
                report_usage(usage_records)

        adapter = DR2SubagentExecutorAdapter(
            executor_factory,
            result_observer=observe_raw_result,
        )
        return _ProgressReportingExecutor(
            adapter=adapter,
            writer=writer,
            task_preparer=lambda task: _infer_task_skill_selection(
                task,
                available_skill_names=self.available_skill_names,
            ),
        )


@dataclass(slots=True)
class _ProgressReportingExecutor:
    adapter: DR2SubagentExecutorAdapter
    writer: Any = None
    task_preparer: Any = None

    def execute(self, task: SPSubagentTask) -> SPSubagentResult:
        if callable(self.task_preparer):
            self.task_preparer(task)
        task_id = _task_stream_id(task)
        if callable(self.writer):
            self.writer(
                {
                    "type": "task_started",
                    "task_id": task_id,
                    "description": task.task,
                    "prompt": task.task,
                    "subagent_type": task.subagent_type,
                }
            )
            skill_names = task.metadata.get("skill_names")
            if isinstance(skill_names, list):
                for index, skill_name in enumerate(skill_names, start=1):
                    if not isinstance(skill_name, str):
                        continue
                    # Represent Skill activation as a compact named step using
                    # the existing subtask timeline protocol. No Skill body or
                    # credential is included.
                    self.writer(
                        {
                            "type": "task_running",
                            "task_id": task_id,
                            "message": {
                                "type": "tool",
                                "name": f"Skill: {skill_name}",
                                "content": "Skill loaded",
                            },
                            "message_index": index,
                            "subagent_type": task.subagent_type,
                        }
                    )
        try:
            result = self.adapter.execute(task)
        except Exception as exc:
            if callable(self.writer):
                self.writer(
                    {
                        "type": "task_failed",
                        "task_id": task_id,
                        "error": str(exc),
                        "subagent_type": task.subagent_type,
                    }
                )
            raise
        if callable(self.writer):
            if result.status == SPSubagentStatus.CANCELLED:
                event_type = "task_cancelled"
            elif result.status == SPSubagentStatus.TIMED_OUT:
                event_type = "task_timed_out"
            else:
                event_type = "task_completed" if result.is_success else "task_failed"
            self.writer(
                {
                    "type": event_type,
                    "task_id": task_id,
                    "result": result.result,
                    "error": result.error,
                    "stop_reason": result.stop_reason,
                    "subagent_type": task.subagent_type,
                }
            )
        return result


def make_sp_agent(config: RunnableConfig, *, app_config: AppConfig | None = None):
    """Build one SP CentralAgent using DeerFlow's native agent loop.

    The CentralAgent is bound only to SP control actions. Ordinary DeerFlow
    tools are supplied to delegated subagents by ``DR2SPExecutorProvider`` so
    planning and execution remain separate at the model-schema boundary.
    """
    resolved_app_config = app_config or get_app_config()
    runtime = _runtime_config(config)
    from deerflow.runtime.user_context import get_effective_user_id

    raw_user_id = runtime.get("user_id")
    user_id = str(raw_user_id) if raw_user_id else get_effective_user_id()
    raw_agent_name = runtime.get("sp_agent_name") or runtime.get("agent_name")
    agent_name = str(raw_agent_name) if raw_agent_name else None
    central_context: SPCentralRuntimeContext = build_sp_central_runtime_context(
        resolved_app_config,
        agent_name=agent_name,
        user_id=user_id,
    )
    model_name = _resolve_model_name(
        config,
        resolved_app_config,
        agent_model=central_context.agent_model,
    )
    model_config = resolved_app_config.get_model_config(model_name)
    thinking_enabled = bool(runtime.get("thinking_enabled", True))
    if model_config is not None and not model_config.supports_thinking:
        thinking_enabled = False
    try:
        max_parallel_delegates = int(runtime.get("max_concurrent_subagents") or 3)
    except (TypeError, ValueError):
        max_parallel_delegates = 3
    metadata = dict(config.get("metadata") or {})
    metadata.update(
        {
            "agent_name": STACKPLANNER_ASSISTANT_ID,
            "model_name": model_name,
            "thinking_enabled": thinking_enabled,
            "orchestration_mode": STACKPLANNER_ASSISTANT_ID,
            "central_tool_mode": "sp_actions_only",
            "sp_agent_name": central_context.agent_name or "default",
            "available_skills": sorted(central_context.available_skill_names) if central_context.available_skill_names is not None else None,
        }
    )
    sp_config = dict(config)
    configurable = dict(sp_config.get("configurable", {}) or {})
    configurable.update(
        {
            "model_name": model_name,
            "thinking_enabled": thinking_enabled,
            "agent_name": central_context.agent_name,
            "orchestration_mode": STACKPLANNER_ASSISTANT_ID,
            # The CentralAgent delegates through sp_delegate; DeerFlow's native
            # task/todo tools must not be injected by the reused lead factory.
            "subagent_enabled": False,
            "is_plan_mode": False,
        }
    )
    sp_config["configurable"] = configurable
    sp_config["metadata"] = metadata

    executor_provider = DR2SPExecutorProvider(
        app_config=resolved_app_config,
        parent_model=model_name,
        runnable_config=sp_config,
        available_skill_names=central_context.available_skill_names,
        memory_agent_name=central_context.agent_name,
        user_id=user_id,
    )
    prompt_sections = [CENTRAL_AGENT_ACTION_PROMPT]
    prompt_sections.append(
        f"<sp-delegation-limit>\nEmit at most {max_parallel_delegates} sibling sp_delegate calls in one model turn. If more work remains, inspect the first batch and delegate the next batch afterward.\n</sp-delegation-limit>"
    )
    if runtime.get("fresh_user_turn_after_terminal"):
        prompt_sections.append(FRESH_USER_TURN_PROMPT)
    if central_context.system_prompt_section:
        prompt_sections.append(central_context.system_prompt_section)
    from deerflow.agents.lead_agent.agent import _make_lead_agent
    from deerflow.agents.middlewares.subagent_limit_middleware import SubagentLimitMiddleware
    from deerflow.sp.middlewares import TaskMemoryMiddleware
    from deerflow.sp.prompt import PromptContextBuilder

    graph = _make_lead_agent(
        sp_config,
        app_config=resolved_app_config,
        extra_tools=build_sp_control_tools(),
        extra_middlewares=[
            TaskMemoryMiddleware(context_builder=PromptContextBuilder(), inject_context=True),
            SPTerminalActionMiddleware(),
            SPAcknowledgementMiddleware(),
            SPFinishAvailabilityMiddleware(),
            SPControlActionMiddleware(executor_provider=executor_provider),
            SPThinkLabelMiddleware(),
            SubagentLimitMiddleware(
                max_concurrent=max_parallel_delegates,
                tool_names={"sp_delegate"},
            ),
        ],
        prompt_prefix="\n\n".join(prompt_sections),
        identity_name="StackPlanner 2.0",
        include_base_tools=False,
        include_base_prompt=False,
        inject_long_term_memory=False,
        control_plane_only=True,
    )
    graph.metadata = dict(metadata)
    return graph
