"""One held-out 10k SP2 case with native Outline-selected body budget."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent
REPO = Path("/data2/pkuiti/zmy/projects/reviews/StackPlanner2-sp2DeepResearch-20261103")
NAME = "band10000-case17-outline-selected"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Reuse the completed nine-run experiment's storage, measurement, preflight and
# native-worker adapter. Its hardcoded nine-run preparation/start/summary are
# never called. The helper and RACE driver are copied and hash-bound locally.
support = load("frozen_experiment_support", OUT / "runner_support.py")
support.OUT = OUT
support.REPO = REPO
support.TIMEOUT = 7200
save, read, sha = support.save, support.read, support.sha


def now():
    return dt.datetime.now(dt.UTC).isoformat()


def manifest():
    value = read(OUT / "manifest.json")
    code = OUT / "code"
    for rel, expected in value["runtime_hashes"].items():
        if sha(code / rel) != expected:
            raise RuntimeError("runtime_source_hash_mismatch: " + rel)
    for rel, expected in value["helper_hashes"].items():
        if sha(OUT / rel) != expected:
            raise RuntimeError("runner_helper_hash_mismatch: " + rel)
    cell = value["cells"][0]
    if sha(OUT / "local-storage" / "runs" / cell["name"] / "query.json") != cell["query_sha256"]:
        raise RuntimeError("query_hash_mismatch")
    if sha(REPO / "config.yaml") != value["original_config_sha256"]:
        raise RuntimeError("model_config_changed_since_preparation")
    if sha(OUT / "project/config.yaml") != value["qwen_config_sha256"]:
        raise RuntimeError("qwen_config_hash_mismatch")
    return value


def native_for(band):
    if band != 10000:
        raise ValueError("this experiment has exactly one adaptive 10k case")
    code = OUT / "code"
    os.environ["DEER_FLOW_SKILLS_PATH"] = str(code / "skills")
    sys.path[:0] = [str(code / "backend/scripts"), str(code / "backend"), str(code / "backend/packages/harness")]
    native = load("qwen_timeout_evaluation_host", OUT / "execution_runner.py")

    native.ROOT = OUT / "project"
    return native


support.prepare = manifest
support.native_for = native_for


def adaptive_worker(name):
    value = manifest()
    cell = next(c for c in value["cells"] if c["name"] == name)
    directory = OUT / "local-storage" / "runs" / name
    os.chdir(directory)
    if (directory / "checkpoints.sqlite").exists():
        raise RuntimeError("existing_attempt_refuse_paid_restart")
    os.environ.update(DEER_FLOW_PROJECT_ROOT=str(REPO), DEER_FLOW_HOME=str(directory / "runtime"))
    native = native_for(cell["target_words"])
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env", override=True)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    os.environ["QWEN_ADAPTER_TRACE_DIR"] = str(directory / "transport")
    native.worker(directory, 0, support.TIMEOUT)


support.worker = adaptive_worker



def observe():
    """Read existing artifacts only; never resume generation or modify drafts."""
    row = support.measure(NAME)
    directory = OUT / "local-storage" / "runs" / NAME
    state = read(directory / "state.json") if (directory / "state.json").exists() else {}
    central = sum((v.get("usage_metadata") or {}).get("total_tokens", 0) for v in state.get("messages", []) if isinstance(v, dict))
    attempts = {}
    if (directory / "events.jsonl").exists():
        for line in (directory / "events.jsonl").read_text().splitlines():
            e = json.loads(line); d = e.get("data")
            if e.get("mode") == "custom" and isinstance(d, dict) and d.get("type") == "task_completed" and d.get("usage"):
                attempt_id = (d.get("result_envelope") or {}).get("task_id")
                if not attempt_id: raise RuntimeError("missing_native_attempt_identity")
                key = (d.get("task_id") or d.get("a2a_task_id"), attempt_id)
                if key in attempts and attempts[key] != d["usage"]: raise RuntimeError("inconsistent_duplicate_usage")
                attempts[key] = d["usage"]
    row.update(generation_tokens=central + sum(u.get("total_tokens", 0) for u in attempts.values()), central_generation_tokens=central, subagent_generation_tokens=sum(u.get("total_tokens", 0) for u in attempts.values()), native_attempt_count=len(attempts), generation_counter="central-plus-unique-native-attempt-v2", tokens_include_cached_input=True)
    ledgers = list((directory / "runtime").rglob("report/r1/ledger.json"))
    if len(ledgers) == 1:
        ledger = read(ledgers[0])
        if (ledger.get("tree") or {}).get("path"):
            tree_record = ledger["tree"]
            path = Path(tree_record["path"])
            if sha(path) != tree_record["sha256"]:
                raise RuntimeError("accepted_outline_hash_mismatch")
            tree = read(path)
            nodes = [n for n in tree["nodes"] if n.get("kind") == "writable"]
            row["outline"] = dict(
                writable_nodes=len(nodes),
                total_arguments=sum(len(n.get("argument") or []) for n in nodes),
                selected_body_target_words=sum(n["constraints"]["target_words"] for n in nodes),
                body_max_sum=sum(n["constraints"]["max_words"] for n in nodes),
                tree_version=tree_record["version"],
                tree_sha256=tree_record["sha256"],
                chapters=[dict(node_id=n["node_id"], title=n.get("title"), arguments=len(n.get("argument") or []), target_words=n["constraints"]["target_words"], max_words=n["constraints"]["max_words"]) for n in nodes],
            )
        # Retain raw native review/findings; distinguish content and length.
        row["ledger_phase"] = ledger.get("phase")
        row["writer_recovery"] = ledger.get("writer_recovery")
        row["candidate_count"] = len(ledger.get("candidates") or [])
        row["length_contract"] = ledger.get("length_contract")
    process = read(directory / "process-result.json") if (directory / "process-result.json").exists() else {}
    row["token_accounting_complete"] = not bool(process.get("stop_reason"))
    if process.get("stop_reason"):
        row["generation_tokens_is_lower_bound"] = True
        row["execution_stop_reason"] = process["stop_reason"]
    previous = directory / "observation.json"
    if previous.exists():
        row.update({k: v for k, v in read(previous).items() if k.startswith("race_")})
    save(previous, row)
    return row


def score():
    """Independent pinned RACE protocol; no new automatic paid scoring attempt."""
    row = read(OUT / "local-storage" / "runs" / NAME / "observation.json")
    if not row.get("words"):
        return row
    report = OUT / "local-storage" / "runs" / NAME / "evaluated-report.md"
    if sha(report) != row["report_sha256"]:
        raise RuntimeError("race_report_hash_mismatch")
    target = OUT / "local-storage" / "race" / NAME
    target.mkdir(parents=True, exist_ok=True)
    with (target / "score.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result_path = target / "result.json"
        if not result_path.exists() and not (target / "attempt.json").exists():
            support.validate_race_inputs(manifest()["cells"][0])
            driver = load("pinned_race_driver", OUT / "race_driver.py")
            driver.ROOT = REPO
            driver.OUT = OUT
            driver.cell_directory = lambda cell: OUT / "local-storage" / "runs" / cell["name"]
            save(target / "attempt.json", dict(started_at=now(), report_sha256=row["report_sha256"]))
            try:
                driver.race_score(manifest()["cells"][0])
            except Exception as error:
                save(target / "error.json", dict(error_type=type(error).__name__, automatic_retry=False))
        calls = [read(p) for p in (target / "calls").glob("*.json") if not p.name.endswith((".pending.json", ".error.json"))]
        row["race_tokens"] = sum(c.get("usage", {}).get("total_tokens", 0) for c in calls)
        if result_path.exists():
            result = read(result_path)
            if result["report_sha256"] != row["report_sha256"]:
                raise RuntimeError("race_result_hash_mismatch")
            row.update(race_status="completed", race_scores=result["scores"], race_overall_percent=result["scores"]["overall_score"] * 100)
        else:
            row.update(race_status="attempt_incomplete_requires_review", race_overall_percent=None)
        save(OUT / "local-storage" / "runs" / NAME / "observation.json", row)
    return row


def summary():
    path = OUT / "local-storage" / "runs" / NAME / "observation.json"
    row = read(path) if path.exists() else dict(name=NAME, status="not_started", words=None)
    save(OUT / "local-storage" / "results.json", row)
    if row.get("words"):
        outline = row.get("outline") or {}
        race_display = f"{row['race_overall_percent']:.2f}" if row.get("race_overall_percent") is not None else "待评分"
        race_token_display = f"{row['race_tokens'] / 10000:.2f}" if row.get("race_tokens") is not None else "待评分"
        lines = [
            "# Qwen3.8-27B：case17一万档对照",
            "",
            "| 项目 | 实测 |",
            "|---|---:|",
            "| 最终目标 | 10000 |",
            f"| Outline选定正文预算 | {outline.get('selected_body_target_words')} |",
            f"| 可写节点 / 总论点 | {outline.get('writable_nodes')} / {outline.get('total_arguments')} |",
            f"| 最终全文估算 | {row['words']} |",
            f"| 偏差 | {row['deviation_percent']}% |",
            f"| 生成Token/万（含缓存输入） | {row['generation_tokens'] / 10000:.2f} |",
            f"| RACE ×100 | {race_display} |",
            f"| 评分Token/万（另计） | {race_token_display} |",
            f"| 原生正式交付 | {row['formal_delivered']} |",
            "",
            "字数为最终引用显示后的mixed-word-estimate-v1估算，非桌面Word实测。",
            "生成Token含原生修复；评分Token单列。RACE沿用固定官方流程与DeepSeek评委，非官方榜单，FACT未运行。",
            "Qwen与DeepSeek同题探索对照，各一次；检索随机性未控制，不能据此断言模型优劣或跨题稳定性。",
        ]
        (OUT / "local-storage" / "RESULTS.md").write_text("\n".join(lines) + "\n")
    return row


def qwen_preflight():
    import urllib.request, socket
    from dotenv import load_dotenv
    load_dotenv(REPO / '.env', override=True)
    for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
        os.environ.pop(key, None)
    checks = []
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open('http://127.0.0.1:8075/v1/models', timeout=10) as response:
            ids = [v['id'] for v in json.load(response)['data']]
        checks.append(dict(service='qwen', reachable='qwen3.8-27b' in ids, model_ids=ids))
    except Exception as error:
        checks.append(dict(service='qwen', reachable=False, error_type=type(error).__name__))
    try:
        with socket.create_connection(('api.deepseek.com',443),timeout=10): pass
        checks.append(dict(service='search_and_judge', reachable=True))
    except Exception as error:
        checks.append(dict(service='search_and_judge', reachable=False,error_type=type(error).__name__))
    result=dict(checks=checks,credentials_configured=bool(os.getenv('LLM_API_KEY')),ready=bool(os.getenv('LLM_API_KEY')) and all(c['reachable'] for c in checks),model_calls_started=False)
    save(OUT / 'local-storage/preflight.json',result)
    return result

support.preflight = qwen_preflight


def start():
    with (OUT / "local-storage" / "supervisor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest()
        check = support.preflight()
        if not check["ready"]:
            save(OUT / "local-storage" / "status.json", dict(status="blocked_preflight", details=check, model_generation_started=False))
            print("Network preflight blocked; no model calls started.", flush=True)
            return 2
        directory = OUT / "local-storage" / "runs" / NAME
        if (directory / "attempt.json").exists() or (directory / "checkpoints.sqlite").exists():
            raise RuntimeError("existing_attempt_refuse_paid_restart; inspect existing artifacts")
        # Validate official scoring inputs before spending on generation.
        support.validate_race_inputs(manifest()["cells"][0])
        save(directory / "attempt.json", dict(started_at=now()))
        save(OUT / "local-storage" / "status.json", dict(status="running", current=NAME, final_target=10000))
        native = native_for(10000)
        with (directory / "worker.log").open("w") as log:
            result = native.run_worker_process([sys.executable, "-B", str(OUT / "run.py"), "--worker"], directory=directory, log=log, timeout_seconds=support.TIMEOUT + 90, no_progress_seconds=1860)
        save(directory / "process-result.json", result)
        measured = subprocess.run([sys.executable, "-B", str(OUT / "run.py"), "--measure"], check=False)
        if measured.returncode:
            save(OUT / "local-storage" / "status.json", dict(status="measurement_failed", returncode=measured.returncode, generation_restarted=False))
            return 3
        row = summary()
        if row.get("words") and manifest().get("race_requested", False):
            save(OUT / "local-storage" / "status.json", dict(status="scoring", current=NAME, words=row["words"]))
            scoring = subprocess.run([sys.executable, "-B", str(OUT / "run.py"), "--score"], check=False)
            row = summary()
            status = "completed" if scoring.returncode == 0 and row.get("race_status") == "completed" else "generation_completed_race_incomplete"
        elif row.get("words"):
            status = "generation_completed"
        else:
            status = "stopped_without_complete_report"
        save(OUT / "local-storage" / "status.json", dict(status=status, native_process=result, observation=row, automatic_restart=False))
        return 0 if status in {"completed", "generation_completed"} else 3


if __name__ == "__main__":
    support.configure_storage()
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    for flag in ["start", "worker", "measure", "score", "preflight", "summary"]:
        group.add_argument("--" + flag, action="store_true")
    args = parser.parse_args()
    if args.worker:
        support.worker(NAME)
    elif args.measure:
        observe()
        summary()
    elif args.score:
        manifest()
        score()
        summary()
    elif args.start:
        from supervision import lifecycle
        with lifecycle(OUT / "local-storage"):
            exit_code = start()
        sys.exit(exit_code)
    elif args.preflight:
        manifest()
        print(json.dumps(support.preflight(), ensure_ascii=False))
    else:
        manifest()
        print(json.dumps(summary(), ensure_ascii=False))
