"""Portable native policy hooks remain passive without trusted run-local binding."""

import asyncio
import importlib
import json
from types import SimpleNamespace

import pytest

from deerflow.sp.actions import ActionType, SPAction
from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.handlers.delegate import DelegateHandler
from deerflow.sp.actions.handlers.finish import FinishHandler
from deerflow.sp.actions.schema import HandlerResult
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus, SPSubagentTask
from deerflow.sp.subagents.dr2_adapter import _execution_verification, normalize_dr2_subagent_result


def _policies():
    # Keep a missing optional integration visible as an individual RED test.
    return importlib.import_module("deerflow.sp.actions.run_policy")


def _task(role="coder"):
    return SPSubagentTask(action_id="audit", subagent_type=role, task="Check supplied data.", description="Native test", metadata={"task_kind": "data_processing"})


class _Policy:
    def __init__(self, events=None):
        self.events = events if events is not None else []

    def before_delegate(self, action, context):
        self.events.append("before")
        return None

    def intermediate_artifact_type(self, action):
        return None

    def configure_task(self, task, context):
        self.events.append("configure")
        task.metadata["trusted_check"] = True

    def prepare_result(self, task, result, context):
        self.events.append("prepare")

    def delegation_stage(self, target, metadata):
        return "verification" if target == "coder" else None

    def is_document_review(self, task):
        return False

    def checked_execution(self, task, name, args, output):
        return name == "checked_file" and args == {"path": "/mnt/user-data/workspace/answer.py"} and output == "checked"

    def validate_written_file(self, task, messages, metadata):
        return None

    def review_gap(self, task, messages):
        return None

    def check_finish(self, ref, context):
        self.events.append("finish")
        raise ValueError("Trusted review remains incomplete.")


def test_run_policy_is_trusted_context_local_and_metadata_cannot_install_it():
    policies = _policies()
    assert policies.active_run_policy.get() is None
    assert policies.scoped_delegate_stage("coder", {"active_run_policy": _Policy(), "stage": "verification"}) is None

    async def bound():
        token = policies.active_run_policy.set(_Policy())
        try:
            await asyncio.sleep(0)
            assert policies.scoped_delegate_stage("coder", {}) == "verification"
        finally:
            policies.active_run_policy.reset(token)

    async def unbound():
        await asyncio.sleep(0)
        assert policies.active_run_policy.get() is None
        assert policies.scoped_delegate_stage("coder", {}) is None

    async def concurrent():
        await asyncio.gather(bound(), unbound())

    asyncio.run(concurrent())
    assert policies.active_run_policy.get() is None


@pytest.mark.parametrize("bound", [False, True])
def test_delegate_uses_native_dispatch_and_only_bound_policy_runs_hooks(bound):
    policies = _policies()
    events = []

    class Executor:
        def execute(self, task):
            events.append("execute")
            assert task.metadata.get("trusted_check", False) is bound
            return SPSubagentResult(status=SPSubagentStatus.COMPLETED, result="Checked.")

    class Handler(DelegateHandler):
        def _handle_success(self, action, context, delegate_entry, result, *, task):
            events.append("accept")
            return HandlerResult(next_step="continue")

    token = policies.active_run_policy.set(_Policy(events) if bound else None)
    try:
        action = SPAction.create(ActionType.DELEGATE, reason="Read evidence", target_agent="researcher", task="Read the supplied evidence.")
        result = Handler(executor=Executor()).handle(action, HandlerContext(state={}, stack=TaskMemoryStack(), run_id="run"))
    finally:
        policies.active_run_policy.reset(token)
    assert result.next_step == "continue"
    assert events == (["before", "configure", "execute", "prepare", "accept"] if bound else ["execute", "accept"])


def test_policy_rejection_prevents_delegate_dispatch():
    policies = _policies()

    class Reject(_Policy):
        def before_delegate(self, action, context):
            return "The trusted stage is not ready."

    executor = SimpleNamespace(execute=lambda task: pytest.fail("Rejected delegation must not execute"))
    token = policies.active_run_policy.set(Reject())
    try:
        action = SPAction.create(ActionType.DELEGATE, reason="Read evidence", target_agent="researcher", task="Read evidence.")
        result = DelegateHandler(executor=executor).handle(action, HandlerContext(state={}, stack=TaskMemoryStack()))
    finally:
        policies.active_run_policy.reset(token)
    assert result.next_step == "error_recoverable"
    assert result.error == "The trusted stage is not ready."


def test_finish_policy_adds_a_gate_after_native_artifact_requirement():
    policies = _policies()
    events = []
    token = policies.active_run_policy.set(_Policy(events))
    try:
        action = SPAction.create(ActionType.FINISH, reason="Ready", task="Done.")
        context = HandlerContext(state={}, stack=TaskMemoryStack())
        native_rejected = FinishHandler().handle(action, context)
        assert "final artifact ref" in native_rejected.error
        assert events == []
        action.metadata["allow_without_artifact"] = True
        policy_rejected = FinishHandler().handle(action, context)
    finally:
        policies.active_run_policy.reset(token)
    assert policy_rejected.next_step == "error_recoverable"
    assert policy_rejected.error == "Trusted review remains incomplete."
    assert events == ["finish"]


def _pair(name, identifier, args, content, status="success"):
    return [{"type": "ai", "tool_calls": [{"id": identifier, "name": name, "args": args}]}, {"type": "tool", "name": name, "tool_call_id": identifier, "content": content, "status": status}]


@pytest.mark.parametrize("damage", [None, "orphan", "error", "later_write", "issued_before_write"])
def test_custom_execution_requires_bound_policy_paired_success_and_postwrite_issue(damage):
    policies = _policies()
    write = _pair("write_file", "write", {"path": "/mnt/user-data/workspace/answer.py", "content": "pass"}, "written")
    check = _pair("checked_file", "check", {"path": "/mnt/user-data/workspace/answer.py"}, "checked")
    messages = [*write, *check]
    if damage == "orphan":
        messages = [*write, check[1]]
    elif damage == "error":
        check[1]["status"] = "error"
    elif damage == "later_write":
        messages = [*check, *write]
    elif damage == "issued_before_write":
        messages = [check[0], *write, check[1]]
    assert _execution_verification(messages, task=_task())["passed"] is False
    token = policies.active_run_policy.set(_Policy())
    try:
        assert _execution_verification(messages, task=_task())["passed"] is (damage is None)
    finally:
        policies.active_run_policy.reset(token)


def test_normalization_keeps_native_default_and_applies_only_bound_review_gap():
    policies = _policies()

    class Review(_Policy):
        def review_gap(self, task, messages):
            return "One evidence reference still needs review."

    def raw():
        return SimpleNamespace(status="completed", result=json.dumps({"summary": "Outline ready.", "artifact_content": "A short outline.", "artifact_metadata": {"completion_status": "complete", "evidence_gaps": []}}), ai_messages=[])

    task = _task("outline")
    assert normalize_dr2_subagent_result(raw(), task=task).completion_status == "complete"
    token = policies.active_run_policy.set(Review())
    try:
        result = normalize_dr2_subagent_result(raw(), task=task)
    finally:
        policies.active_run_policy.reset(token)
    assert result.completion_status == "partial"
    assert result.artifact_metadata["evidence_gaps"] == ["One evidence reference still needs review."]
    assert result.retryable is True
