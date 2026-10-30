# ruff: noqa: E402
# These standalone entry points bootstrap the source checkout before imports.
"""Collect benchmark deliveries with native SP2.0; never parse or score them."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import json
import os
import platform
import random
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dcc_harness.paths import native_harness_path

sys.path.insert(0, str(native_harness_path(ROOT)))

from dcc_harness.datasets.benchmark_plugins import (
    configure_dataset_app,
    resolve_dataset_plugin,
)
from dcc_harness.datasets.benchmarks import load_tasks, validate_disjoint
from dcc_harness.evaluation.benchmark_runner import Settings, run_task
from dcc_harness.evaluation.benchmarks import PROTOCOL
from dcc_harness.evaluation.offline import (
    inference_summary,
    plugin_identity,
    task_identity,
    validate_prediction_rows,
)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def directory_digest(root: Path) -> str:
    files = (
        [
            p
            for p in root.rglob("*")
            if p.is_file() and not {".git", "__pycache__"}.intersection(p.parts)
        ]
        if root.exists()
        else []
    )
    payload = "\n".join(
        f"{p.relative_to(root).as_posix()}:{digest(p)}" for p in sorted(files)
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def source_digest(root: Path = ROOT) -> str:
    roots = [
        root / "dcc_harness",
        native_harness_path(root) / "deerflow",
        root / "configs/stackplanner_skills",
    ]
    payload = "\n".join(
        f"{p.relative_to(root).as_posix()}:{directory_digest(p)}" for p in roots
    )
    script = root / "scripts/infer_sp_benchmarks.py"
    if script.exists():
        payload += "\n" + digest(script)
    return hashlib.sha256(payload.encode()).hexdigest()


def retrieval_identity() -> dict:
    # Endpoint/proxy URIs may embed credentials. Hash their values rather than
    # persisting them, while still detecting a changed retrieval configuration.
    private_urls = (
        "DCC_SEARCH_URL",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    )
    return {
        "backend": os.environ.get("DCC_SEARCH_BACKEND", "public_web"),
        "timeout": os.environ.get("DCC_WEB_TIMEOUT", "30"),
        "endpoint_proxy_sha256": {
            key: hashlib.sha256(os.environ.get(key, "").encode()).hexdigest()
            for key in private_urls
        },
    }


def ensure_manifest(root: Path, identity: dict, *, resume: bool) -> None:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "manifest.json"
    if path.exists():
        if not resume:
            raise ValueError(
                "run already exists; use --resume with identical configuration or a new directory"
            )
        if json.loads(path.read_text(encoding="utf-8")) != identity:
            raise ValueError(
                "resume rejected: data, source, model, judge, or execution configuration differs"
            )
    else:
        if resume or any(root.iterdir()):
            raise ValueError("refusing to adopt a non-native or nonempty run directory")
        with path.open("x", encoding="utf-8") as handle:
            json.dump(identity, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())


def endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise argparse.ArgumentTypeError(
            "use a plain HTTP(S) base URL without credentials or query parameters"
        )
    return value.rstrip("/")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tasks", type=Path)
    p.add_argument(
        "--dataset-source",
        help="Dataset provenance; official WideSearch retains its 200-task test check",
    )
    p.add_argument(
        "--benchmark",
        default="widesearch",
        help="Dataset plugin for --check-config/--check-search; evaluation selects each task's benchmark",
    )
    p.add_argument(
        "--dataset-plugin",
        action="append",
        default=[],
        help="Trusted module:plugin_or_factory; repeat to register more datasets for this run",
    )
    p.add_argument("--run-dir", type=Path)
    p.add_argument(
        "--config", type=Path, default=ROOT / "configs/stackplanner_benchmarks.yaml"
    )
    p.add_argument(
        "--base-url", type=endpoint, default=os.environ.get("DCC_MODEL_BASE_URL")
    )
    p.add_argument("--model", default=os.environ.get("DCC_MODEL_NAME"))
    p.add_argument("--api-key-env", default="DCC_MODEL_API_KEY")
    p.add_argument(
        "--max-actions",
        "--max-iterations",
        dest="max_iterations",
        type=int,
        default=20,
        help="Native SP action budget (legacy flag --max-iterations is an alias)",
    )
    p.add_argument("--max-tokens", type=int, default=4096)
    p.add_argument("--timeout", type=float, default=900.0)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--top-p", type=float, default=1.0)
    p.add_argument("--thinking", choices=("enabled", "disabled"))
    p.add_argument(
        "--recover-qwen-tool-calls",
        action="store_true",
        help="Opt in only for Qwen/vLLM textual calls",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Continue missing tasks only; recorded failures remain immutable",
    )
    p.add_argument(
        "--check-config",
        action="store_true",
        help="Offline tool/credential-presence check; no model request",
    )
    p.add_argument(
        "--check-search",
        action="store_true",
        help="Test public retrieval separately; no model request",
    )
    return p


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    # A partial last append is not silently ignored or overwritten. The
    # per-attempt result remains available for explicit recovery/inspection.
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


async def main_async(args) -> int:
    from deerflow.config.app_config import AppConfig, set_app_config
    from deerflow.tools import get_available_tools

    from dcc_harness.tools import InfrastructureError, WebTools

    if args.run_dir:
        os.environ["DEER_FLOW_HOME"] = str((args.run_dir / "native-state").resolve())
    specs = tuple(args.dataset_plugin)
    check_plugin = resolve_dataset_plugin(args.benchmark, specs)
    app = configure_dataset_app(AppConfig.from_file(str(args.config)), check_plugin)
    set_app_config(app)
    tools = get_available_tools(app_config=app, include_mcp=False)
    if args.check_config:
        print(
            json.dumps(
                {
                    "protocol": PROTOCOL,
                    "benchmark": check_plugin.name,
                    "tools": [t.name for t in tools],
                    "host_bash": app.sandbox.allow_host_bash,
                    "model": args.model,
                    "base_url": args.base_url,
                    "credential_present": bool(os.environ.get(args.api_key_env)),
                    "subagents": app.subagents.model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.check_search:
        if "web_search" not in {tool.name for tool in check_plugin.tools}:
            raise ValueError(
                f"dataset plugin has no web search tool: {check_plugin.name}"
            )
        try:
            result = await WebTools().web_search("IANA ICMP Type Numbers", count=3)
        except (InfrastructureError, OSError) as exc:
            print(
                json.dumps(
                    {"status": "infrastructure_error", "error_type": type(exc).__name__}
                )
            )
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("results") else 1
    if not (
        args.tasks
        and args.run_dir
        and args.dataset_source
        and args.base_url
        and args.model
    ):
        raise ValueError(
            "inference requires --tasks --run-dir --dataset-source --base-url --model"
        )
    if args.max_iterations < 1 or args.max_tokens < 1 or args.timeout <= 0:
        raise ValueError("execution budgets must be positive")
    if not os.environ.get(args.api_key_env):
        raise ValueError(
            f"credential environment variable {args.api_key_env} is not set"
        )
    tasks = load_tasks(args.tasks)
    if not tasks:
        raise ValueError("task file is empty")
    validate_disjoint(tasks)
    if args.dataset_source == "widesearch-official" and (
        len(tasks) != 200
        or any(t.split != "test" or t.benchmark != "widesearch" for t in tasks)
    ):
        raise ValueError(
            "official evaluation requires the complete held-out 200-task test split"
        )
    settings = Settings(
        config=str(args.config.resolve()),
        base_url=args.base_url,
        model=args.model,
        api_key_env=args.api_key_env,
        max_iterations=args.max_iterations,
        max_tokens=args.max_tokens,
        timeout=args.timeout,
        seed=args.seed,
        temperature=args.temperature,
        top_p=args.top_p,
        thinking=args.thinking,
        recover_qwen_tool_calls=args.recover_qwen_tool_calls,
        dataset_plugins=specs,
    )
    from uuid import uuid4

    from deerflow.agents.lead_agent.prompt import get_enabled_skills_for_config

    from dcc_harness.evaluation.benchmark_runner import effective_app

    dataset_configs = {}
    for benchmark in sorted({task.benchmark for task in tasks}):
        plugin = resolve_dataset_plugin(benchmark, specs)
        if plugin.validate_dataset:
            plugin.validate_dataset(
                [task for task in tasks if task.benchmark == benchmark]
            )
        app = effective_app(settings, benchmark)
        skills = get_enabled_skills_for_config(app, user_id=f"manifest-{uuid4().hex}")
        dataset_configs[benchmark] = {
            "tools": [
                {"name": tool.name, "group": tool.group, "use": tool.use}
                for tool in app.tools
            ],
            "skills": [
                {
                    "name": skill.name,
                    "category": str(skill.category),
                    "sha256": directory_digest(skill.skill_dir),
                }
                for skill in sorted(skills, key=lambda skill: skill.name)
            ],
            "skills_root_sha256": directory_digest(Path(plugin.skills_path)),
            "plugin_identity": plugin_identity(plugin, phase="inference"),
        }
    app = effective_app(settings)
    set_app_config(app)
    normalized = json.dumps(
        [task_identity(t) for t in tasks], sort_keys=True, ensure_ascii=False
    )
    settings_record = dataclasses.asdict(settings)
    settings_record["dataset_plugins"] = list(settings.dataset_plugins)
    settings_record["config"] = (
        args.config.name
    )  # Contents, rather than host-specific path, identify the config.
    try:
        commit = (
            await asyncio.to_thread(
                subprocess.check_output,
                ["git", "rev-parse", "HEAD"],
                cwd=ROOT,
                text=True,
            )
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    identity = {
        "protocol": PROTOCOL,
        "phase": "inference",
        "task_inputs": [task_identity(t) for t in tasks],
        "dataset_source": args.dataset_source,
        "normalized_tasks_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
        "source_sha256": source_digest(),
        "code_head": commit,
        "config_sha256": digest(args.config),
        "settings": settings_record,
        "effective_app_config": {
            "models": [
                {"name": m.name, "model": m.model, "use": m.use} for m in app.models
            ],
            "tools": [
                {"name": t.name, "group": t.group, "use": t.use} for t in app.tools
            ],
            "sandbox": app.sandbox.model_dump(mode="json"),
            "subagents": app.subagents.model_dump(mode="json"),
        },
        "dataset_plugins": dataset_configs,
        "search": retrieval_identity(),
        "python": platform.python_version(),
        "platform": platform.system(),
        "concurrency": 1,
    }
    ensure_manifest(args.run_dir, identity, resume=args.resume)
    random.seed(args.seed)
    predictions = args.run_dir / "predictions.jsonl"
    rows = read_rows(predictions)
    validate_prediction_rows(rows, tasks)
    seen = {r["task_id"] for r in rows}
    for index, task in enumerate(tasks):
        if task.id in seen:
            continue
        attempt_dir = args.run_dir / "attempts" / f"{index:04d}"
        # A crash after result fsync but before the prediction append is
        # recoverable without rerunning an expensive model. Preserve traces.
        saved = attempt_dir / "result.json"
        if attempt_dir.exists():
            if not args.resume or not saved.is_file():
                raise ValueError(
                    f"incomplete attempt retained at {attempt_dir}; inspect it and start a new run"
                )
            row = json.loads(saved.read_text(encoding="utf-8"))
            validate_prediction_rows([row], [task])
        else:
            row = await run_task(task, settings, output_dir=attempt_dir)
        with predictions.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        rows.append(row)
        report = inference_summary(rows, expected_tasks=len(tasks))
        report.update(
            dataset_source=args.dataset_source,
            run_dir=str(args.run_dir),
        )
        temporary = args.run_dir / "report.tmp"
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        os.replace(temporary, args.run_dir / "report.json")
        print(
            json.dumps(
                {
                    "task_id": task.id,
                    "status": row["status"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    # Rebuild even when resume had no missing tasks: the last prediction may
    # have been fsynced just before a crash interrupted report publication.
    report = inference_summary(rows, expected_tasks=len(tasks))
    report.update(
        dataset_source=args.dataset_source,
        run_dir=str(args.run_dir),
    )
    temporary = args.run_dir / "report.tmp"
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    os.replace(temporary, args.run_dir / "report.json")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(r["status"] == "completed" for r in rows) else 2


def main() -> int:
    p = parser()
    args = p.parse_args()
    try:
        return asyncio.run(main_async(args))
    except (ValueError, FileNotFoundError) as exc:
        p.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
