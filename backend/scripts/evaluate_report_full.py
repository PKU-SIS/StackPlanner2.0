"""Full native Central report graph, isolated cases, explicit off-peak start gate."""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from evaluate_report_outline import ROOT, dump


def safe(value):
    if hasattr(value, "tool_calls"):
        return {k: safe(getattr(value, k, None)) for k in ("type", "content", "tool_calls", "invalid_tool_calls", "usage_metadata")}
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items() if not any(word in str(k).lower() for word in ("api_key", "secret", "password", "reasoning", "thinking", "chain_of_thought"))}
    if isinstance(value, (list, tuple)):
        return [safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "model_dump"):
        return safe(value.model_dump())
    return str(value)


def worker(directory, not_before, timeout):
    if time.time() < not_before:
        raise RuntimeError("Off-peak gate: model calls are not allowed yet")
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
    from types import SimpleNamespace

    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from deerflow.config import get_app_config
    from deerflow.config.paths import get_paths
    from deerflow.runtime.user_context import set_current_user
    from deerflow.sp.runtime import make_sp_agent

    set_current_user(SimpleNamespace(id="full-eval", role="admin"))
    query = json.loads((directory / "query.json").read_text())
    thread = f"full-{query['id']}"
    app = get_app_config()
    runtime = dict(
        thread_id=thread,
        run_id=f"{thread}-{uuid4().hex[:12]}",
        user_id="full-eval",
        model_name=app.models[0].name,
        thinking_enabled=bool(app.models[0].supports_thinking),
        sp_subagent_thinking_enabled=bool(app.models[0].supports_thinking),
        debug_trace_enabled=True,
        max_concurrent_subagents=6,
        sp_subagent_timeout_seconds=timeout - 30,
        sp_central_timeout_seconds=90,
        sp_central_retry_timeout_seconds=60,
    )
    graph_config = {"configurable": runtime, "context": runtime, "metadata": {"experiment": "full-report-smoke", "debug_trace_enabled": True}, "recursion_limit": 1600}
    dump(directory / "run-config.json", graph_config)
    last = {}
    error = None
    started = time.time()
    events = 0

    async def run():
        nonlocal last, error, events
        async with AsyncSqliteSaver.from_conn_string(str(directory / "checkpoints.sqlite")) as checkpoint:
            graph = make_sp_agent(graph_config, app_config=app)
            graph.checkpointer = checkpoint
            try:
                async with asyncio.timeout(timeout):
                    iterator = graph.astream({"messages": [HumanMessage(content=query["prompt"])]}, config=graph_config, context=runtime, stream_mode=["values", "custom"]).__aiter__()
                    while True:
                        try:
                            mode, data = await asyncio.wait_for(anext(iterator), timeout=300)
                        except StopAsyncIteration:
                            break
                        events += 1
                        if mode == "values":
                            last = data
                            dump(directory / "state.json", safe(last))
                        with (directory / "events.jsonl").open("a") as stream:
                            stream.write(json.dumps({"time": time.time(), "mode": mode, "data": safe(data)}, ensure_ascii=False) + "\n")
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"

    asyncio.run(run())
    outputs = get_paths().sandbox_outputs_dir(thread, user_id="full-eval")
    ledgers = list(outputs.glob("report/*/ledger.json"))
    ledger = json.loads(ledgers[-1].read_text()) if ledgers else {}
    dump(directory / "ledger.json", ledger)
    final = ledger.get("final_report") or {}
    content = Path(final["path"]).read_text() if final.get("path") and Path(final["path"]).exists() else ""
    if content:
        (directory / "final-report.md").write_text(content)
    tree = json.loads(Path(ledger["tree"]["path"]).read_text()) if ledger.get("tree") else {}
    writable = {n["node_id"] for n in tree.get("nodes", []) if n.get("kind") == "writable"}
    current = {d["node_id"] for d in ledger.get("drafts", []) if d.get("is_current")}
    dump(
        directory / "metrics.json",
        dict(
            id=query["id"],
            delivered=bool(ledger.get("delivered")),
            error=error,
            elapsed_seconds=round(time.time() - started, 1),
            events=events,
            writable_count=len(writable),
            current_drafts=len(current),
            missing_nodes=sorted(writable - current),
            final_chars=len(content),
            citation_sources=len(final.get("citation_numbering") or {}),
            final_hash_ok=bool(content) and hashlib.sha256(content.encode()).hexdigest() == final.get("sha256"),
            last_stage=last.get("sp_current_stage"),
            last_handler=safe(last.get("sp_last_handler_result")),
            overall_pass=bool(ledger.get("delivered")) and bool(content) and not error,
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ids", default="31,11,97")
    parser.add_argument("--not-before", required=True)
    parser.add_argument("--timeout", type=int, default=1500)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--start", action="store_true")
    args = parser.parse_args()
    gate = datetime.fromisoformat(args.not_before).timestamp()
    out = args.output.resolve()
    if args.worker:
        worker(out, gate, args.timeout)
        return
    out.mkdir(parents=True, exist_ok=False)
    ids = [int(x) for x in args.ids.split(",")]
    queries = {q["id"]: q for q in map(json.loads, (ROOT / "query.jsonl").read_text().splitlines())}
    dump(
        out / "manifest.json",
        dict(
            ids=ids,
            not_before=args.not_before,
            concurrency=len(ids),
            timeout=args.timeout,
            scope="native make_sp_agent Central graph from original query; no stage replay; SQLite checkpoints; frontend excluded",
            status="waiting" if args.start else "prepared_only",
        ),
    )
    for i in ids:
        d = out / str(i)
        d.mkdir()
        dump(d / "query.json", queries[i])
        (d / "request.md").write_text(queries[i]["prompt"])
    hashes = {}
    for prefix in ("backend/packages/harness/deerflow", "backend/scripts", "skills/public"):
        for f in (ROOT / prefix).rglob("*"):
            if f.suffix in {".py", ".md"}:
                data = f.read_bytes()
                hashes[str(f.relative_to(ROOT))] = hashlib.sha256(data).hexdigest()
                target = out / "code" / f.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
    dump(out / "source-hashes.json", hashes)
    if not args.start:
        print("Prepared only: no models or paid APIs invoked.", flush=True)
        return
    while time.time() < gate:
        print(json.dumps({"status": "waiting_for_off_peak", "seconds": round(gate - time.time())}), flush=True)
        time.sleep(min(30, max(0, gate - time.time())))

    def run(i):
        d = out / str(i)
        with (d / "worker.log").open("w") as log:
            try:
                result = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve()), "--worker", "--output", str(d), "--not-before", args.not_before, "--timeout", str(args.timeout)], stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout + 90
                )
                if not (d / "metrics.json").exists():
                    dump(d / "metrics.json", dict(id=i, overall_pass=False, error=f"worker_exit_{result.returncode}"))
            except subprocess.TimeoutExpired:
                dump(d / "metrics.json", dict(id=i, overall_pass=False, error="hard_timeout"))
        return json.loads((d / "metrics.json").read_text())

    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(ids)) as pool:
        for future in concurrent.futures.as_completed([pool.submit(run, i) for i in ids]):
            row = future.result()
            rows.append(row)
            dump(out / "results.json", rows)
            lines = ["# Central 全链路 smoke", "", "| ID | 交付 | 草稿/节点 | 来源 | 结果 |", "|---|---|---|---|---|"]
            for r in rows:
                lines.append(f"| {r['id']} | {r.get('delivered')} | {r.get('current_drafts')}/{r.get('writable_count')} | {r.get('citation_sources')} | [详情]({r['id']}/metrics.json) |")
            (out / "REPORT.md").write_text("\n".join(lines) + "\n")
            print(json.dumps({k: v for k, v in row.items() if k != "last_handler"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
