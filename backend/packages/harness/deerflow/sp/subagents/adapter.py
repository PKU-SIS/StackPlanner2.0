"""Typed adapter boundary for SP delegation through DR2 subagents."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

A2A_PROTOCOL_VERSION = "1.0"
A2A_DELEGATE_MESSAGE = "delegate"
A2A_RESULT_MESSAGE = "task_result"
A2A_PROGRESS_MESSAGE = "progress"
_A2A_COMPLETION_STATUSES = frozenset({"complete", "partial", "blocked"})


class SPSubagentStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True, slots=True)
class SPToolExecutionContract:
    """Trusted application contract for existing tools, never parsed from actions.

    This does not choose a role or stage. It declares that source modification
    and software tests are not the deliverable, while real tool receipts remain
    mandatory for a Coder claiming execution.
    """

    execution_tools: tuple[str, ...]
    read_only_tools: tuple[str, ...] = ()
    artifact_write_tools: tuple[str, ...] = ()
    require_result_protocol: bool = False

    def __post_init__(self) -> None:
        if not self.execution_tools or any(not isinstance(name, str) or not name.strip() for name in (*self.execution_tools, *self.read_only_tools, *self.artifact_write_tools)):
            raise ValueError("Existing-tool contracts require nonempty trusted tool names")


@dataclass(frozen=True, slots=True)
class SPReportReferenceContract:
    """Host-owned literal obligations and explicit reference catalogs."""

    required_literals: tuple[str, ...] = ()
    catalogs: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # Host-owned validation format, never parsed from model/action metadata.
    content_type: str | None = None

    def __post_init__(self):
        if self.content_type is not None and (not isinstance(self.content_type, str) or self.content_type not in {"text/plain", "text/markdown", "text/html"}):
            raise ValueError("Report validation content type must be a trusted supported MIME type")
        if not isinstance(self.required_literals, tuple) or any(not isinstance(t, str) or not t.strip() or len(t) > 512 for t in self.required_literals):
            raise ValueError("Required literals must be trusted strings")
        if not isinstance(self.catalogs, tuple) or len(self.catalogs) > 32:
            raise ValueError("Reference catalogs must be bounded tuples")
        for pattern, values in self.catalogs:
            if not isinstance(pattern, str) or len(pattern) > 512 or not isinstance(values, tuple) or len(values) > 20000 or any(not isinstance(v, str) for v in values):
                raise ValueError("Invalid reference catalog")
            re.compile(pattern)


@dataclass(slots=True)
class SPSubagentTask:
    """Structured task sent from DelegateHandler to a subagent executor."""

    action_id: str
    subagent_type: str
    task: str
    description: str
    input_refs: list[str] = field(default_factory=list)
    expected_output: str | None = None
    context_refs: dict[str, Any] = field(default_factory=dict)
    thread_id: str | None = None
    run_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # Explicit A2A fields are intentionally additive. Existing callers may
    # still construct tasks with the legacy fields and receive safe defaults.
    protocol_version: str = A2A_PROTOCOL_VERSION
    message_type: str = A2A_DELEGATE_MESSAGE
    sender: str = "central"
    receiver: str | None = None
    stage: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    budgets: dict[str, Any] = field(default_factory=dict)
    # Set only by trusted Python application hooks, never by SPAction metadata.
    tool_execution_contract: SPToolExecutionContract | None = None
    # Server-captured Central selection, before software-oriented defaults.
    # None means legacy caller; {} means Central did not request an allowlist.
    control_tool_request: dict[str, Any] | None = None
    # Trusted application aliases, never populated from model metadata.
    artifact_type_aliases: dict[str, str] = field(default_factory=dict)
    # Trusted application source identity, never populated from model metadata.
    recovery_scope_key: str | None = None
    # Optional host-observed source IDs; permission only, never a must-include list.
    # Excluded from model protocol and never parsed from action/result metadata.
    source_reference_tokens: tuple[str, ...] = ()
    # Host-only; absent from the model protocol and action metadata.
    report_reference_contract: SPReportReferenceContract | None = None

    def validate_protocol(self) -> None:
        """Validate the transport contract at the SP/DR2 boundary.

        This is deliberately separate from ``SPAction.validate``: Central
        action validation describes the control plane, while this validation
        describes the message sent to a child agent.
        """

        if self.report_reference_contract is not None and not isinstance(self.report_reference_contract, SPReportReferenceContract):
            raise ValueError("Report reference contract must be a trusted typed contract")
        if self.recovery_scope_key is not None and (not isinstance(self.recovery_scope_key, str) or not self.recovery_scope_key.strip() or len(self.recovery_scope_key) > 200):
            raise ValueError("Recovery scope must be a bounded trusted source identity")
        if not isinstance(self.source_reference_tokens, tuple) or len(self.source_reference_tokens) > 20000 or any(not isinstance(token, str) or not token.strip() or len(token) > 256 for token in self.source_reference_tokens):
            raise ValueError("Source reference tokens must be a bounded tuple of trusted strings")
        if self.protocol_version != A2A_PROTOCOL_VERSION:
            raise ValueError(f"Unsupported SP A2A protocol version: {self.protocol_version!r}")
        if self.message_type != A2A_DELEGATE_MESSAGE:
            raise ValueError(f"SP subagent task must be a {A2A_DELEGATE_MESSAGE!r} message")
        if not isinstance(self.sender, str) or not self.sender.strip():
            raise ValueError("SP A2A task sender must be a non-empty string")
        if self.sender != "central":
            raise ValueError("Only Central may send SP subagent delegate messages")
        if not isinstance(self.action_id, str) or not self.action_id.strip():
            raise ValueError("SP A2A task action_id must be non-empty")
        if not isinstance(self.subagent_type, str) or not self.subagent_type.strip():
            raise ValueError("SP A2A task subagent_type must be non-empty")
        if self.receiver is not None and self.receiver != self.subagent_type:
            raise ValueError("SP A2A task receiver must match subagent_type")
        for field_name, values in (
            ("allowed_tools", self.allowed_tools),
            ("acceptance_criteria", self.acceptance_criteria),
        ):
            if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f"SP A2A task {field_name} must be a list of non-empty strings")
        if not isinstance(self.artifact_type_aliases, dict) or any(not isinstance(name, str) or not name.strip() for pair in self.artifact_type_aliases.items() for name in pair):
            raise ValueError("Artifact type aliases require nonempty string names")
        if not isinstance(self.budgets, dict):
            raise ValueError("SP A2A task budgets must be an object")
        for name, value in self.budgets.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("SP A2A task budget names must be non-empty strings")
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("SP A2A task budgets must contain positive integer values")

    def protocol_payload(self) -> dict[str, Any]:
        """Return a bounded, JSON-safe request envelope for prompts/events."""

        self.validate_protocol()
        return {
            "protocol_version": self.protocol_version,
            "message_type": self.message_type,
            "task_id": self.action_id,
            "run_id": self.run_id,
            "sender": self.sender,
            "receiver": self.receiver or self.subagent_type,
            "subagent_type": self.subagent_type,
            "stage": self.stage or self.metadata.get("stage"),
            "objective": self.task,
            "input_refs": list(self.input_refs),
            "allowed_tools": list(self.allowed_tools),
            "acceptance_criteria": list(self.acceptance_criteria),
            "budgets": dict(self.budgets),
            **({"artifact_type_aliases": dict(self.artifact_type_aliases)} if self.artifact_type_aliases else {}),
            **(
                {
                    "tool_execution_contract": {
                        "kind": "existing_tools",
                        "source_changes_required": False,
                        "execution_tools": list(self.tool_execution_contract.execution_tools),
                        "read_only_tools": list(self.tool_execution_contract.read_only_tools),
                        "execution_tools_mode": "any_successful_delegated_call",
                        **({"require_result_protocol": True} if self.tool_execution_contract.require_result_protocol else {}),
                    }
                }
                if self.tool_execution_contract is not None
                else {}
            ),
        }


@dataclass(slots=True)
class SPSubagentResult:
    """Normalized result returned by a SP subagent executor adapter."""

    status: SPSubagentStatus
    result: str | None = None
    error: str | None = None
    stop_reason: str | None = None
    task_id: str | None = None
    artifact_content: Any | None = None
    artifact_type: str | None = None
    artifact_metadata: dict[str, Any] = field(default_factory=dict)
    token_usage_records: list[dict[str, Any]] = field(default_factory=list)
    protocol_version: str = A2A_PROTOCOL_VERSION
    message_type: str = A2A_RESULT_MESSAGE
    completion_status: str | None = None
    retryable: bool = False
    recommended_action: str | None = None
    changed_files: list[str] = field(default_factory=list)
    tests: list[dict[str, Any]] = field(default_factory=list)

    def protocol_payload(self, *, task: SPSubagentTask | None = None) -> dict[str, Any]:
        """Return a compact result envelope; never include hidden reasoning."""

        if self.protocol_version != A2A_PROTOCOL_VERSION:
            raise ValueError(f"Unsupported SP A2A protocol version: {self.protocol_version!r}")
        if self.message_type != A2A_RESULT_MESSAGE:
            raise ValueError(f"SP subagent result must be a {A2A_RESULT_MESSAGE!r} message")
        completion_status = self.completion_status or self.artifact_metadata.get("completion_status")
        if completion_status not in _A2A_COMPLETION_STATUSES:
            completion_status = "complete" if self.is_success else "blocked"
        return {
            "protocol_version": self.protocol_version,
            "message_type": self.message_type,
            "task_id": self.task_id or (task.action_id if task is not None else None),
            "run_id": task.run_id if task is not None else None,
            "sender": task.receiver or task.subagent_type if task is not None else None,
            "receiver": "central",
            "subagent_type": task.subagent_type if task is not None else None,
            "stage": task.stage or task.metadata.get("stage") if task is not None else None,
            "status": self.status.value,
            "completion_status": completion_status,
            "summary": self.result,
            "error": self.error,
            "stop_reason": self.stop_reason,
            "retryable": bool(self.retryable),
            "recommended_action": self.recommended_action,
            "changed_files": list(self.changed_files),
            "tests": list(self.tests),
            "artifact_type": self.artifact_type,
            "artifact_metadata": {key: self.artifact_metadata.get(key) for key in ("created_paths", "filename", "artifact_id", "artifact_url", "evidence_gaps", "completion_status") if key in self.artifact_metadata},
        }

    @property
    def is_success(self) -> bool:
        return self.status == SPSubagentStatus.COMPLETED


class SPSubagentExecutorProtocol(Protocol):
    """Executor dependency used by DelegateHandler.

    Runtime integration should wrap DR2's ``SubagentExecutor`` behind this
    protocol. Tests can provide a fake executor without touching tools.
    """

    def execute(self, task: SPSubagentTask) -> SPSubagentResult:
        """Execute a SP subagent task and return a normalized result."""
