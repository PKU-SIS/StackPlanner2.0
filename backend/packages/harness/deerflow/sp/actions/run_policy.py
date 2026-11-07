"""Optional trusted, in-process constraints for a single SP run."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from deerflow.sp.actions.handlers.base import HandlerContext
    from deerflow.sp.actions.schema import SPAction
    from deerflow.sp.subagents.adapter import SPSubagentResult, SPSubagentTask


class RunPolicy(Protocol):
    def before_delegate(self, action: SPAction, context: HandlerContext) -> str | None: ...
    def configure_task(self, task: SPSubagentTask, context: HandlerContext) -> None: ...
    def prepare_result(self, task: SPSubagentTask, result: SPSubagentResult, context: HandlerContext) -> None: ...
    def delegation_stage(self, target: str, metadata: dict) -> str | None: ...
    def intermediate_artifact_type(self, action: SPAction) -> str | None: ...
    def is_document_review(self, task: SPSubagentTask) -> bool: ...
    def checked_execution(self, task: SPSubagentTask, name: str, args: dict, output: object) -> bool: ...
    def validate_written_file(self, task: SPSubagentTask, messages: list[dict], metadata: dict) -> dict | None: ...
    def review_gap(self, task: SPSubagentTask, messages: list[dict]) -> str | None: ...
    def check_finish(self, ref: dict | None, context: HandlerContext) -> None: ...


# Installed by trusted callers, never loaded from model-controlled metadata.
active_run_policy: ContextVar[RunPolicy | None] = ContextVar("sp_run_policy", default=None)


def scoped_delegate_stage(target: str, metadata: dict) -> str | None:
    """Only a trusted in-process policy can disambiguate explicit role choices."""
    method = getattr(active_run_policy.get(), "delegation_stage", None)
    return method(target, metadata) if method else None
