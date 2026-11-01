"""Real pre-research stage evaluation; never dispatches Outline or Writer.

Run from backend with PYTHONPATH=.:packages/harness. Each sample has a separate
process/runtime. Credentials come from the existing .env and are never copied.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib

# scripts/ is on sys.path for CLI use; explicit loading also supports isolated
# test imports without adding scripts/ to the application's module search path.
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_METRICS_SPEC = importlib.util.spec_from_file_location("report_stage_metrics", Path(__file__).with_name("report_stage_metrics.py"))
_METRICS = importlib.util.module_from_spec(_METRICS_SPEC)
_METRICS_SPEC.loader.exec_module(_METRICS)

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_IDS = [1, 5, 8, 11, 13, 21, 25, 29, 31, 35, 55, 60, 62, 64, 73, 77, 80, 84, 87, 97]
SMOKE_IDS = [31, 11, 97]


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def assess(text, ledger, *, error=None, stop_reason=None):
    # Count citations in prose, not a trailing bibliography. This is a breadth
    # heuristic, not proof that each referenced page entails the nearby claim.
    body = re.split(r"(?im)^#{1,6}\s*[^\n]*(?:references|bibliography|source catalog|material index|参考文献|参考资料|来源列表|材料索引)[^\n]*$", text)[0]
    body = "\n".join(line for line in body.splitlines() if not re.search(r"unread leads|未读线索|未阅读线索", line, re.I))
    cited = set(re.findall(r"\[S(\d+)\]", body))
    known = {str(row["source_id"]).removeprefix("S") for row in ledger.get("sources", [])}
    invalid = sorted(cited - known)
    format_issue = _METRICS.summary_format_issue(text)
    summary_ok = len(text.strip()) >= 500 and not format_issue and not stop_reason and not error
    return {
        "no_error": not error and not stop_reason,
        "summary_ok": summary_ok,
        "summary_format_issue": format_issue,
        "rich_citations": summary_ok and len(cited) >= 5 and not invalid,
        "body_unique_sources": len(cited),
        "invalid_source_ids": invalid,
        "saved_sources": len(known),
        "saved_materials": len(ledger.get("materials", [])),
        "summary_chars": len(text),
        "error": error,
        "stop_reason": stop_reason,
    }


def render_report(output, results):
    counts = {key: sum(row[key] for row in results) for key in ("no_error", "summary_ok", "rich_citations")}
    passed = sum(all(row[key] for key in counts) for row in results)
    lines = [
        "# 预检索阶段评测",
        "",
        "仅运行原生 Researcher 阶段。无异常要求没有执行中断及对 Agent 可见的工具错误；正常综述排除原始 JSON、恢复记录和截断替代物；引用丰富暂定为正文至少 5 个不同来源且编号均存在。引用数是启发式统计，不代表事实已逐条核实。",
        "",
        f"样本 {len(results)}；无异常 {counts['no_error']}；正常综述 {counts['summary_ok']}；引用检查通过 {counts['rich_citations']}；三项全通过 {passed}。",
        "",
        "| ID | 无异常 | 正常综述 | 引用检查 | 正文来源数 | 保存来源数 | summary |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in sorted(results, key=lambda r: r["id"]):
        cited = row.get("body_unique_sources", 0) if row["summary_ok"] else "—"
        lines.append(f"| {row['id']} | {row['no_error']} | {row['summary_ok']} | {row['rich_citations']} | {cited} | {row.get('saved_sources', 0)} | [查看]({row['id']}/summary.md) |")
    (output / "REPORT.md").write_text("\n".join(lines) + "\n")


def audit_batch(output):
    """Re-score saved evidence without models, tools, or changing real outputs."""
    results = []
    for directory in output.iterdir():
        metrics_path = directory / "metrics.json"
        if not directory.is_dir() or not metrics_path.exists():
            continue
        original = directory / "metrics.raw.json"
        if not original.exists():
            original.write_bytes(metrics_path.read_bytes())
        old = json.loads(original.read_text())
        ledger_path, summary_path = directory / "ledger.json", directory / "summary.md"
        ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
        text = summary_path.read_text() if summary_path.exists() else ""
        steps_path = directory / "steps.jsonl"
        steps = [json.loads(line) for line in steps_path.read_text().splitlines()] if steps_path.exists() else []
        errors = _METRICS.collect_tool_errors(steps)
        tool_errors = [item for record in ledger.get("actions", {}).values() for item in record.get("tool_executions", []) if item.get("ok") is False]
        row = {**old, **assess(text, ledger, error=old.get("error"), stop_reason=old.get("stop_reason")), "step_tool_errors": errors, "tool_errors": tool_errors}
        row["no_error"] = row["no_error"] and not errors and not tool_errors
        row["overall_pass"] = all(row[key] for key in ("no_error", "summary_ok", "rich_citations"))
        dump(metrics_path, row)
        results.append(row)
    results.sort(key=lambda r: r["id"])
    for filename in ("results.json", "REPORT.md"):
        path = output / filename
        raw = output / ("results.raw.json" if filename.endswith("json") else "REPORT.raw.md")
        if path.exists() and not raw.exists():
            raw.write_bytes(path.read_bytes())
    dump(output / "results.json", results)
    dump(
        output / "audit-manifest.json",
        {
            "runtime_rerun": False,
            "summary_files_unchanged": True,
            "scorer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "helper_sha256": hashlib.sha256(Path(__file__).with_name("report_stage_metrics.py").read_bytes()).hexdigest(),
        },
    )
    render_report(output, results)
    return results


def worker(sample_dir):
    # Set paths before importing application modules with cached globals.
    import yaml
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    os.environ.update(DEER_FLOW_PROJECT_ROOT=str(ROOT), DEER_FLOW_HOME=str(sample_dir / "runtime"))
    for key in ("NO_PROXY", "no_proxy"):
        os.environ[key] = os.environ.get(key, "") + ",localhost,127.0.0.1,api.deepseek.com"
    config = yaml.safe_load((ROOT / "config.yaml").read_text())
    config["database"] = {"backend": "sqlite", "sqlite_dir": str(sample_dir / "runtime/db")}
    for entry in config["tools"]:
        if entry["name"] in {"web_search", "web_fetch"}:
            entry.update(use=f"deerflow.community.deepseek_tools.tools:{entry['name']}_tool", api_key="$LLM_API_KEY", timeout=30)
            if entry["name"] == "web_search":
                entry["max_results"] = 10
    # Save only variable references, never resolved credentials.
    config_path = sample_dir / "config.yaml"
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
    from deerflow.sp.subagents import DR2SubagentExecutorAdapter
    from deerflow.subagents import get_subagent_config
    from deerflow.subagents.executor import SubagentExecutor
    from deerflow.tools.tools import get_available_tools

    set_current_user(SimpleNamespace(id="stage-eval", role="admin"))
    sample = json.loads((sample_dir / "query.json").read_text())
    thread_id = f"pre-{sample['id']}"
    paths = get_paths()
    outputs = paths.sandbox_outputs_dir(thread_id, user_id="stage-eval")
    outputs.mkdir(parents=True, exist_ok=True)
    state = {
        "messages": [HumanMessage(content=sample["prompt"], id="original-query")],
        "thread_data": {
            "outputs_path": str(outputs),
            "workspace_path": str(paths.sandbox_work_dir(thread_id, user_id="stage-eval")),
            "uploads_path": str(paths.sandbox_uploads_dir(thread_id, user_id="stage-eval")),
        },
    }
    app_config = get_app_config()
    tools = get_available_tools(model_name=app_config.models[0].name, app_config=app_config)
    executors = []
    raw_results = []

    def observe(step, index):
        # Visible responses only; exclude provider-private reasoning fields.
        public = {k: step[k] for k in ("type", "name", "status", "tool_call_id", "content", "tool_calls", "usage_metadata") if k in step}
        with (sample_dir / "steps.jsonl").open("a") as stream:
            stream.write(json.dumps({"index": index, **public}, ensure_ascii=False, default=str) + "\n")

    def factory(task):
        cfg, scoped, role = configure_report_subagent(task, state=state, available_tools=tools, subagent_config=get_subagent_config("sp-researcher", app_config=app_config))
        assert role == "researcher"
        cfg = replace(cfg, max_turns=350, tool_call_limit=50, finalize_after_tool_calls=40)
        executor = SubagentExecutor(
            config=cfg,
            tools=scoped,
            app_config=app_config,
            parent_model=app_config.models[0].name,
            thread_data=state["thread_data"],
            thread_id=thread_id,
            run_id=thread_id,
            user_id="stage-eval",
            user_scoped_skills=True,
            thinking_enabled=False,
            step_observer=observe,
        )
        executors.append(executor)
        return executor

    action = SPAction.create(
        ActionType.DELEGATE,
        target_agent="researcher",
        stage="research",
        reason="Isolated native pre-research stage evaluation",
        task=sample["prompt"],
        expected_output="Complete the report pre-research evidence handoff as a detailed theme-organized, cited Markdown synthesis in artifact_content; do not produce an outline or final report.",
        metadata={"report_action": "start"},
    )
    started = time.monotonic()
    result = DelegateHandler(executor=DR2SubagentExecutorAdapter(factory, result_observer=raw_results.append)).handle(action, HandlerContext(state=state, stack=TaskMemoryStack(), thread_id=thread_id, run_id=thread_id))
    dump(sample_dir / "handler.json", result.to_dict())
    ledgers = list(outputs.glob("report/*/ledger.json"))
    ledger = json.loads(ledgers[0].read_text()) if ledgers else {}
    dump(sample_dir / "ledger.json", ledger)
    summaries = list(outputs.glob("report/*/research/*/summary.md"))
    text = summaries[0].read_text() if summaries else ""
    (sample_dir / "summary.md").write_text(text)
    raw = raw_results[-1] if raw_results else None
    error = result.error or (getattr(raw, "error", None) if raw else "executor_not_started")
    stop_reason = getattr(raw, "stop_reason", None)
    metrics = assess(text, ledger, error=error, stop_reason=stop_reason)
    executions = [item for action_record in ledger.get("actions", {}).values() for item in action_record.get("tool_executions", [])]
    tool_errors = [item for item in executions if item.get("ok") is False]
    metrics["tool_errors"] = tool_errors
    steps = [json.loads(line) for line in (sample_dir / "steps.jsonl").read_text().splitlines()] if (sample_dir / "steps.jsonl").exists() else []
    metrics["step_tool_errors"] = _METRICS.collect_tool_errors(steps)
    if tool_errors or metrics["step_tool_errors"]:
        metrics["no_error"] = False
    metrics.update(
        id=sample["id"],
        topic=sample["topic"],
        language=sample["language"],
        elapsed_seconds=round(time.monotonic() - started, 1),
        finalization_reason=getattr(getattr(executors[-1], "_finalization_middleware", None), "trigger_reason", None) if executors else None,
        token_usage_records=getattr(raw, "token_usage_records", []),
        tool_calls=sum(len(json.loads(line).get("tool_calls", [])) for line in (sample_dir / "steps.jsonl").read_text().splitlines()) if (sample_dir / "steps.jsonl").exists() else 0,
    )
    dump(sample_dir / "metrics.json", metrics)
    print(json.dumps(metrics, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ids", default=",".join(map(str, SAMPLE_IDS)))
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--audit", action="store_true", help="Re-score an existing batch offline; do not call any API")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.worker:
        worker(output)
        return
    if args.audit:
        results = audit_batch(output)
        print(
            json.dumps(
                {
                    "samples": len(results),
                    "no_error": sum(r["no_error"] for r in results),
                    "summary_ok": sum(r["summary_ok"] for r in results),
                    "rich_citations": sum(r["rich_citations"] for r in results),
                    "overall_pass": sum(r["overall_pass"] for r in results),
                }
            )
        )
        return
    output.mkdir(parents=True, exist_ok=False)
    ids = [int(x) for x in args.ids.split(",")]
    queries = {row["id"]: row for row in map(json.loads, (ROOT / "query.jsonl").read_text().splitlines())}
    if len(set(ids)) != len(ids) or any(i not in queries for i in ids):
        raise ValueError("sample IDs must be unique and exist in query.jsonl")
    manifest = {
        "ids": ids,
        "concurrency": args.concurrency,
        "tool_limit": 50,
        "finalize_after": 40,
        "max_turns": 350,
        "citation_threshold": 5,
        "query_sha256": hashlib.sha256((ROOT / "query.jsonl").read_bytes()).hexdigest(),
        "scope": "pre-research only; excludes Central routing and all downstream stages",
    }
    dump(output / "manifest.json", manifest)
    snapshot = {}
    for prefix in ("backend/packages/harness/deerflow", "skills/public/report-research", "backend/scripts"):
        for file in (ROOT / prefix).rglob("*"):
            if file.suffix in {".py", ".md"}:
                snapshot[str(file.relative_to(ROOT))] = hashlib.sha256(file.read_bytes()).hexdigest()
    dump(output / "source-hashes.json", snapshot)
    # Freeze the actual files too, so later edits cannot erase what was tested.
    for name in snapshot:
        target = output / "code" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())

    def run(sample_id):
        directory = output / str(sample_id)
        directory.mkdir()
        dump(directory / "query.json", queries[sample_id])
        (directory / "request.md").write_text(queries[sample_id]["prompt"])
        with (directory / "worker.log").open("w") as log:
            try:
                process = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", "--output", str(directory)], stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout)
                if process.returncode and not (directory / "metrics.json").exists():
                    dump(directory / "metrics.json", {"id": sample_id, "no_error": False, "summary_ok": False, "rich_citations": False, "error": f"worker_exit_{process.returncode}"})
            except subprocess.TimeoutExpired:
                dump(directory / "metrics.json", {"id": sample_id, "no_error": False, "summary_ok": False, "rich_citations": False, "error": "sample_timeout"})
        return json.loads((directory / "metrics.json").read_text())

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for future in concurrent.futures.as_completed([pool.submit(run, i) for i in ids]):
            result = future.result()
            results.append(result)
            dump(output / "results.json", sorted(results, key=lambda r: r["id"]))
            print(json.dumps({k: v for k, v in result.items() if k != "token_usage_records"}, ensure_ascii=False), flush=True)
    render_report(output, results)


if __name__ == "__main__":
    main()
