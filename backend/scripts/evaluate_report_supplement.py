"""Native gap research and Outline revision; reuses accepted initial Outline."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from evaluate_report_outline import METRICS, ROOT, dump, outline_metrics, rebase_paths, render_outline
from evaluate_report_preresearch import assess


def consolidation_metrics(tree, ledger, *, accepted_revision):
    writable = {n["node_id"] for n in tree.get("nodes", []) if n.get("kind") == "writable"}
    covered = {n for r in ledger.get("write_requests", []) if r.get("status") in {"pending", "in_flight", "technical_failed", "completed"} for n in r.get("node_ids", [])}
    active = [g["gap_id"] for g in ledger.get("gaps", []) if g.get("status") not in {"resolved", "not_needed", "awaiting_user"}]
    user = [g["gap_id"] for g in ledger.get("gaps", []) if g.get("status") == "awaiting_user"]
    missing = sorted(writable - covered)
    return dict(accepted_revision=accepted_revision, missing_write_nodes=missing, unclosed_gaps=active, awaiting_user_gaps=user, consolidation_ok=accepted_revision and not missing and not active and not user)


def worker(directory, upstream, consolidation_only=False):
    import yaml
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    os.environ.update(DEER_FLOW_PROJECT_ROOT=str(ROOT), DEER_FLOW_HOME=str(directory / "runtime"))
    for key in ("NO_PROXY", "no_proxy"):
        os.environ[key] = os.environ.get(key, "") + ",localhost,127.0.0.1,api.deepseek.com"
    config = yaml.safe_load((ROOT / "config.yaml").read_text())
    config["database"] = {"backend": "sqlite", "sqlite_dir": str(directory / "runtime/db")}
    for tool in config["tools"]:
        if tool["name"] in {"web_search", "web_fetch"}:
            tool.update(use=f"deerflow.community.deepseek_tools.tools:{tool['name']}_tool", api_key="$LLM_API_KEY", timeout=30)
            if tool["name"] == "web_search":
                tool["max_results"] = 10
    config_path = directory / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    os.environ["DEER_FLOW_CONFIG_PATH"] = str(config_path)

    from dataclasses import replace
    from types import SimpleNamespace

    from langchain_core.messages import HumanMessage

    from deerflow.config import get_app_config
    from deerflow.config.paths import get_paths
    from deerflow.runtime.user_context import set_current_user
    from deerflow.sp.actions.handlers.base import HandlerContext
    from deerflow.sp.actions.handlers.delegate import DelegateHandler
    from deerflow.sp.actions.schema import ActionType, SPAction
    from deerflow.sp.memory import TaskMemoryStack
    from deerflow.sp.report.hooks import configure_report_subagent
    from deerflow.sp.runtime import REPORT_OUTLINE_OUTPUT_MAX_TOKENS, app_config_for_report_outline
    from deerflow.sp.subagents import DR2SubagentExecutorAdapter
    from deerflow.subagents import get_subagent_config
    from deerflow.subagents.executor import SubagentExecutor
    from deerflow.tools.tools import get_available_tools

    query = json.loads((directory / "query.json").read_text())
    source = upstream / str(query["id"])
    original = json.loads((source / ("research-ledger.json" if consolidation_only else "ledger.json")).read_text())
    accepted_key = "research_ok" if consolidation_only else "accepted"
    if not json.loads((source / "metrics.json").read_text()).get(accepted_key) or not original.get("tree"):
        raise ValueError("blocked_upstream: accepted initial Outline required")
    set_current_user(SimpleNamespace(id="stage-eval", role="admin"))
    thread_id = f"pre-{query['id']}"
    paths = get_paths()
    outputs = paths.sandbox_outputs_dir(thread_id, user_id="stage-eval")
    old_root = Path(original["request"]["path"]).parents[3]
    tree_bytes = Path(original["tree"]["path"]).read_bytes()
    if hashlib.sha256(tree_bytes).hexdigest() != original["tree"]["sha256"] and consolidation_only:
        initial_batch = Path(json.loads((upstream / "manifest.json").read_text())["upstream"])
        initial_source = initial_batch / str(query["id"])
        original_tree = json.loads((initial_source / "ledger.json").read_text())["tree"]
        baseline_bytes = Path(original_tree["path"]).read_bytes()
        if hashlib.sha256(baseline_bytes).hexdigest() != original["tree"]["sha256"] or json.loads(baseline_bytes) != json.loads(tree_bytes):
            raise ValueError("blocked_upstream: original accepted tree cannot be restored exactly")
        tree_bytes = baseline_bytes
        dump(directory / "input-tree-restoration.json", {"origin": original_tree["path"], "sha256": original["tree"]["sha256"], "reason": "Clone serialization changed bytes only; exact original verified and restored in this clone."})
    for item in [*original.get("materials", []), *original.get("summaries", []), original["tree"]]:
        file = Path(item["path"])
        if not file.resolve().is_relative_to(old_root.resolve()) or hashlib.sha256(tree_bytes if item is original["tree"] else file.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("blocked_upstream: artifact changed")
    shutil.copytree(old_root, outputs.parent)
    report_directory = outputs / "report" / original["report_id"]
    for file in outputs.parent.rglob("*.json"):
        # Candidate/rejected JSON may intentionally be incomplete. Accepted
        # trees and diagnostic proposals are immutable content, not path indexes.
        if file.is_relative_to(report_directory / "outline") or file.is_relative_to(report_directory / "tree"):
            continue
        dump(file, rebase_paths(json.loads(file.read_text()), old_root, outputs.parent))
    Path(rebase_paths(original["tree"]["path"], old_root, outputs.parent)).write_bytes(tree_bytes)
    if consolidation_only:
        initial_batch = Path(json.loads((upstream / "manifest.json").read_text())["upstream"])
        initial_source = initial_batch / str(query["id"])
        initial_ledger = json.loads((initial_source / "ledger.json").read_text())
        checkpoint_root = Path(initial_ledger["request"]["path"]).parents[3]
    else:
        initial_source, checkpoint_root = source, old_root
    checkpoint = rebase_paths(json.loads((initial_source / "restored-checkpoint.json").read_text()), checkpoint_root, outputs.parent)
    state = dict(
        messages=[HumanMessage(content=query["prompt"], id="original-query")],
        thread_data=dict(outputs_path=str(outputs), workspace_path=str(paths.sandbox_work_dir(thread_id, user_id="stage-eval")), uploads_path=str(paths.sandbox_uploads_dir(thread_id, user_id="stage-eval"))),
    )
    state.update(checkpoint["state_update"])
    memory = checkpoint["memory"]
    for attempt in sorted(initial_source.glob("attempt-*.json")):
        saved = rebase_paths(json.loads(attempt.read_text()), checkpoint_root, outputs.parent)
        state.update(saved["state_update"])
        memory["entries"].extend(saved.get("memory_entries", []))
    if consolidation_only:
        research_handoff = rebase_paths(json.loads((source / "handler-1.json").read_text()), old_root, outputs.parent)
        if research_handoff.get("error"):
            raise ValueError("blocked_upstream: supplementary research handoff failed")
        state.update(research_handoff["state_update"])
        memory["entries"].extend(research_handoff.get("memory_entries", []))
        # The cloned runtime also contains later Outline revisions. Restore the
        # exact accepted research ledger, not the final consolidated ledger.
        target_ledger = outputs / "report" / original["report_id"] / "ledger.json"
        dump(target_ledger, rebase_paths(original, old_root, outputs.parent))
    stack = TaskMemoryStack.from_dict(memory, thread_id=thread_id)
    app = get_app_config()
    tools = get_available_tools(model_name=app.models[0].name, app_config=app)
    phase = "research"
    raw_results, created = [], []

    def observe(step, index):
        public = {k: step[k] for k in ("type", "name", "status", "tool_call_id", "content", "tool_calls", "invalid_tool_calls", "usage_metadata") if k in step}
        with (directory / "steps.jsonl").open("a") as stream:
            stream.write(json.dumps(dict(phase=phase, index=index, **public), ensure_ascii=False, default=str) + "\n")

    def factory(task):
        role_name = "sp-researcher" if task.subagent_type == "researcher" else "sp-outline"
        cfg, scoped, role = configure_report_subagent(task, state=state, available_tools=tools, subagent_config=get_subagent_config(role_name, app_config=app))
        if not task.metadata.get("report_supplement_repair"):
            cfg = replace(cfg, max_turns=350, tool_call_limit=50, **({"finalize_after_tool_calls": 40} if role == "researcher" else {}))
        limit = REPORT_OUTLINE_OUTPUT_MAX_TOKENS
        task.metadata["max_tokens_per_step"] = limit
        dump(
            directory / f"task-{len(created) + 1}.json",
            dict(metadata=task.metadata, context_refs=task.context_refs, thinking_enabled=bool(app.models[0].supports_thinking), max_turns=cfg.max_turns, tool_call_limit=cfg.tool_call_limit, output_format=cfg.output_format),
        )
        executor = SubagentExecutor(
            config=cfg,
            tools=scoped,
            app_config=app_config_for_report_outline(app, task) if role == "outline" else app,
            parent_model=app.models[0].name,
            max_tokens_per_step=limit,
            thread_data=state["thread_data"],
            thread_id=thread_id,
            run_id=f"supplement-{query['id']}",
            user_id="stage-eval",
            user_scoped_skills=True,
            thinking_enabled=bool(app.models[0].supports_thinking),
            step_observer=observe,
        )
        created.append(executor)
        return executor

    context = HandlerContext(state=state, stack=stack, thread_id=thread_id, run_id=f"supplement-{query['id']}")
    handler = DelegateHandler(executor=DR2SubagentExecutorAdapter(factory, result_observer=raw_results.append))
    ledger_file = next(outputs.glob("report/*/ledger.json"))
    initial = json.loads(ledger_file.read_text())
    gap_ids = [g["gap_id"] for g in initial["gaps"] if g["status"] == ("awaiting_outline" if consolidation_only else "open")]
    records = []

    def delegate(target, text, metadata):
        action = SPAction.create(
            ActionType.DELEGATE,
            target_agent=target,
            task=text,
            stage="research" if target == "researcher" else "planning",
            reason="Isolated native supplementary research and consolidation test",
            expected_output="Normal native report stage handoff",
            metadata=metadata,
        )
        result = handler.handle(action, context)
        record = result.to_dict()
        records.append(dict(phase=phase, action_id=action.action_id, error=result.error, next_step=result.next_step))
        dump(directory / f"handler-{len(records)}.json", record)
        state.update(result.state_update)
        dump(directory / "checkpoint.json", dict(state_update={k: v for k, v in state.items() if k not in {"messages", "thread_data"}}, memory=stack.to_dict()))
        return action, result

    research_ok, summary_scores = True, []
    if consolidation_only:
        text = (source / "summary.md").read_text()
        (directory / "summary.md").write_text(text)
        summary_scores = [assess(text, initial, error=None, stop_reason=None)]
        research_ok = all(s["summary_ok"] and s["body_unique_sources"] > 0 and not s["invalid_source_ids"] for s in summary_scores)
    if gap_ids and not consolidation_only:
        action, result = delegate(
            "researcher",
            "Process all assigned report gaps together in one research pass. Share searches and material collection across nodes. "
            "Return a complete cited research synthesis and a result for every assigned gap using the native report protocol.",
            {"report_gap_ids": gap_ids},
        )
        ledger = json.loads(ledger_file.read_text())
        for summary in ledger.get("summaries", []):
            if summary.get("action_id") == action.action_id:
                text = Path(summary["path"]).read_text()
                (directory / "summary.md").write_text(text)
                summary_scores.append(assess(text, ledger, error=result.error, stop_reason=raw_results[-1].stop_reason if raw_results else None))
        research_ok = not result.error and bool(summary_scores) and all(s["summary_ok"] and s["body_unique_sources"] > 0 and not s["invalid_source_ids"] for s in summary_scores)
    dump(directory / "research-ledger.json", json.loads(ledger_file.read_text()))
    dump(directory / "research-metrics.json", dict(gap_ids=gap_ids, research_ok=research_ok, summaries=summary_scores))
    accepted_revision = False
    if research_ok and gap_ids:
        phase = "consolidation"
        for attempt in range(3):
            previous = records[-1].get("error") if records else None
            before_version = json.loads(ledger_file.read_text())["tree"]["version"]
            _, result = delegate(
                "outline",
                "Consolidate all returned gap research into the existing Outline. Bind useful saved materials to nodes, "
                "provide outcomes for every returned gap, close optional unmet targets, and create/retain a write request for every writable node. "
                "Do not start new automatic research. Deliver the complete revised tree through native submission." + ("\nPrevious rejection: " + str(previous) if previous else ""),
                {"report_task": "revise"},
            )
            after_version = json.loads(ledger_file.read_text())["tree"]["version"]
            if not result.error and after_version > before_version:
                accepted_revision = True
                break
    ledger = json.loads(ledger_file.read_text())
    tree = json.loads(Path(ledger["tree"]["path"]).read_text())
    dump(directory / "ledger.json", ledger)
    dump(directory / "outline.json", tree)
    (directory / "outline.md").write_text(render_outline(tree))
    scores = consolidation_metrics(tree, ledger, accepted_revision=accepted_revision or not gap_ids)
    steps_file = directory / "steps.jsonl"
    steps = [json.loads(line) for line in steps_file.read_text().splitlines()] if steps_file.exists() else []
    row = dict(
        id=query["id"],
        gap_ids=gap_ids,
        research_ok=research_ok,
        summaries=summary_scores,
        records=records,
        blocked_upstream=not research_ok,
        skipped_no_gaps=not gap_ids,
        consolidation_only=consolidation_only,
        retrieval_calls=sum(c.get("name") in {"web_search", "web_fetch", "report_fetch_material"} for step in steps for c in step.get("tool_calls", [])),
        submission_events=[event for executor in created for event in getattr(getattr(executor, "_tool_argument_repair_middleware", None), "events", [])],
        **scores,
        outline=outline_metrics(tree, ledger, 0),
        new_materials=len(ledger["materials"]) - len(initial["materials"]),
        tool_errors=METRICS.collect_tool_errors(steps),
        stop_reasons=[r.stop_reason for r in raw_results],
        token_usage_records=[u for r in raw_results for u in r.token_usage_records],
    )
    row["overall_pass"] = research_ok and scores["consolidation_ok"]
    dump(directory / "metrics.json", row)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--ids", default="11,25,97")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--consolidation-only", action="store_true", help="Resume accepted research-ledger and handoff; never re-run Researcher")
    args = parser.parse_args()
    output = args.output.resolve()
    upstream = args.upstream.resolve()
    if args.worker:
        worker(output, upstream, args.consolidation_only)
        return
    ids = [int(s) for s in args.ids.split(",")]
    if len(ids) != len(set(ids)) or args.concurrency < 1:
        parser.error("unique IDs and positive concurrency required")
    output.mkdir(parents=True, exist_ok=False)
    dump(
        output / "manifest.json",
        dict(
            ids=ids,
            upstream=str(upstream),
            concurrency=args.concurrency,
            research_attempts=0 if args.consolidation_only else 1,
            consolidation_only=args.consolidation_only,
            outline_attempts=3,
            max_tools=50,
            max_turns=350,
            scope="native batched supplementary research then Outline revision; no Central, Writer or delivery",
        ),
    )
    for prefix in ("backend/packages/harness/deerflow", "backend/scripts", "skills/public/report-research", "skills/public/report-outline"):
        for file in (ROOT / prefix).rglob("*"):
            if file.suffix in {".py", ".md"}:
                target = output / "code" / file.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, target)

    def run(i):
        d = output / str(i)
        d.mkdir()
        for name in ("query.json", "request.md"):
            shutil.copyfile(upstream / str(i) / name, d / name)
        with (d / "worker.log").open("w") as log:
            try:
                command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--output", str(d), "--upstream", str(upstream)]
                if args.consolidation_only:
                    command.append("--consolidation-only")
                p = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=1200)
                if not (d / "metrics.json").exists():
                    dump(d / "metrics.json", dict(id=i, overall_pass=False, error=f"worker_exit_{p.returncode}"))
            except subprocess.TimeoutExpired:
                dump(d / "metrics.json", dict(id=i, overall_pass=False, error="sample_timeout"))
        return json.loads((d / "metrics.json").read_text())

    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for f in concurrent.futures.as_completed([pool.submit(run, i) for i in ids]):
            r = f.result()
            rows.append(r)
            dump(output / "results.json", rows)
            lines = ["# 补充研究与 Outline 收尾 smoke", "", "| ID | 补研通过 | 收尾通过 | 新材料 | summary | 大纲 |", "|---|---|---|---|---|---|"]
            for row in sorted(rows, key=lambda x: x["id"]):
                i = row["id"]
                lines.append(f"| {i} | {row.get('research_ok')} | {row.get('consolidation_ok')} | {row.get('new_materials')} | [查看]({i}/summary.md) | [查看]({i}/outline.md) |")
            (output / "REPORT.md").write_text("\n".join(lines) + "\n")
            print(json.dumps({k: r.get(k) for k in ("id", "research_ok", "consolidation_ok", "overall_pass", "error")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
