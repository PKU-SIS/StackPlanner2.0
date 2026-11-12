"""Optional trusted Python hooks for one SP graph; never read from API context.

Applications can supply task requirements, narrowed specialist configurations,
local tools and callbacks without replacing the Central policy or global registry.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from deerflow.sp.subagents.adapter import SPSubagentTask
from deerflow.subagents.config import SubagentConfig


@dataclass(frozen=True, slots=True)
class SPExecutionExtensions:
    tools: tuple[Any, ...] | None = None
    prepare_task: Callable[[SPSubagentTask], None] | None = None
    configure_subagent: Callable[[SPSubagentTask, SubagentConfig], SubagentConfig] | None = None
    callbacks: tuple[Any, ...] = ()
    central_middlewares: tuple[Any, ...] = ()
    central_recovery_owned: bool = False
    central_model_overrides: dict[str, Any] | None = None
    subagent_middlewares: Callable[[SPSubagentTask], tuple[Any, ...]] | None = None
    skill_names: tuple[str, ...] | None = None
    # Registered-tool argument identities, trusted Python only; default off.
    tool_call_key_fields: dict[str, tuple[str, ...]] | None = None
    image_tool_names: tuple[str, ...] | None = None

    # Trusted resource allowance; API/action metadata cannot enable it.
    # Native action selection and the budget middleware remain unchanged.
    max_actions: int | None = None
    precise_report_intent: bool = False
    # Preserve Central's chosen executor when inspection rerouting would lose
    # explicitly requested tools from this graph's trusted extension registry.
    preserve_registered_tool_intent: bool = False
    scoped_delegate_recovery: bool = False
    allow_limited_report: bool = False
    count_missing_reports: bool = False
    finish_validator: Callable[[Any, Any], str | None] | None = None
    observe_result: Callable[[SPSubagentTask, Any], None] | None = None

    def __post_init__(self):
        if type(self.count_missing_reports) is not bool:
            raise ValueError("count_missing_reports must be a trusted boolean")
        if type(self.central_recovery_owned) is not bool:
            raise ValueError("Central recovery ownership must be a trusted boolean")
        if self.central_model_overrides is not None and (not isinstance(self.central_model_overrides, dict) or set(self.central_model_overrides) - {"timeout", "stream_chunk_timeout", "reasoning_effort", "max_tokens"}):
            raise ValueError("Unsupported trusted Central model override")
        if self.image_tool_names is not None and (not isinstance(self.image_tool_names, tuple) or not self.image_tool_names or len(self.image_tool_names) > 16 or any(not isinstance(n, str) or not n.strip() for n in self.image_tool_names)):
            raise ValueError("Image tool names must be a bounded trusted tuple")
        if self.tool_call_key_fields is not None:
            if (
                not isinstance(self.tool_call_key_fields, dict)
                or len(self.tool_call_key_fields) > 64
                or any(
                    not isinstance(name, str) or not name.strip() or not isinstance(fields, tuple) or not fields or len(fields) > 16 or any(not isinstance(f, str) or not f.strip() for f in fields)
                    for name, fields in self.tool_call_key_fields.items()
                )
            ):
                raise ValueError("Tool identity fields must be a bounded trusted mapping of tuples")
        if type(self.preserve_registered_tool_intent) is not bool:
            raise ValueError("preserve_registered_tool_intent must be a trusted boolean")
        if type(self.precise_report_intent) is not bool:
            raise ValueError("precise_report_intent must be a trusted boolean")
        if type(self.allow_limited_report) is not bool:
            raise ValueError("allow_limited_report must be a trusted boolean")
        if type(self.scoped_delegate_recovery) is not bool:
            raise ValueError("scoped_delegate_recovery must be a trusted boolean")
        if self.max_actions is not None and (type(self.max_actions) is not int or not 2 <= self.max_actions <= 256):
            raise ValueError("Trusted SP action budgets must be integers in 2..256")
