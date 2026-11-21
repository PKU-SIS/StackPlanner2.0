"""Ledger-driven sliding-window Writer execution for report mode only."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import copy_context
from copy import deepcopy
from threading import Event

from deerflow.sp.report import packets, store, writing
from deerflow.sp.report.ledger import ReportLedger


def _dependency_nodes(node, nodes):
    constraints = node.get("constraints") or {}
    raw = constraints.get("depends_on")
    values = raw if isinstance(raw, list) else [raw] if raw else []
    found = set()
    for value in values:
        text = str(value)
        found.update(key for key in nodes if re.search(r"(?<![A-Za-z0-9_-])" + re.escape(key) + r"(?![A-Za-z0-9_-])", text))
    return found, bool(constraints.get("write_last") or raw and not found)


def eligible_requests(ledger, tree, *, run_id, active_nodes=(), attempted_keys=()):
    nodes = {n["node_id"]: n for n in tree.get("nodes", []) if n.get("kind") == "writable"}
    current = {d["node_id"] for d in writing.drafts(ledger) if d.get("is_current")}
    pending_nodes = {n for r in writing.write_requests(ledger) if r.get("status") not in {"completed", "superseded"} for n in r.get("node_ids", [])}
    done = current - pending_nodes
    late = {key for key, node in nodes.items() if _dependency_nodes(node, nodes)[1]}
    occupied = set(active_nodes) | {n for r in writing.write_requests(ledger) if r.get("status") == "in_flight" for n in r.get("node_ids", [])}
    counts = Counter(n for r in writing.write_requests(ledger) if r.get("status") in {"pending", "technical_failed", "in_flight"} for n in r.get("node_ids", []))
    marker = ledger.payload.get("writer_pipeline") or {}
    reserved = {(r["write_key"], r["round"]) for r in marker.get("attempted_rounds", [])} if marker.get("run_id") == run_id else set()
    result = []
    for request in writing.write_requests(ledger):
        ids = request.get("node_ids") or []
        key = request.get("key")
        if len(ids) != 1 or ids[0] not in nodes or ids[0] in occupied or counts[ids[0]] > 1 or key in attempted_keys:
            continue
        if request.get("status") not in {"pending", "technical_failed"} or nodes[ids[0]].get("status") != "ready":
            continue
        # Failure is retained, not silently retried by every Central turn. A
        # new run or an Outline-approved next writing round may retry it.
        round_number = int(request.get("round") or 1)
        if (key, round_number) in reserved:
            continue
        if any(a.get("run_id") == run_id and int(a.get("round") or 1) == round_number for a in request.get("attempts") or []):
            continue
        dependencies, is_late = _dependency_nodes(nodes[ids[0]], nodes)
        if dependencies - done:
            continue
        if is_late and (set(nodes) - late) - done:
            continue
        occupied.add(ids[0])
        result.append(request)
    return result


def _phase_ready(ledger):
    summary = ledger.control_summary()
    return bool(ledger.payload.get("tree")) and not any(summary.get(k) for k in ("gaps", "unapplied_input_ids", "research_actions_in_flight", "draft_review_due", "feedback_open", "outline_review_due"))


def _merge_state(state, update):
    from deerflow.agents.thread_state import merge_artifacts, merge_sp_artifact_refs, merge_sp_task_memory

    reducers = {"artifacts": merge_artifacts, "sp_current_artifact_refs": merge_sp_artifact_refs, "sp_task_memory": merge_sp_task_memory}
    for key, value in update.items():
        state[key] = reducers[key](state.get(key), value) if key in reducers else value


def run_writer_pipeline(state, *, executor_provider, runtime, max_workers=3, stop_event=None):
    from deerflow.sp.report.hooks import report_context

    context = report_context(state)
    if not context.is_active:
        return None
    paths = store.ReportPaths(context.outputs_path, context.report_id)
    max_workers = max(1, int(max_workers))
    stop = stop_event if stop_event is not None else Event()
    if stop.is_set():
        return None
    runtime_context = getattr(runtime, "context", None)
    runtime_context = runtime_context if isinstance(runtime_context, Mapping) else {}
    run_id = str(runtime_context.get("run_id") or "unscoped-writer-pipeline")
    thread_id = runtime_context.get("thread_id")
    with store.report_lock(paths):
        ledger = ReportLedger.load(paths)
        marker = ledger.payload.setdefault("writer_pipeline", {})
        if marker.get("run_id") != run_id:
            marker.update(run_id=run_id, attempted_rounds=[])
        marker.update(managed=True, max_concurrent=max_workers)
        ledger.save()
        if not _phase_ready(ledger):
            marker["status"] = "waiting_for_outline_or_inputs"
            ledger.save()
            return None
        tree = packets.load_current_tree(ledger)
        if not eligible_requests(ledger, tree, run_id=run_id):
            unfinished = [r for r in writing.write_requests(ledger) if r.get("status") not in {"completed", "superseded"}]
            marker["status"] = "needs_outline_or_technical_recovery" if unfinished else "returned"
            ledger.save()
            return None
        marker["status"] = "running"
        ledger.save()
        version = ledger.payload["tree"]["version"]
    parent_abort = runtime_context.get("__run_abort_event")

    class Abort:
        def is_set(self):
            return stop.is_set() or bool(parent_abort is not None and parent_abort.is_set())

    abort = Abort()

    class RuntimeProxy:
        context = {**runtime_context, "__run_abort_event": abort}

        def __getattr__(self, key):
            return getattr(runtime, key)

    if abort.is_set():
        return None
    proxy = RuntimeProxy()
    shared = deepcopy(dict(state))
    initial = deepcopy(shared)
    executor = executor_provider(shared, proxy)
    from deerflow.sp.actions.handlers.base import HandlerContext
    from deerflow.sp.actions.handlers.delegate import DelegateHandler
    from deerflow.sp.actions.schema import ActionType, SPAction
    from deerflow.sp.agent_tools import SPControlActionMiddleware
    from deerflow.sp.memory import TaskMemoryStack

    attempted = set()
    active = {}
    outcomes = []

    def execute(request, snapshot):
        if abort.is_set():
            return None
        action = SPAction.create(
            ActionType.DELEGATE,
            target_agent="reporter",
            reason="Host ledger-driven writing pipeline",
            task="Write the assigned node body using the authoritative report packet. Save the complete draft at the host-assigned path and return the normal Writer result. Preserve canonical source IDs.",
            stage="reporting",
            expected_output="One complete accepted node draft",
            metadata={"report_write_key": request["key"], "report_writer_pipeline": True},
        )
        ctx = HandlerContext(state=snapshot, stack=TaskMemoryStack.from_dict(snapshot.get("sp_task_memory"), thread_id=thread_id, run_id=run_id), thread_id=thread_id, run_id=run_id)
        try:
            result = DelegateHandler(executor=executor).handle(action, ctx)
        except Exception as exc:
            from deerflow.sp.actions.schema import HandlerResult
            from deerflow.sp.report.hooks import fail_delegation_action
            from deerflow.sp.subagents import SPSubagentTask

            error = f"{type(exc).__name__}: {exc}"
            task = SPSubagentTask(action_id=action.action_id, subagent_type="reporter", task=action.task, description="Writer pipeline", metadata={"report_role": "writer", "report_write_key": request["key"]})
            fail_delegation_action(snapshot, action.action_id, error, task=task)
            result = HandlerResult(next_step="error_recoverable", error=error)

        result.state_update["sp_task_memory"] = ctx.stack.to_dict()
        SPControlActionMiddleware._record_events(proxy, result.run_events)
        return result

    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
        while True:
            if not abort.is_set():
                with store.report_lock(paths):
                    ledger = ReportLedger.load(paths)
                    if ledger.payload["tree"]["version"] == version and _phase_ready(ledger):
                        tree = packets.load_current_tree(ledger)
                        occupied = {request["node_ids"][0] for request in active.values()}
                        for request in eligible_requests(ledger, tree, run_id=run_id, active_nodes=occupied, attempted_keys=attempted):
                            if len(active) >= max_workers:
                                break
                            attempted.add(request["key"])
                            ledger.payload["writer_pipeline"]["attempted_rounds"].append({"write_key": request["key"], "round": int(request.get("round") or 1)})
                            ledger.save()
                            active[pool.submit(copy_context().run, execute, deepcopy(request), deepcopy(shared))] = request
            if not active:
                break
            completed, _ = wait(active, timeout=0.1, return_when=FIRST_COMPLETED)
            for future in completed:
                request = active.pop(future)
                result = future.result()
                if result is not None:
                    _merge_state(shared, result.state_update)
                    outcomes.append({"write_key": request["key"], "error": result.error, "next_step": result.next_step})
    with store.report_lock(paths):
        ledger = ReportLedger.load(paths)
        ledger.payload.setdefault("writer_pipeline", {}).update(last_run_id=run_id, last_outcomes=outcomes, status="cancelled" if abort.is_set() else "technical_failed" if any(o["error"] for o in outcomes) else "returned")
        ledger.save()
    if not outcomes:
        return None
    shared["sp_current_stage"] = "reporting"
    return {key: value for key, value in shared.items() if key not in {"messages", "sp_task_memory"} and value != initial.get(key)} | ({"sp_task_memory": shared["sp_task_memory"]} if "sp_task_memory" in shared else {})
