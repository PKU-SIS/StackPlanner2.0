"""Native node writing, fixed acceptance and delivery; explicit billing cutoff."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from evaluate_report_outline import ROOT, dump, rebase_paths


def can_dispatch(now, deadline):
    return now < deadline - 60


def writing_waves(requests, tree):
    nodes = {n["node_id"]: n for n in tree.get("nodes", [])}
    ordinary, late = [], []
    for request in requests:
        constraints = nodes.get(request["node_ids"][0], {}).get("constraints") or {}
        (late if constraints.get("write_last") or constraints.get("depends_on") else ordinary).append(request)
    return [wave for wave in [ordinary, late] if wave]


def accepted_writer_input(metrics, ledger, tree):
    writable_ids = {n["node_id"] for n in tree.get("nodes", []) if n.get("kind") == "writable"}
    current_ids = {d["node_id"] for d in ledger.get("drafts", []) if d.get("is_current")}
    resume = metrics.get("outcome") in {"acceptance_error", "cutoff", "incomplete"} and bool(writable_ids) and writable_ids <= current_ids
    accepted = metrics.get("consolidation_ok") or metrics.get("accepted") or resume
    if not accepted or any(g.get("status") not in {"resolved", "not_needed"} for g in ledger.get("gaps", [])):
        return False
    writable = [n for n in tree.get("nodes", []) if n.get("kind") == "writable"]
    covered = {node for r in ledger.get("write_requests", []) if r.get("status") in {"pending", "technical_failed", "completed"} for node in r.get("node_ids", [])}
    return bool(writable) and all(n.get("status") == "ready" and n["node_id"] in covered for n in writable)


def worker(directory, origin, concurrency, deadline):
    import yaml
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    os.environ.update(DEER_FLOW_PROJECT_ROOT=str(ROOT), DEER_FLOW_HOME=str(directory / "runtime"))
    for key in ("NO_PROXY", "no_proxy"):
        os.environ[key] = os.environ.get(key, "") + ",localhost,127.0.0.1,api.deepseek.com"
    config = yaml.safe_load((ROOT / "config.yaml").read_text())
    config["database"] = {"backend": "sqlite", "sqlite_dir": str(directory / "runtime/db")}
    config_file = directory / "config.yaml"
    config_file.write_text(yaml.safe_dump(config, sort_keys=False))
    os.environ["DEER_FLOW_CONFIG_PATH"] = str(config_file)
    from copy import deepcopy
    from dataclasses import replace
    from types import SimpleNamespace

    from langchain_core.messages import HumanMessage

    from deerflow.agents.thread_state import merge_artifacts, merge_sp_artifact_refs, merge_sp_task_memory
    from deerflow.config import get_app_config
    from deerflow.config.paths import get_paths
    from deerflow.runtime.user_context import set_current_user
    from deerflow.sp.actions.handlers.base import HandlerContext
    from deerflow.sp.actions.handlers.delegate import DelegateHandler
    from deerflow.sp.actions.handlers.finish import FinishHandler
    from deerflow.sp.actions.schema import ActionType, SPAction
    from deerflow.sp.memory import TaskMemoryStack
    from deerflow.sp.report.acceptance import next_acceptance_action
    from deerflow.sp.report.hooks import configure_report_subagent
    from deerflow.sp.runtime import app_config_for_report_outline
    from deerflow.sp.subagents import DR2SubagentExecutorAdapter
    from deerflow.subagents import get_subagent_config
    from deerflow.subagents.executor import SubagentExecutor
    from deerflow.tools.tools import get_available_tools

    set_current_user(SimpleNamespace(id="stage-eval", role="admin"))
    query = json.loads((origin / "query.json").read_text())
    initial = json.loads((origin / "ledger.json").read_text())
    source_tree = json.loads(Path(initial["tree"]["path"]).read_text())
    if not accepted_writer_input(json.loads((origin / "metrics.json").read_text()), initial, source_tree):
        raise ValueError("blocked_upstream: accepted ready tree with complete writing requests and no active gaps required")
    thread_id = f"pre-{query['id']}"
    paths = get_paths()
    outputs = paths.sandbox_outputs_dir(thread_id, user_id="stage-eval")
    old_root = Path(initial["request"]["path"]).parents[3]
    for item in [initial["tree"], *initial["materials"], *initial["summaries"], *[d for d in initial.get("drafts", []) if d.get("is_current")]]:
        f = Path(item["path"])
        if not f.resolve().is_relative_to(old_root.resolve()) or hashlib.sha256(f.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("blocked_upstream: artifact missing or changed")
    shutil.copytree(old_root, outputs.parent)
    report = outputs / "report" / initial["report_id"]
    for f in outputs.parent.rglob("*.json"):
        if f.is_relative_to(report / "tree") or f.is_relative_to(report / "outline"):
            continue
        dump(f, rebase_paths(json.loads(f.read_text()), old_root, outputs.parent))
    checkpoint = rebase_paths(json.loads((origin / "checkpoint.json").read_text()), old_root, outputs.parent)
    state = {
        "messages": [HumanMessage(content=query["prompt"], id="original-query")],
        "thread_data": dict(outputs_path=str(outputs), workspace_path=str(paths.sandbox_work_dir(thread_id, user_id="stage-eval")), uploads_path=str(paths.sandbox_uploads_dir(thread_id, user_id="stage-eval"))),
    }
    state.update(checkpoint["state_update"])
    memory = checkpoint["memory"]
    ledger_file = report / "ledger.json"
    app = get_app_config()
    tools = get_available_tools(model_name=app.models[0].name, app_config=app)
    abort = threading.Event()
    timer = threading.Timer(max(0, deadline - 35 - time.time()), abort.set)
    timer.daemon = True
    timer.start()
    records = []
    record_lock = threading.Lock()
    merge_lock = threading.Lock()
    sequence = 0

    def load():
        return json.loads(ledger_file.read_text())

    def execute(action, label):
        nonlocal sequence, memory
        set_current_user(SimpleNamespace(id="stage-eval", role="admin"))
        if not can_dispatch(time.time(), deadline) or abort.is_set():
            raise TimeoutError("billing_cutoff_no_new_dispatch")
        with record_lock:
            sequence += 1
            number = sequence
        with merge_lock:
            local = deepcopy(state)
            stack = TaskMemoryStack.from_dict(deepcopy(memory), thread_id=thread_id)
        raws = []

        def observe(step, index):
            public = {k: step[k] for k in ("type", "name", "status", "tool_call_id", "content", "tool_calls", "invalid_tool_calls", "usage_metadata") if k in step}
            with record_lock:
                with (directory / "steps.jsonl").open("a") as stream:
                    stream.write(json.dumps(dict(execution=number, label=label, index=index, **public), ensure_ascii=False, default=str) + "\n")

        def factory(task):
            if not can_dispatch(time.time(), deadline) or abort.is_set():
                raise TimeoutError("billing_cutoff_no_new_model")
            writer = task.metadata.get("report_role") == "writer"
            cfg, scoped, role = configure_report_subagent(task, state=local, available_tools=tools, subagent_config=get_subagent_config("sp-reporter" if writer else "sp-outline", app_config=app))
            cfg = replace(cfg, max_turns=350, tool_call_limit=50)
            limit = 16384 if writer else 32768
            dump(directory / f"task-{number}.json", dict(label=label, metadata=task.metadata, context_refs=task.context_refs, max_turns=cfg.max_turns, tool_call_limit=cfg.tool_call_limit))
            return SubagentExecutor(
                config=cfg,
                tools=scoped,
                app_config=app if writer else app_config_for_report_outline(app, task),
                parent_model=app.models[0].name,
                max_tokens_per_step=limit,
                thread_data=local["thread_data"],
                thread_id=thread_id,
                run_id=f"writer-eval-{query['id']}",
                user_id="stage-eval",
                user_scoped_skills=True,
                thinking_enabled=False,
                step_observer=observe,
                parent_abort_event=abort,
                force_isolated_loop=True,
            )

        context = HandlerContext(state=local, stack=stack, thread_id=thread_id, run_id=f"writer-eval-{query['id']}")
        result = FinishHandler().handle(action, context) if action.action_type == ActionType.FINISH else DelegateHandler(executor=DR2SubagentExecutorAdapter(factory, result_observer=raws.append)).handle(action, context)
        dump(directory / f"handler-{number}.json", result.to_dict())
        row = dict(execution=number, label=label, action_id=action.action_id, error=result.error, next_step=result.next_step, stop_reasons=[r.stop_reason for r in raws], usage=[u for r in raws for u in r.token_usage_records])
        with record_lock:
            records.append(row)
            dump(directory / "executions.json", records)
        with merge_lock:
            reducers = {"artifacts": merge_artifacts, "sp_current_artifact_refs": merge_sp_artifact_refs, "sp_task_memory": merge_sp_task_memory}
            for key, value in result.state_update.items():
                state[key] = reducers[key](state.get(key), value) if key in reducers else value
            memory = merge_sp_task_memory(memory, stack.to_dict()) or memory
            state["sp_last_handler_result"] = result.to_dict()
            dump(directory / "checkpoint.json", dict(state_update={k: v for k, v in state.items() if k not in {"messages", "thread_data"}}, memory=memory))
        return row

    def write(request):
        key = request["key"]
        last = None
        for attempt in range(2):
            if abort.is_set() or not can_dispatch(time.time(), deadline):
                break
            current = next((r for r in load()["write_requests"] if r["key"] == key), {})
            if current.get("status") not in {"pending", "technical_failed"}:
                break
            action = SPAction.create(
                ActionType.DELEGATE,
                target_agent="reporter",
                task="Write the assigned node body from the authoritative report packet and saved evidence. "
                "Follow the node specification and canonical citations, save the complete body at the host-assigned draft path, "
                "then return the normal delivery envelope. Do not search or redesign the Outline.",
                reason="Isolated native Writer stage evaluation",
                stage="reporting",
                expected_output="One complete accepted node draft saved at the assigned path",
                metadata={"report_write_key": key},
            )
            last = execute(action, "write:" + key)
            if not last["error"]:
                break
        return last

    outcome = "incomplete"
    failure = None
    try:
        for cycle in range(4):
            ledger = load()
            tree = json.loads(Path(ledger["tree"]["path"]).read_text())
            pending = [r for r in ledger["write_requests"] if r.get("status") in {"pending", "technical_failed"}]
            for wave in writing_waves(pending, tree):
                with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                    futures = [pool.submit(write, r) for r in wave]
                    for future in concurrent.futures.as_completed(futures):
                        future.result()
            # Use the existing acceptance gate; do not invent a review/delivery shortcut.
            for gate_step in range(6):
                if abort.is_set() or not can_dispatch(time.time(), deadline):
                    raise TimeoutError("billing_cutoff")
                message = next_acceptance_action(state)
                if message is None:
                    break
                call = message.tool_calls[0]
                args = call["args"]
                if call["name"] == "sp_ask_human":
                    outcome = "awaiting_user"
                    break
                if call["name"] == "sp_finish":
                    action = SPAction.create(ActionType.FINISH, action_id="spact_" + call["id"], reason="Native fixed acceptance delivery", task="Deliver current accepted report", metadata={})
                else:
                    action = SPAction.create(
                        ActionType.DELEGATE,
                        action_id="spact_" + call["id"],
                        reason=args["reason"],
                        task=args["task"],
                        target_agent=args["target_agent"],
                        stage=args.get("stage"),
                        expected_output=args.get("expected_output"),
                        metadata=args.get("metadata") or {},
                    )
                result = execute(action, "acceptance:" + call["name"])
                if load().get("delivered"):
                    outcome = "delivered"
                    break
                if result["error"]:
                    outcome = "acceptance_error"
                    break
                if any(r.get("status") in {"pending", "technical_failed"} for r in load()["write_requests"]):
                    break
            if outcome in {"delivered", "awaiting_user", "acceptance_error"}:
                break
            if not any(r.get("status") in {"pending", "technical_failed"} for r in load()["write_requests"]):
                break
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
        outcome = "cutoff" if isinstance(exc, TimeoutError) else "execution_error"
    finally:
        timer.cancel()
    ledger = load()
    dump(directory / "ledger.json", ledger)
    tree = json.loads(Path(ledger["tree"]["path"]).read_text())
    writable = {n["node_id"] for n in tree["nodes"] if n.get("kind") == "writable"}
    drafts = {d["node_id"]: d for d in ledger["drafts"] if d.get("is_current")}
    bad = []
    citations = set()
    for node, draft in drafts.items():
        f = Path(draft["path"])
        text = f.read_text() if f.exists() else ""
        if not text.strip() or hashlib.sha256(text.encode()).hexdigest() != draft.get("sha256"):
            bad.append(node)
        citations.update(re.findall(r"\[(S\d+)\]", text))
    final = ledger.get("final_report")
    final_ok = False
    if final and Path(final["path"]).exists():
        text = Path(final["path"]).read_text()
        (directory / "final-report.md").write_text(text)
        final_ok = hashlib.sha256(text.encode()).hexdigest() == final["sha256"]
    dump(
        directory / "metrics.json",
        dict(
            id=query["id"],
            outcome=outcome,
            error=failure,
            delivered=bool(ledger.get("delivered")),
            final_integrity_ok=final_ok,
            writable_count=len(writable),
            current_drafts=len(drafts),
            missing_nodes=sorted(writable - set(drafts)),
            invalid_drafts=bad,
            unique_cited_sources=len(citations),
            acceptance=ledger.get("acceptance"),
            records=[{k: v for k, v in r.items() if k != "usage"} for r in records],
            overall_pass=bool(ledger.get("delivered")) and final_ok and not (writable - set(drafts)) and not bad,
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--origins", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--deadline", default="2026-11-02T01:00:00+00:00")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--origin", type=Path)
    args = parser.parse_args()
    deadline = datetime.fromisoformat(args.deadline).timestamp()
    output = args.output.resolve()
    if args.worker:
        worker(output, args.origin.resolve(), args.concurrency, deadline)
        return
    output.mkdir(parents=True, exist_ok=False)
    origins = json.loads(args.origins.read_text())
    dump(
        output / "manifest.json",
        dict(
            origins=origins,
            writer_concurrency_per_report=args.concurrency,
            reports_concurrent=len(origins),
            deadline=args.deadline,
            abort_before_seconds=35,
            hard_stop_before_seconds=10,
            scope="Writer, native fixed acceptance and delivery; no Central or research",
        ),
    )
    hashes = {}
    for prefix in ("backend/packages/harness/deerflow", "backend/scripts", "skills/public/report-node-writing", "skills/public/report-outline"):
        for f in (ROOT / prefix).rglob("*"):
            if f.suffix in {".py", ".md"}:
                hashes[str(f.relative_to(ROOT))] = hashlib.sha256(f.read_bytes()).hexdigest()
    dump(output / "source-hashes.json", hashes)

    def run(item):
        i = item["id"]
        d = output / str(i)
        d.mkdir()
        origin = (ROOT / item["origin"]).resolve()
        for name in ("query.json", "request.md"):
            shutil.copyfile(origin / name, d / name)
        with (d / "worker.log").open("w") as log:
            command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--output", str(d), "--origins", str(args.origins.resolve()), "--origin", str(origin), "--concurrency", str(args.concurrency), "--deadline", args.deadline]
            p = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            dump(d / "process.json", dict(pid=p.pid, deadline=deadline))
            try:
                p.wait(timeout=max(1, deadline - 10 - time.time()))
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGTERM)
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait()
            if not (d / "metrics.json").exists():
                dump(d / "metrics.json", dict(id=i, overall_pass=False, outcome="cutoff" if time.time() > deadline - 15 else "worker_error", error=f"worker_exit_{p.returncode}"))
        return json.loads((d / "metrics.json").read_text())

    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(origins)) as pool:
        for future in concurrent.futures.as_completed([pool.submit(run, item) for item in origins]):
            row = future.result()
            rows.append(row)
            dump(output / "results.json", rows)
            lines = ["# Writer 至最终交付测试", "", "| ID | 结果 | 草稿／节点 | 来源数 | 最终报告 |", "|---|---|---|---|---|"]
            for r in sorted(rows, key=lambda x: x["id"]):
                lines.append(f"| {r['id']} | {r.get('outcome')} | {r.get('current_drafts')}/{r.get('writable_count')} | {r.get('unique_cited_sources')} | [查看]({r['id']}/final-report.md) |")
            (output / "REPORT.md").write_text("\n".join(lines) + "\n")
            print(json.dumps({k: row.get(k) for k in ("id", "outcome", "overall_pass", "current_drafts", "writable_count", "error")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
