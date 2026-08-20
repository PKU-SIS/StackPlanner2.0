"""Typed adapter boundary for SP delegation through DR2 subagents."""

from __future__ import annotations

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

    def validate_protocol(self) -> None:
        """Validate the transport contract at the SP/DR2 boundary.

        This is deliberately separate from ``SPAction.validate``: Central
        action validation describes the control plane, while this validation
        describes the message sent to a child agent.
        """

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
            "artifact_metadata": {
                key: self.artifact_metadata.get(key)
                for key in ("created_paths", "filename", "artifact_id", "artifact_url", "evidence_gaps", "completion_status")
                if key in self.artifact_metadata
            },
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
