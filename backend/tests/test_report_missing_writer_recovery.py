"""Missing write requests must recover without changing successful chapters."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from test_report_writer_pipeline import seeded

from deerflow.sp.report import store, writer_pipeline
from deerflow.sp.report.context import SPReportContextMiddleware, _writer_failure_action
from deerflow.sp.report.hooks import report_context
from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus


def _deferred(tmp_path):
    ctx = seeded(tmp_path, 2)
    ledger = ReportLedger.load_for_context(report_context(ctx.state))
    tree = json.loads(Path(ledger.payload["tree"]["path"]).read_text())
    tree["nodes"][-1]["status"] = "planned"
    tree["nodes"][-1]["constraints"]["write_after_body"] = "Write the synthesis after the body."
    tree["write_requests"] = tree["write_requests"][:1]
    path = Path(ledger.payload["tree"]["path"])
    text = json.dumps(tree)
    path.write_text(text)
    ledger.payload["tree"]["sha256"] = store.sha256_text(text)
    ledger.payload["write_requests"] = ledger.payload["write_requests"][:1]
    ledger.save()
    calls = []

    class Executor:
        def execute(self, task):
            calls.append(task.metadata["report_node_id"])
            ledger = ReportLedger.load_for_context(report_context(ctx.state))
            path = ledger.paths.draft_file(task.metadata["report_node_id"], task.metadata["report_draft_id"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("The complete accepted chapter.\n")
            return SPSubagentResult(
                status=SPSubagentStatus.COMPLETED, artifact_type="node_draft", artifact_metadata={"completion_status": "complete", "node_id": task.metadata["report_node_id"], "draft_path": task.metadata["report_draft_path"]}
            )

    runtime = SimpleNamespace(context={"run_id": "run1", "thread_id": "t1"})
    update = writer_pipeline.run_writer_pipeline(ctx.state, executor_provider=lambda state, runtime: Executor(), runtime=runtime, max_workers=2)
    assert update and calls == ["n2"]
    return ctx, runtime


def test_drained_queue_with_deferred_chapter_dispatches_only_native_outline_repair(tmp_path):
    ctx, runtime = _deferred(tmp_path)
    before = ReportLedger.load_for_context(report_context(ctx.state))
    draft = deepcopy(before.payload["drafts"][0])
    digest = hashlib.sha256(Path(draft["path"]).read_bytes()).hexdigest()
    tree = Path(before.payload["tree"]["path"]).read_bytes()
    update = SPReportContextMiddleware()._after_writing(ctx.state, None, runtime)
    assert update["jump_to"] == "tools"
    call = update["messages"][0].tool_calls[0]
    assert call["name"] == "sp_delegate"
    assert call["args"]["target_agent"] == "outline"
    metadata = call["args"]["metadata"]
    assert metadata["report_task"] == "revise"
    assert metadata["report_writer_missing_node_ids"] == ["n3"]
    assert metadata["report_writer_failure_keys"] == []
    assert "keep" in call["args"]["task"]
    after = ReportLedger.load_for_context(report_context(ctx.state))
    assert after.payload["drafts"][0] == draft
    assert hashlib.sha256(Path(draft["path"]).read_bytes()).hexdigest() == digest
    assert Path(after.payload["tree"]["path"]).read_bytes() == tree
    assert after.payload["writer_recovery"]["missing_node_ids"] == ["n3"]


def test_missing_chapter_gets_one_repair_then_explicit_handoff(tmp_path):
    ctx, _ = _deferred(tmp_path)
    first = _writer_failure_action(ctx.state, "run1")
    assert first.tool_calls[0]["name"] == "sp_delegate"
    second = _writer_failure_action(ctx.state, "run1")
    assert second.tool_calls[0]["name"] == "sp_ask_human"
    assert "n3" in second.tool_calls[0]["args"]["question"]
    assert _writer_failure_action(ctx.state, "run1") is None


@pytest.mark.parametrize("blocker", ["pending", "in_flight", "gap", "research", "input", "outline_review", "draft_review", "feedback"])
def test_active_work_or_native_review_prevents_premature_missing_repair(tmp_path, blocker):
    ctx, _ = _deferred(tmp_path)
    ledger = ReportLedger.load_for_context(report_context(ctx.state))
    if blocker in {"pending", "in_flight"}:
        ledger.payload["write_requests"].append({"key": "later", "node_ids": ["n3"], "status": blocker})
    elif blocker == "gap":
        ledger.payload["gaps"].append({"gap_id": "g1", "status": "open", "node_ids": ["n3"]})
    elif blocker == "research":
        ledger.payload["actions"]["research"] = {"action_id": "research", "status": "in_flight"}
    elif blocker == "input":
        ledger.add_user_input("A new required comparison", source="hitl_feedback", source_message_id="later-user-input")
    elif blocker == "outline_review":
        ledger.payload["outline_review_due"] = True
    elif blocker == "draft_review":
        ledger.payload["drafts"][0]["review_status"] = "open"
    else:
        ledger.payload["feedback"].append({"feedback_id": "f1", "status": "open", "node_id": "n3"})
    ledger.save()
    assert _writer_failure_action(ctx.state, "run1") is None
    assert "writer_recovery" not in ReportLedger.load_for_context(report_context(ctx.state)).payload


def test_complete_body_does_not_add_repair_or_model_call(tmp_path):
    ctx, _ = _deferred(tmp_path)
    ledger = ReportLedger.load_for_context(report_context(ctx.state))
    copy = deepcopy(ledger.payload["drafts"][0])
    copy.update(node_id="n3", draft_id="d-complete")
    ledger.payload["drafts"].append(copy)
    ledger.save()
    assert _writer_failure_action(ctx.state, "run1") is None
    assert "writer_recovery" not in ReportLedger.load_for_context(report_context(ctx.state)).payload


@pytest.mark.parametrize("state", [{}, {"thread_data": {}}])
def test_ordinary_non_report_task_is_unchanged(state):
    assert _writer_failure_action(state, "run1") is None


def test_unmanaged_report_and_corrupt_tree_do_not_trigger_missing_repair(tmp_path):
    ctx, _ = _deferred(tmp_path)
    ledger = ReportLedger.load_for_context(report_context(ctx.state))
    ledger.payload.pop("writer_pipeline", None)
    ledger.save()
    assert _writer_failure_action(ctx.state, "run1") is None
    ledger.payload["writer_pipeline"] = {"managed": True}
    ledger.save()
    Path(ledger.payload["tree"]["path"]).write_text("{}")
    assert _writer_failure_action(ctx.state, "run1") is None


def test_parent_cancellation_never_dispatches_missing_repair(tmp_path):
    ctx, runtime = _deferred(tmp_path)
    abort = Event()
    abort.set()
    runtime.context["__run_abort_event"] = abort
    update = SPReportContextMiddleware()._after_writing(ctx.state, None, runtime)
    assert update["jump_to"] == "end"
    assert "writer_recovery" not in ReportLedger.load_for_context(report_context(ctx.state)).payload


def test_native_graph_repairs_only_missing_chapter_then_reviews_and_delivers(tmp_path, monkeypatch):
    from datetime import datetime

    import test_sp_report_wiring as wiring
    from langchain_core.messages import AIMessage

    import deerflow.agents.lead_agent.agent as lead_module
    import deerflow.config.paths as paths_module
    import deerflow.sp.runtime as sp_runtime
    from deerflow.agents.middlewares.dynamic_context_middleware import DynamicContextMiddleware
    from deerflow.runtime.user_context import get_effective_user_id

    monkeypatch.setattr(paths_module, "_paths", paths_module.Paths(tmp_path / "native"))
    outputs = paths_module.get_paths().sandbox_outputs_dir("t1", user_id=get_effective_user_id())
    outputs.mkdir(parents=True, exist_ok=True)
    ctx, _ = _deferred(outputs)
    date = datetime.now().strftime("%Y-%m-%d, %A")
    ctx.state["messages"] = DynamicContextMiddleware._make_reminder_and_user_messages(ctx.state["messages"][0], "<system-reminder>date</system-reminder>", reminder_date=date)
    ledger = ReportLedger.load_for_context(report_context(ctx.state))
    ledger.inputs()[0]["source_message_id"] = ctx.state["messages"][-1].id
    ledger.save()
    original = ledger.payload["drafts"][0]
    original_bytes = Path(original["path"]).read_bytes()
    original_tree = json.loads(Path(ledger.payload["tree"]["path"]).read_text())
    calls = []

    class Executor:
        def execute(self, task):
            role = task.metadata["report_role"]
            calls.append((role, task.metadata.get("report_task"), task.metadata.get("report_node_id")))
            ledger = ReportLedger.load_for_context(report_context(ctx.state))
            if role == "writer":
                assert task.metadata["report_node_id"] == "n3"
                path = ledger.paths.draft_file("n3", task.metadata["report_draft_id"])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("The complete deferred synthesis.\n")
                return SPSubagentResult(status=SPSubagentStatus.COMPLETED, artifact_type="node_draft", artifact_metadata={"completion_status": "complete", "node_id": "n3", "draft_path": task.metadata["report_draft_path"]})
            if task.metadata.get("report_task") == "revise":
                proposal = json.loads(Path(ledger.payload["tree"]["path"]).read_text())
                proposal["nodes"][-1]["status"] = "ready"
                proposal["write_requests"] = [{"key": "w-missing", "node_ids": ["n3"], "feedback_wanted": False}]
                proposal["draft_dispositions"] = [{"node_id": "n2", "draft_id": original["draft_id"], "action": "keep", "reason": "Already complete; only schedule missing synthesis"}]
                return SPSubagentResult(status=SPSubagentStatus.COMPLETED, artifact_content=json.dumps(proposal), artifact_type="outline", artifact_metadata={"completion_status": "complete"})
            packet = task.context_refs["report_packet"]
            review = {"inputs_digest": packet["inputs_digest"], "reviewed_candidate_sha256": packet["candidate_sha256"], "review": {"status": "ready", "outcome": "deliver", "findings": [], "edits": [], "covered_requirement_ids": ["R1"]}}
            return SPSubagentResult(status=SPSubagentStatus.COMPLETED, artifact_content=json.dumps(review), artifact_type="integration_review", artifact_metadata={"report_task": "integrate_review", "status": "ready"})

    root = wiring._model([AIMessage(content="Unexpected Central turn")])
    monkeypatch.setattr(lead_module, "create_chat_model", lambda **kwargs: root)
    monkeypatch.setattr(sp_runtime.DR2SPExecutorProvider, "__call__", lambda self, state, runtime: Executor())
    graph = sp_runtime.make_sp_agent({"configurable": {"model_name": "fake-model", "max_concurrent_subagents": 2}}, app_config=wiring._app_config())
    result = graph.invoke(ctx.state, context={"thread_id": "t1", "run_id": "missing-writer-native"})
    ledger = ReportLedger.load_for_context(report_context(result))
    assert ledger.payload["status"] == "delivered", {"calls": calls, "last": [str(m.content)[:500] for m in result["messages"][-3:]]}
    assert calls == [("outline", "revise", None), ("writer", None, "n3"), ("outline", "integrate_review", None)]
    assert wiring._CAPTURED_REQUESTS == []
    assert Path(original["path"]).read_bytes() == original_bytes
    accepted = json.loads(Path(ledger.payload["tree"]["path"]).read_text())
    assert accepted["nodes"][1] == original_tree["nodes"][1]
    assert ledger.payload["writer_recovery"]["attempts"] == 1
