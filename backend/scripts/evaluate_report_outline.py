"""Evaluate native initial Outline using immutable, real pre-research outputs."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_IDS = [1, 5, 8, 11, 13, 21, 25, 29, 31, 35, 55, 60, 62, 64, 73, 77, 80, 84, 87, 97]
SPEC = importlib.util.spec_from_file_location("stage_metrics", Path(__file__).with_name("report_stage_metrics.py"))
METRICS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(METRICS)


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def rebase_paths(value, old_root, destination):
    if isinstance(value, str) and value.startswith(str(old_root) + "/"):
        return str(destination) + value[len(str(old_root)) :]
    if isinstance(value, dict):
        return {key: rebase_paths(item, old_root, destination) for key, item in value.items()}
    if isinstance(value, list):
        return [rebase_paths(item, old_root, destination) for item in value]
    return value


def clone_upstream(source, destination):
    metrics = json.loads((source / "metrics.json").read_text())
    ledger = json.loads((source / "ledger.json").read_text())
    if not metrics.get("summary_ok") or not metrics.get("rich_citations") or ledger.get("tree"):
        raise ValueError("blocked_upstream: a validated pre-research synthesis without a tree is required")
    old_root = Path(ledger["request"]["path"]).parents[3]
    for material in ledger.get("materials", []):
        path = Path(material["path"])
        if not path.resolve().is_relative_to(old_root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != material["sha256"]:
            raise ValueError("blocked_upstream: saved material is missing or changed")
    for summary in ledger.get("summaries", []):
        if Path(summary["path"]).read_text() != (source / "summary.md").read_text():
            raise ValueError("blocked_upstream: summary snapshot mismatch")
    shutil.copytree(old_root, destination)

    for path in destination.rglob("*.json"):
        dump(path, rebase_paths(json.loads(path.read_text()), old_root, destination))
    return rebase_paths(ledger, old_root, destination)


def outline_metrics(tree, ledger, summary_sources):
    nodes = tree.get("nodes", []) if tree else []
    writable = [n for n in nodes if n.get("kind") == "writable"]
    materials = {row["material_id"]: row for row in ledger.get("materials", [])}
    known_sources = {row["source_id"] for row in ledger.get("sources", [])}
    mapped = {rid for node in nodes for rid in node.get("requirement_ids", [])}
    all_sources, issues, per_node = set(), [], []
    for node in writable:
        sources = set()
        for item in node.get("evidence", []):
            material = materials.get(item.get("material_id"))
            ids = set(item.get("source_ids", []))
            if material is None or not ids <= known_sources or material.get("source_id") not in ids:
                issues.append({"node_id": node["node_id"], "issue": "material_source_mismatch", "evidence": item})
            lines = item.get("locator", {}).get("lines")
            if lines and (len(lines) != 2 or lines[0] < 1 or lines[1] < lines[0] or material and lines[1] > material.get("lines", 0)):
                issues.append({"node_id": node["node_id"], "issue": "invalid_lines", "evidence": item})
            sources.update(ids)
        all_sources.update(sources)
        per_node.append({"node_id": node["node_id"], "title": node.get("title"), "sources": sorted(sources), "source_count": len(sources), "gap_ids": node.get("gap_ids", [])})
    return {
        "node_count": len(nodes),
        "writable_count": len(writable),
        "nodes_with_evidence": sum(bool(n["source_count"]) for n in per_node),
        "nodes_with_evidence_or_gap": sum(bool(n["source_count"] or n["gap_ids"]) for n in per_node),
        "outline_sources": len(all_sources),
        "summary_sources": summary_sources,
        "unmapped_requirements": [r["id"] for r in tree.get("requirements", []) if r["id"] not in mapped] if tree else [],
        "evidence_issues": issues,
        "node_evidence": per_node,
    }


def worker(directory, upstream, attempts):
    import yaml
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    os.environ.update(DEER_FLOW_PROJECT_ROOT=str(ROOT), DEER_FLOW_HOME=str(directory / "runtime"))
    for name in ("NO_PROXY", "no_proxy"):
        os.environ[name] = os.environ.get(name, "") + ",localhost,127.0.0.1,api.deepseek.com"
    config = yaml.safe_load((ROOT / "config.yaml").read_text())
    config["database"] = {"backend": "sqlite", "sqlite_dir": str(directory / "runtime/db")}
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
    set_current_user(SimpleNamespace(id="stage-eval", role="admin"))
    thread_id = f"pre-{query['id']}"
    paths = get_paths()
    outputs = paths.sandbox_outputs_dir(thread_id, user_id="stage-eval")
    source = upstream / str(query["id"])
    clone_upstream(source, outputs.parent)
    original_ledger = json.loads((source / "ledger.json").read_text())
    original_root = Path(original_ledger["request"]["path"]).parents[3]
    checkpoint = rebase_paths(json.loads((source / "handler.json").read_text()), original_root, outputs.parent)
    state = {
        "messages": [HumanMessage(content=query["prompt"], id="original-query")],
        "thread_data": {"outputs_path": str(outputs), "workspace_path": str(paths.sandbox_work_dir(thread_id, user_id="stage-eval")), "uploads_path": str(paths.sandbox_uploads_dir(thread_id, user_id="stage-eval"))},
    }
    state.update(checkpoint["state_update"])
    stack = TaskMemoryStack.from_dict({"entries": checkpoint["memory_entries"]}, thread_id=thread_id)
    dump(directory / "restored-checkpoint.json", {"state_update": checkpoint["state_update"], "memory": stack.to_dict()})
    config = get_app_config()
    tools = get_available_tools(model_name=config.models[0].name, app_config=config)
    raws, created = [], []
    current_attempt = 0

    def observe(step, index):
        public = {key: step[key] for key in ("type", "name", "status", "tool_call_id", "content", "tool_calls", "invalid_tool_calls", "usage_metadata") if key in step}
        with (directory / "steps.jsonl").open("a") as stream:
            stream.write(json.dumps({"attempt": current_attempt, "index": index, **public}, ensure_ascii=False, default=str) + "\n")

    def factory(task):
        cfg, scoped, role = configure_report_subagent(task, state=state, available_tools=tools, subagent_config=get_subagent_config("sp-outline", app_config=config))
        assert role == "outline"
        cfg = replace(cfg, max_turns=350, tool_call_limit=50)
        task.metadata["max_tokens_per_step"] = REPORT_OUTLINE_OUTPUT_MAX_TOKENS
        executor = SubagentExecutor(
            config=cfg,
            tools=scoped,
            app_config=app_config_for_report_outline(config, task),
            parent_model=config.models[0].name,
            max_tokens_per_step=REPORT_OUTLINE_OUTPUT_MAX_TOKENS,
            thread_data=state["thread_data"],
            thread_id=thread_id,
            run_id=f"outline-{query['id']}",
            user_id="stage-eval",
            user_scoped_skills=True,
            thinking_enabled=False,
            step_observer=observe,
        )
        created.append(executor)
        dump(
            directory / f"task-{current_attempt}.json",
            {
                "metadata": task.metadata,
                "context_refs": task.context_refs,
                "executor_profile": {
                    "max_turns": cfg.max_turns,
                    "tool_call_limit": cfg.tool_call_limit,
                    "finalize_after_tool_calls": cfg.finalize_after_tool_calls,
                    "keep_tools": cfg.finalize_keep_tools,
                    "token_fraction": cfg.finalize_token_fraction,
                },
            },
        )
        return executor

    context = HandlerContext(state=state, stack=stack, thread_id=thread_id, run_id=f"outline-{query['id']}")
    handler = DelegateHandler(executor=DR2SubagentExecutorAdapter(factory, result_observer=raws.append))
    attempt_results = []
    started = time.monotonic()
    ledger_file = next(outputs.glob("report/*/ledger.json"))
    tree = None
    for current_attempt in range(1, attempts + 1):
        previous_error = attempt_results[-1]["error"] if attempt_results else ""
        task_text = "Generate the initial report Outline from the original request, saved research synthesis and saved materials. Register the fixed supplementary research needs, bind existing evidence and deliver the complete tree."
        if previous_error:
            task_text = "Correct the retained Outline candidate using the host rejection below. Reuse already registered gap IDs and preserve the original request scope. Do not restart research.\n" + str(previous_error)
        action = SPAction.create(
            ActionType.DELEGATE,
            reason="Isolated initial Outline stage evaluation",
            task=task_text,
            target_agent="outline",
            stage="planning",
            expected_output="One complete valid initial tree accepted through the native report gate",
            metadata={"report_task": "plan"},
        )
        result = handler.handle(action, context)
        record = result.to_dict()
        dump(directory / f"attempt-{current_attempt}.json", record)
        state.update(result.state_update)
        attempt_results.append({"attempt": current_attempt, "error": result.error, "next_step": result.next_step, "action_id": action.action_id})
        ledger = json.loads(ledger_file.read_text())
        if ledger.get("tree"):
            tree = json.loads(Path(ledger["tree"]["path"]).read_text())
            break
    dump(directory / "ledger.json", ledger)
    if tree:
        dump(directory / "outline.json", tree)
        (directory / "outline.md").write_text(render_outline(tree))
    steps_file = directory / "steps.jsonl"
    steps = [json.loads(line) for line in steps_file.read_text().splitlines()] if steps_file.exists() else []
    upstream_metrics = json.loads((source / "metrics.json").read_text())
    metrics = outline_metrics(tree, ledger, upstream_metrics["body_unique_sources"])
    metrics.update(
        id=query["id"],
        accepted=tree is not None,
        attempts=attempt_results,
        elapsed_seconds=round(time.monotonic() - started, 1),
        first_pass_accepted=bool(tree and len(attempt_results) == 1),
        tool_errors=METRICS.collect_tool_errors(steps),
        tool_calls=sum(len(step.get("tool_calls", [])) for step in steps),
        token_usage_records=[row for result in raws for row in getattr(result, "token_usage_records", [])],
        stop_reasons=[getattr(result, "stop_reason", None) for result in raws],
        retrieval_calls=sum(call.get("name") in {"web_search", "web_fetch", "report_fetch_material"} for step in steps for call in step.get("tool_calls", [])),
        finalization_reasons=[getattr(getattr(item, "_finalization_middleware", None), "trigger_reason", None) for item in created],
        invalid_tool_call_count=sum(len(step.get("invalid_tool_calls", [])) for step in steps),
    )
    dump(directory / "metrics.json", metrics)


def render_outline(tree):
    lines = ["# " + tree["report_title"], "", "> 初始规划，尚未执行补充研究或写作。", "", "## 用户要求", ""]
    for item in tree.get("requirements", []):
        lines.append(f"- **{item['id']}**：{item['text']}")
    nodes = {item["node_id"]: item for item in tree.get("nodes", [])}
    for node in tree.get("nodes", []):
        depth, parent, seen = 2, node.get("parent_id"), set()
        while parent in nodes and parent not in seen:
            seen.add(parent)
            depth += 1
            parent = nodes[parent].get("parent_id")
        lines.extend(["", "#" * min(depth, 6) + f" {node['title']}（{node['node_id']}）", ""])
        if node.get("kind") == "writable":
            lines.append(node.get("question", ""))
            evidence = []
            for item in node.get("evidence", []):
                location = item.get("locator", {}).get("lines", [])
                evidence.append(f"{','.join(item.get('source_ids', []))}/{item['material_id']}" + (f":L{location[0]}–{location[1]}" if location else ""))
            lines.extend(["", "证据：" + ("；".join(evidence) or "未独立绑定"), "", "需求：" + ", ".join(node.get("requirement_ids", []))])
            if node.get("gap_ids"):
                lines.extend(["", "待补研究：" + ", ".join(node["gap_ids"])])
    return "\n".join(lines) + "\n"


def render_report(output, results):
    lines = [
        "# Outline 初始规划单阶段测试",
        "",
        "复用真实预检索产物，不运行 Researcher/Writer。accepted 是原生门禁接收；需求映射是结构检查，不等于已经核实用户原始要求无遗漏。",
        "",
        "| ID | accepted | 委派次数 | 有证据/正文节点 | summary→Outline来源数 | 工具错误 | 产物 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in sorted(results, key=lambda row: row["id"]):
        counts = f"{row.get('nodes_with_evidence', 0)}/{row.get('writable_count', 0)}"
        sources = f"{row.get('summary_sources', 0)}→{row.get('outline_sources', 0)}"
        links = f"[详情]({row['id']}/metrics.json) / [大纲]({row['id']}/outline.md) / [JSON]({row['id']}/outline.json)"
        lines.append(f"| {row['id']} | {row.get('accepted', False)} | {len(row.get('attempts', []))} | {counts} | {sources} | {len(row.get('tool_errors', []))} | {links} |")
    (output / "REPORT.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--ids", default=",".join(map(str, SAMPLE_IDS)))
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    output, upstream = args.output.resolve(), args.upstream.resolve()
    if args.worker:
        worker(output, upstream, args.attempts)
        return
    if not 1 <= args.attempts <= 3:
        parser.error("attempts must be between 1 and 3")
    output.mkdir(parents=True, exist_ok=False)
    ids = [int(item) for item in args.ids.split(",")]
    if len(set(ids)) != len(ids):
        parser.error("duplicate sample IDs")
    dump(
        output / "manifest.json",
        {
            "ids": ids,
            "upstream": str(upstream),
            "max_attempts": args.attempts,
            "max_tools_per_attempt": 50,
            "max_turns": 350,
            "model_output_cap": 32768,
            "concurrency": args.concurrency,
            "scope": "native initial Outline only; excludes Central routing, supplementary research, Writer and delivery",
        },
    )
    snapshots = {}
    for prefix in ("backend/packages/harness/deerflow", "backend/scripts", "skills/public/report-outline", "skills/public/report-research"):
        for path in (ROOT / prefix).rglob("*"):
            if path.suffix in {".py", ".md"}:
                target = output / "code" / path.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
                snapshots[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    dump(output / "source-hashes.json", snapshots)

    def run(sample_id):
        directory = output / str(sample_id)
        directory.mkdir()
        for name in ("query.json", "request.md"):
            shutil.copyfile(upstream / str(sample_id) / name, directory / name)
        with (directory / "worker.log").open("w") as log:
            try:
                result = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve()), "--worker", "--output", str(directory), "--upstream", str(upstream), "--attempts", str(args.attempts)], stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout
                )
                if result.returncode and not (directory / "metrics.json").exists():
                    dump(directory / "metrics.json", {"id": sample_id, "accepted": False, "error": f"worker_exit_{result.returncode}"})
            except subprocess.TimeoutExpired:
                dump(directory / "metrics.json", {"id": sample_id, "accepted": False, "error": "sample_timeout"})
        return json.loads((directory / "metrics.json").read_text())

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for future in concurrent.futures.as_completed([pool.submit(run, sample_id) for sample_id in ids]):
            row = future.result()
            results.append(row)
            dump(output / "results.json", sorted(results, key=lambda row: row["id"]))
            render_report(output, results)
            print(json.dumps({key: value for key, value in row.items() if key not in {"node_evidence", "token_usage_records", "tool_errors"}}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
