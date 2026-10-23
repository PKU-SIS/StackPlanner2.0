"""Real make_sp_agent flows for fixed report acceptance.

Only the model and executor boundaries are replaced.  Report body setup is the
shared test seed; acceptance progress, revision, review, finish and delivery all
run through production handlers.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _agent_e2e_helpers import FakeToolCallingModel
from test_sp_report_p3 import _seed
from test_sp_report_wiring import _app_config

from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus


class _NoCentralModel:
    def bind_tools(self, tools, **kwargs):  # type: ignore[no-untyped-def]
        return self

    def _generate(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("fixed acceptance should not call the Central model")


class _FixedExecutor:
    def __init__(self, review_statuses: list[str]):
        self.review_statuses = list(review_statuses)
        self.tasks = []
        self.review_action_ids = []
        self.revise_action_ids = []
        self.finish_attempts = 0

    def execute(self, task):
        self.tasks.append(task)
        report_task = str(task.metadata.get("report_task") or "")
        if report_task == "integrate_review":
            self.review_action_ids.append(task.action_id)
            status = self.review_statuses.pop(0)
            if status == "technical":
                return SPSubagentResult(
                    status=SPSubagentStatus.FAILED,
                    result="",
                    error="simulated review timeout",
                    artifact_metadata={"artifact_type": "integration_review", "report_task": "integrate_review"},
                )
            packet = task.context_refs["report_packet"]
            content = json.dumps(
                {
                    "inputs_digest": packet["inputs_digest"],
                    "reviewed_candidate_sha256": packet["candidate_sha256"],
                    "review": {
                        "status": "ready" if status == "ready" else "issues",
                        "outcome": "deliver" if status == "ready" else "revise",
                        "findings": [] if status == "ready" else [
                            {"kind": "coverage", "severity": "blocking", "node_ids": ["n2"], "message": "缺少必要内容"}
                        ],
                        "covered_requirement_ids": ["R1"],
                        "edits": [],
                    },
                },
                ensure_ascii=False,
            )
            return SPSubagentResult(
                status=SPSubagentStatus.COMPLETED,
                result="review",
                artifact_content=content,
                artifact_type="integration_review",
                artifact_metadata={
                    "artifact_type": "integration_review",
                    "report_task": "integrate_review",
                    "status": status,
                },
            )
        if report_task == "revise":
            self.revise_action_ids.append(task.action_id)
            packet = task.context_refs["report_packet"]
            tree = json.loads(packet["tree"]["content"])
            tree["version"] = int(tree.get("version") or 1) + 1
            tree["draft_dispositions"] = [
                {
                    "draft_id": "d1",
                    "node_id": "n2",
                    "action": "keep",
                    "reason": "fixed acceptance revision keeps the unaffected node",
                }
            ]
            origin = str(task.metadata.get("report_acceptance_origin") or "")
            if origin != "harness":
                inputs = packet.get("inputs") or []
                input_id = str(inputs[-1].get("input_id") or "")
                tree["user_decisions"] = [
                    {
                        "input_id": input_id,
                        "gap_ids": [],
                        "action": "apply_to_plan",
                        "reason": "explicit new user change",
                    }
                ]
            return SPSubagentResult(
                status=SPSubagentStatus.COMPLETED,
                result=json.dumps(tree, ensure_ascii=False),
                artifact_content=json.dumps(tree, ensure_ascii=False),
                artifact_type="outline",
                artifact_metadata={"report_id": task.metadata.get("report_id"), "tree_version": tree["version"]},
            )
        raise AssertionError(f"unexpected report task: {report_task}")


def _outputs():
    outputs = get_paths().sandbox_outputs_dir("t1", user_id=get_effective_user_id())
    if outputs.exists():
        shutil.rmtree(outputs)
    outputs.mkdir(parents=True, exist_ok=True)
    return outputs


def _invoke(monkeypatch, *, executor, model, state):
    import deerflow.agents.lead_agent.agent as lead_agent_module
    import deerflow.sp.runtime as sp_runtime

    monkeypatch.setattr(lead_agent_module, "create_chat_model", lambda **kwargs: model)
    monkeypatch.setattr(sp_runtime.DR2SPExecutorProvider, "__call__", lambda self, state, runtime: executor)
    graph = sp_runtime.make_sp_agent({"configurable": {"model_name": "fake-model"}}, app_config=_app_config())
    return graph.invoke(state, context={"thread_id": "t1", "run_id": "run1"})


def _ledger(state):
    from deerflow.sp.report import hooks
    from deerflow.sp.report.ledger import ReportLedger

    return ReportLedger.load_for_context(hooks.report_context(state))


def _state_without_messages():
    outputs = _outputs()
    ctx, ledger = _seed(outputs)
    state = dict(ctx.state)
    state["messages"] = []
    return state, ledger


def test_revision_is_reaccepted_and_delivered(monkeypatch):
    state, _ = _state_without_messages()
    executor = _FixedExecutor(["issues", "ready"])
    result = _invoke(monkeypatch, executor=executor, model=_NoCentralModel(), state=state)

    assert [task.metadata.get("report_task") for task in executor.tasks] == ["integrate_review", "revise", "integrate_review"]
    assert result["messages"][-1].content
    ledger = _ledger(state)
    assert ledger.payload["delivered"] is not None
    assert ledger.payload["acceptance"]["status"] == "delivered"
    assert ledger.payload["acceptance"]["review_count"] == 2
    assert ledger.payload["acceptance"]["content_revision_used"] is True
    assert executor.review_action_ids[0] != executor.review_action_ids[1]


def test_review_technical_failure_recovers_same_round(monkeypatch):
    state, _ = _state_without_messages()
    executor = _FixedExecutor(["technical", "ready"])
    result = _invoke(monkeypatch, executor=executor, model=_NoCentralModel(), state=state)

    assert len(executor.review_action_ids) == 2
    assert executor.review_action_ids[0] == executor.review_action_ids[1]
    assert result["messages"][-1].content
    ledger = _ledger(state)
    assert ledger.payload["delivered"] is not None
    assert ledger.payload["acceptance"]["technical_failure_count"] == 1
    assert ledger.payload["acceptance"]["review_count"] == 1


def test_finish_failure_retries_same_delivery(monkeypatch):
    state, _ = _state_without_messages()
    executor = _FixedExecutor(["ready"])
    import deerflow.sp.report as report_package

    original = report_package.deliver_report
    attempts = {"count": 0}

    def _flaky(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("simulated delivery failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(report_package, "deliver_report", _flaky)
    result = _invoke(monkeypatch, executor=executor, model=_NoCentralModel(), state=state)

    assert attempts["count"] == 2
    assert result["messages"][-1].content
    ledger = _ledger(state)
    assert ledger.payload["delivered"] is not None
    assert ledger.payload["acceptance"]["status"] == "delivered"


def _reach_blocked(monkeypatch, executor):
    state, _ = _state_without_messages()
    result = _invoke(monkeypatch, executor=executor, model=_NoCentralModel(), state=state)
    ledger = _ledger(result)
    assert ledger.payload["acceptance"]["status"] == "blocked_user"
    assert ledger.payload["delivered"] is None
    return result, executor


def test_ordinary_question_does_not_restart_acceptance(monkeypatch):
    executor = _FixedExecutor(["issues", "issues"])
    blocked_state, executor = _reach_blocked(monkeypatch, executor)
    task_count = len(executor.tasks)

    question_state = dict(blocked_state)
    question_state["messages"] = [
        *blocked_state["messages"],
        HumanMessage(content="报告里这个术语是什么意思？", id="user-question"),
    ]
    model = FakeToolCallingModel(responses=[AIMessage(content="这是解释，不修改报告。")])
    result = _invoke(monkeypatch, executor=executor, model=model, state=question_state)

    assert result["messages"][-1].content == "这是解释，不修改报告。"
    ledger = _ledger(result)
    assert ledger.payload["acceptance"]["status"] == "blocked_user"
    assert ledger.payload["acceptance"]["cycle_id"] == 0
    assert len(executor.tasks) == task_count


def test_explicit_new_cycle_uses_new_action_identity(monkeypatch):
    executor = _FixedExecutor(["issues", "issues"])
    blocked_state, executor = _reach_blocked(monkeypatch, executor)
    old_review_ids = list(executor.review_action_ids)
    executor.review_statuses.append("ready")

    changed_state = dict(blocked_state)
    changed_state["messages"] = [
        *blocked_state["messages"],
        HumanMessage(content="请调整第二部分的比较范围并补充材料。", id="user-change"),
    ]
    call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "sp_delegate",
                "args": {
                    "target_agent": "outline",
                    "task": "Apply the explicit user change to the plan",
                    "reason": "user requested a change",
                    "stage": "planning",
                    "metadata": {"report_task": "revise"},
                },
                "id": "call-apply-user-change",
                "type": "tool_call",
            }
        ],
    )
    model = FakeToolCallingModel(responses=[call, AIMessage(content="done")])
    result = _invoke(monkeypatch, executor=executor, model=model, state=changed_state)

    ledger = _ledger(result)
    assert ledger.payload["delivered"] is not None
    assert ledger.payload["acceptance"]["cycle_id"] == 1
    assert len(executor.review_action_ids) > len(old_review_ids)
    assert executor.review_action_ids[-1] not in old_review_ids
    assert executor.review_action_ids[-1].startswith("spact_sp-fixed-c1-review-r1")

