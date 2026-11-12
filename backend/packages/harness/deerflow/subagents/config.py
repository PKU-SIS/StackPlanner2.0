"""Subagent configuration definitions."""

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig


@dataclass
class SubagentConfig:
    """Configuration for a subagent.

    Attributes:
        name: Unique identifier for the subagent.
        description: When Claude should delegate to this subagent.
        system_prompt: The system prompt that guides the subagent's behavior.
        tools: Optional list of tool names to allow. If None, inherits all tools.
        disallowed_tools: Optional list of tool names to deny.
        skills: Optional list of skill names to load. If None, inherits all enabled skills.
                If an empty list, no skills are loaded.
        model: Model to use - 'inherit' uses parent's model.
        max_turns: Maximum agent turns before stopping. Built-in agents use the
            value set here (general-purpose=150, bash=60) unless the global
            ``subagents.max_turns`` is set.
        timeout_seconds: Bare fallback execution-time cap. For built-in agents the
            effective limit is the global ``subagents.timeout_seconds`` (default
            1800 = 30 min), layered on by the registry; this 900 only applies
            when no differing global value exists.
    """

    name: str
    description: str
    system_prompt: str | None = None
    tools: list[str] | None = None
    disallowed_tools: list[str] | None = field(default_factory=lambda: ["task"])
    skills: list[str] | None = None
    model: str = "inherit"
    max_turns: int = 50
    timeout_seconds: int = 900
    # Internal specialists are addressable by orchestration code through the
    # registry but are not advertised to, or callable from, the default lead
    # agent's generic task tool.
    internal: bool = False

    # Trusted server-side overrides, absent from model action metadata. None
    # preserves native model/env defaults. Scheduling and tool choice unchanged.
    tool_call_limit: int | None = None
    model_errors_propagate: bool = False
    model_thinking_enabled: bool | None = None
    model_max_tokens: int | None = None
    model_timeout_seconds: float | None = None
    model_request_deadline_seconds: float | None = None

    def __post_init__(self):
        if type(self.model_errors_propagate) is not bool:
            raise ValueError("model_errors_propagate must be a trusted boolean")
        if self.tool_call_limit is not None and (type(self.tool_call_limit) is not int or not 2 <= self.tool_call_limit <= 256):
            raise ValueError("tool_call_limit must be a trusted integer in 2..256")
        if self.model_thinking_enabled is not None and type(self.model_thinking_enabled) is not bool:
            raise ValueError("model_thinking_enabled must be a trusted boolean or None")
        if self.model_max_tokens is not None and (type(self.model_max_tokens) is not int or self.model_max_tokens <= 0):
            raise ValueError("model_max_tokens must be a positive trusted integer")
        for name in ("model_timeout_seconds", "model_request_deadline_seconds"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0):
                raise ValueError(name + " must be a finite positive trusted timeout")


def _default_model_name(app_config: "AppConfig") -> str:
    if not app_config.models:
        raise ValueError("No chat models are configured. Please configure at least one model in config.yaml.")
    return app_config.models[0].name


def resolve_subagent_model_name(config: SubagentConfig, parent_model: str | None, *, app_config: "AppConfig | None" = None) -> str:
    """Resolve the effective model name a subagent should use."""
    if config.model != "inherit":
        return config.model

    if parent_model is not None:
        return parent_model

    if app_config is None:
        from deerflow.config import get_app_config

        app_config = get_app_config()
    return _default_model_name(app_config)
