"""Ledger-owned writing: bounded sliding window, failure retention and dependencies."""

from copy import deepcopy
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace

from test_sp_report_writing import _context, _outline, _tree

from deerflow.sp.report import writer_pipeline
from deerflow.sp.report.hooks import report_context
from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus


def seeded(tmp_path, count=4):
    ctx = _context(tmp_path)
    tree = _tree()
    node = tree["nodes"][1]
    tree["nodes"] = [tree["nodes"][0]] + [dict(deepcopy(node), node_id=f"n{i + 2}", title=f"Section {i}") for i in range(count)]
    tree["write_requests"] = [{"key": f"w{i}", "node_ids": [f"n{i + 2}"], "feedback_wanted": False} for i in range(count)]
    assert _outline(ctx, tree).error is None
    return ctx


def test_refills_a_slot_before_slow_sibling_returns_and_never_loses_a_node(tmp_path):
    ctx = seeded(tmp_path)
    replacement_started = Event()
    lock = Lock()
    running = 0
    peak = 0
    seen = []

    class Executor:
        def execute(self, task):
            nonlocal running, peak
            key = task.metadata["report_write_key"]
            with lock:
                running += 1
                peak = max(peak, running)
                seen.append(key)
            ledger = ReportLedger.load_for_context(report_context(ctx.state))
            request = next(r for r in ledger.payload["write_requests"] if r["key"] == key)
            assert request["status"] == "in_flight"
            if key == "w0":
                assert replacement_started.wait(5)
            if key == "w2":
                replacement_started.set()
            virtual = task.metadata["report_draft_path"]
            path = ledger.paths.draft_file(task.metadata["report_node_id"], task.metadata["report_draft_id"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"Complete body for {key}.\n")
            with lock:
                running -= 1
            return SPSubagentResult(status=SPSubagentStatus.COMPLETED, artifact_type="node_draft", artifact_metadata={"completion_status": "complete", "node_id": task.metadata["report_node_id"], "draft_path": virtual})

    update = writer_pipeline.run_writer_pipeline(ctx.state, executor_provider=lambda state, runtime: Executor(), runtime=SimpleNamespace(context={"run_id": "run1", "thread_id": "t1"}), max_workers=2)
    ledger = ReportLedger.load_for_context(report_context(ctx.state))
    assert update and replacement_started.is_set()
    assert peak <= 2 and sorted(seen) == ["w0", "w1", "w2", "w3"]
    assert all(r["status"] == "completed" for r in ledger.payload["write_requests"])
    assert len([d for d in ledger.payload["drafts"] if d["is_current"]]) == 4


def test_failure_is_not_completed_or_retried_in_same_run(tmp_path):
    ctx = seeded(tmp_path, 1)
    calls = []

    class Executor:
        def execute(self, task):
            calls.append(task.action_id)
            return SPSubagentResult(status=SPSubagentStatus.FAILED, error="controlled failure")

    runtime = SimpleNamespace(context={"run_id": "run1", "thread_id": "t1"})
    for _ in range(2):
        writer_pipeline.run_writer_pipeline(ctx.state, executor_provider=lambda state, runtime: Executor(), runtime=runtime, max_workers=3)
    ledger = ReportLedger.load_for_context(report_context(ctx.state))
    assert len(calls) == 1
    assert ledger.payload["write_requests"][0]["status"] == "technical_failed"
    assert not ledger.payload["drafts"]


def test_dependency_and_write_last_wait_for_current_drafts(tmp_path):
    ctx = seeded(tmp_path, 3)
    ledger = ReportLedger.load_for_context(report_context(ctx.state))
    tree = __import__("json").loads(Path(ledger.payload["tree"]["path"]).read_text())
    tree["nodes"][2]["constraints"]["depends_on"] = ["n2"]
    tree["nodes"][3]["constraints"]["write_last"] = True
    first = writer_pipeline.eligible_requests(ledger, tree, run_id="run1")
    assert [r["key"] for r in first] == ["w0"]


def test_cancelled_pipeline_does_not_dispatch_and_ordinary_task_is_untouched(tmp_path):
    ctx = seeded(tmp_path, 2)
    stop = Event()
    stop.set()
    called = []

    def provider(state, runtime):
        return called.append(True)

    writer_pipeline.run_writer_pipeline(ctx.state, executor_provider=provider, runtime=SimpleNamespace(context={"run_id": "run1"}), max_workers=2, stop_event=stop)
    assert not called
    assert writer_pipeline.run_writer_pipeline({}, executor_provider=provider, runtime=SimpleNamespace(context={}), max_workers=2) is None
    assert not called


def test_pipeline_preserves_owner_context_and_enters_native_acceptance(tmp_path):
    from contextvars import ContextVar

    from deerflow.sp.report.context import SPReportContextMiddleware

    owner = ContextVar("pipeline_test_owner")
    owner.set("owner-a")
    ctx = seeded(tmp_path, 2)

    class Executor:
        def execute(self, task):
            assert owner.get() == "owner-a"
            ledger = ReportLedger.load_for_context(report_context(ctx.state))
            path = ledger.paths.draft_file(task.metadata["report_node_id"], task.metadata["report_draft_id"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Accepted body.\n")
            return SPSubagentResult(
                status=SPSubagentStatus.COMPLETED, artifact_type="node_draft", artifact_metadata={"completion_status": "complete", "node_id": task.metadata["report_node_id"], "draft_path": task.metadata["report_draft_path"]}
            )

    middleware = SPReportContextMiddleware(executor_provider=lambda state, runtime: Executor(), max_writers=2)
    update = middleware.before_model(ctx.state, SimpleNamespace(context={"run_id": "run1", "thread_id": "t1"}))
    assert update["jump_to"] == "tools"
    call = update["messages"][0].tool_calls[0]
    assert call["args"]["metadata"]["report_task"] == "integrate_review"
    assert update["messages"][0].additional_kwargs["stackplanner_generated"]


def test_duplicate_node_requests_are_not_sent_to_two_writers(tmp_path):
    ctx = seeded(tmp_path, 1)
    ledger = ReportLedger.load_for_context(report_context(ctx.state))
    tree = __import__("json").loads(Path(ledger.payload["tree"]["path"]).read_text())
    extra = deepcopy(ledger.payload["write_requests"][0])
    extra["key"] = "duplicate"
    ledger.payload["write_requests"].append(extra)
    assert writer_pipeline.eligible_requests(ledger, tree, run_id="run1") == []


def test_async_cancellation_stops_refilling_and_releases_inflight_nodes(tmp_path):
    import asyncio

    from deerflow.sp.report.context import SPReportContextMiddleware

    ctx = seeded(tmp_path, 5)
    started = Event()
    lock = Lock()
    calls = []

    def provider(state, runtime):
        class Executor:
            def execute(self, task):
                with lock:
                    calls.append(task.metadata["report_write_key"])
                    if len(calls) == 2:
                        started.set()
                abort = runtime.context["__run_abort_event"]
                import time

                deadline = time.monotonic() + 5
                while not abort.is_set() and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert abort.is_set()
                return SPSubagentResult(status=SPSubagentStatus.CANCELLED, error="cancelled by parent")

        return Executor()

    async def check():
        middleware = SPReportContextMiddleware(executor_provider=provider, max_writers=2)
        task = asyncio.create_task(middleware.abefore_model(ctx.state, SimpleNamespace(context={"run_id": "run1", "thread_id": "t1"})))
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(check())
    ledger = ReportLedger.load_for_context(report_context(ctx.state))
    assert len(calls) == 2
    assert not any(r["status"] == "in_flight" for r in ledger.payload["write_requests"])
    assert sum(r["status"] == "pending" for r in ledger.payload["write_requests"]) == 3


def test_parent_abort_does_not_schedule_review_or_another_central_turn(tmp_path):
    from deerflow.sp.report.context import SPReportContextMiddleware

    ctx = seeded(tmp_path, 1)
    abort = Event()
    abort.set()
    calls = []

    def provider(state, runtime):
        calls.append(True)

    middleware = SPReportContextMiddleware(executor_provider=provider, max_writers=2)
    result = middleware.before_model(ctx.state, SimpleNamespace(context={"run_id": "run1", "thread_id": "t1", "__run_abort_event": abort}))
    assert result["jump_to"] == "end" and not calls


def test_native_graph_drains_writers_and_delivers_without_a_central_writer_turn(tmp_path, monkeypatch):
    import json

    import test_sp_report_wiring as wiring
    from langchain_core.messages import AIMessage

    import deerflow.agents.lead_agent.agent as lead_module
    import deerflow.config.paths as paths_module
    import deerflow.sp.runtime as sp_runtime
    from deerflow.runtime.user_context import get_effective_user_id

    monkeypatch.setattr(paths_module, "_paths", paths_module.Paths(tmp_path / "native"))
    output = paths_module.get_paths().sandbox_outputs_dir("t1", user_id=get_effective_user_id())
    output.mkdir(parents=True, exist_ok=True)
    ctx = seeded(output, 3)
    # Production has already normalized the user-message identity before an
    # accepted tree exists. Seed the same identity in this resumed fixture.
    from datetime import datetime

    from deerflow.agents.middlewares.dynamic_context_middleware import DynamicContextMiddleware

    date = datetime.now().strftime("%Y-%m-%d, %A")
    ctx.state["messages"] = DynamicContextMiddleware._make_reminder_and_user_messages(ctx.state["messages"][0], "<system-reminder>date</system-reminder>", reminder_date=date)
    seeded_ledger = ReportLedger.load_for_context(report_context(ctx.state))
    seeded_ledger.inputs()[0]["source_message_id"] = ctx.state["messages"][-1].id
    seeded_ledger.save()
    calls = []

    class Executor:
        def execute(self, task):
            calls.append(task.metadata["report_role"])
            if task.metadata["report_role"] == "writer":
                ledger = ReportLedger.load_for_context(report_context(ctx.state))
                path = ledger.paths.draft_file(task.metadata["report_node_id"], task.metadata["report_draft_id"])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Complete body.\n")
                return SPSubagentResult(
                    status=SPSubagentStatus.COMPLETED, artifact_type="node_draft", artifact_metadata={"completion_status": "complete", "node_id": task.metadata["report_node_id"], "draft_path": task.metadata["report_draft_path"]}
                )
            packet = task.context_refs["report_packet"]
            review = {"inputs_digest": packet["inputs_digest"], "reviewed_candidate_sha256": packet["candidate_sha256"], "review": {"status": "ready", "outcome": "deliver", "findings": [], "edits": [], "covered_requirement_ids": ["R1"]}}
            return SPSubagentResult(status=SPSubagentStatus.COMPLETED, artifact_content=json.dumps(review), artifact_type="integration_review", artifact_metadata={"report_task": "integrate_review", "status": "ready"})

    root = wiring._model([AIMessage(content="Unexpected Central turn")])
    monkeypatch.setattr(lead_module, "create_chat_model", lambda **kwargs: root)
    monkeypatch.setattr(sp_runtime.DR2SPExecutorProvider, "__call__", lambda self, state, runtime: Executor())
    graph = sp_runtime.make_sp_agent({"configurable": {"model_name": "fake-model", "max_concurrent_subagents": 2}}, app_config=wiring._app_config())
    result = graph.invoke(ctx.state, context={"thread_id": "t1", "run_id": "native-writer-pipeline"})
    ledger = ReportLedger.load_for_context(report_context(result))
    assert ledger.unapplied_input_ids() == [], [{k: v for k, v in i.items() if k in {"input_id", "source", "source_message_id", "text"}} for i in ledger.inputs()]
    assert ledger.payload["status"] == "delivered", {
        "calls": calls,
        "root_turns": len(wiring._CAPTURED_REQUESTS),
        "pipeline": ledger.payload.get("writer_pipeline"),
        "unapplied": ledger.unapplied_input_ids(),
        "last": [str(getattr(m, "content", ""))[:500] for m in result["messages"][-4:]],
    }
    assert calls.count("writer") == 3 and calls.count("outline") == 1
    assert wiring._CAPTURED_REQUESTS == []
