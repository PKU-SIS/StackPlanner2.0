"""Evaluate immutable native deliveries without importing or executing an agent."""

from __future__ import annotations

import dataclasses
import hashlib
import importlib
import importlib.metadata
import platform
import json
import math
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

from dcc_harness.data import Task, validate_disjoint
from dcc_harness.datasets.benchmark_plugins import BenchmarkPlugin, dataset_plugins
from dcc_harness.evaluation.benchmarks import PROTOCOL, retrieval_diagnostics


@dataclasses.dataclass
class EvaluationContext:
    """One context per benchmark; setup may cache expensive local scorers here."""

    judge: Any = None
    exact_proxy: bool = False
    options: dict[str, Any] = dataclasses.field(default_factory=dict)
    resources: dict[str, Any] = dataclasses.field(default_factory=dict)
    diagnostic_partial: bool = False
    provenance: dict[str, Any] = dataclasses.field(default_factory=dict)


def environment_versions() -> dict[str, str]:
    # Distribution discovery follows sys.path priority. Shared/read-only site
    # directories may contain another version: keep the first, like imports do.
    versions = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"]
        if name:
            versions.setdefault(name, dist.version)
    return versions


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def harness_fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    return json_hash(
        {str(p.relative_to(root)): file_hash(p) for p in sorted(root.rglob("*.py"))}
    )


def json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def write_json(path: Path, value: Any) -> None:
    body = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def task_identity(task: Task) -> dict:
    return {
        "task_id": task.id,
        "benchmark": task.benchmark,
        "split": task.split,
        "input_sha256": task.input_sha256(),
    }


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("prediction rows must be JSON objects")
    return rows


def validate_prediction_rows(rows: list[dict], tasks: list[Task]) -> None:
    expected = {task.id: task_identity(task) for task in tasks}
    seen = set()
    for row in rows:
        key = row.get("task_id")
        if key not in expected or key in seen:
            raise ValueError("unknown or duplicate prediction ID")
        seen.add(key)
        if row.get("protocol") != PROTOCOL or any(
            row.get(k) != v for k, v in expected[key].items()
        ):
            raise ValueError(
                "prediction does not match task, split, public input, or protocol"
            )
        if row.get("status") == "completed":
            text = row.get("delivery")
            if not isinstance(text, str) or hashlib.sha256(
                text.encode()
            ).hexdigest() != row.get("delivery_sha256"):
                raise ValueError(
                    "completed delivery is missing or its checksum differs"
                )


def inference_summary(rows: list[dict], expected_tasks: int) -> dict:
    completed = sum(row.get("status") == "completed" for row in rows)
    return {
        "protocol": PROTOCOL,
        "phase": "inference",
        "retrieval": retrieval_diagnostics(rows),
        "tasks": expected_tasks,
        "predictions": len(rows),
        "completed": completed,
        "prediction_coverage": len(rows) / expected_tasks if expected_tasks else 0.0,
        "completion_rate": completed / expected_tasks if expected_tasks else 0.0,
        "status_counts": dict(Counter(row["status"] for row in rows)),
        "complete_split": completed == expected_tasks and len(rows) == expected_tasks,
    }


def plugin_identity(plugin: BenchmarkPlugin, *, phase: str) -> dict:
    functions = (
        (plugin.prepare_task, plugin.prepare_inputs, plugin.validate_dataset)
        if phase == "inference"
        else (
            plugin.parse_answer,
            plugin.score_task,
            plugin.aggregate,
            plugin.setup_evaluation,
            plugin.validate_dataset,
        )
    )
    modules = {function.__module__ for function in functions if function is not None}
    if phase == "inference":
        modules.update(tool.use.split(":", 1)[0] for tool in plugin.tools)
    sources = {}
    for name in sorted(modules):
        source = getattr(importlib.import_module(name), "__file__", None)
        sources[name] = (
            file_hash(Path(source)) if source and Path(source).is_file() else "unknown"
        )
    result = {"sources": sources}
    if phase == "evaluation":
        resources = {}
        for index, path in enumerate(plugin.evaluation_resources):
            path = Path(path)
            if not path.exists():
                raise FileNotFoundError(f"evaluation resource missing: {path.name}")
            files = (
                sorted(p for p in path.rglob("*") if p.is_file())
                if path.is_dir()
                else [path]
            )
            resources[f"{index}:{path.name}"] = json_hash(
                {
                    str(p.relative_to(path)) if path.is_dir() else path.name: file_hash(
                        p
                    )
                    for p in files
                }
            )
        result.update(
            version=plugin.evaluation_version,
            requires_judge=plugin.requires_judge,
            resources=resources,
        )
    return result


def macro_average(rows: list[dict], keys: tuple[str, ...]) -> dict:
    """Opt-in helper: null/missing values are excluded, with per-metric denominators."""
    values = {}
    denominators = {}
    for key in keys:
        numbers = [
            row["metrics"][key] for row in rows if row["metrics"].get(key) is not None
        ]
        if any(
            isinstance(n, bool)
            or not isinstance(n, (float, int))
            or not math.isfinite(n)
            for n in numbers
        ):
            raise ValueError(f"non-finite/non-numeric metric: {key}")
        values[key] = sum(numbers) / len(numbers) if numbers else None
        denominators[key] = len(numbers)
    return {**values, "metric_denominators": denominators}


def evaluate_run(
    tasks: list[Task],
    *,
    run_dir: Path,
    output_dir: Path,
    specs: tuple[str, ...] = (),
    benchmark: str | None = None,
    judge=None,
    judge_identity: dict | None = None,
    exact_proxy: bool = False,
    allow_partial: bool = False,
    options: dict | None = None,
) -> dict:
    """No SP invocation. Strict mode refuses aggregate scores on incomplete runs.

    Each evaluation uses a new output directory; inference files are read-only.
    Invalid predictions remain failures, never fabricated empty answers.
    """
    if not tasks or len({t.id for t in tasks}) != len(tasks):
        raise ValueError("evaluation needs nonempty tasks with unique IDs")
    if exact_proxy and judge is not None:
        raise ValueError("exact proxy and a real judge are mutually exclusive")
    validate_disjoint(tasks)
    if options is not None and (
        not isinstance(options, dict)
        or any(not isinstance(v, dict) for v in options.values())
    ):
        raise ValueError("evaluation options must be objects keyed by benchmark")
    run_dir, output_dir = Path(run_dir), Path(output_dir)
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("protocol") != PROTOCOL or manifest.get("phase") != "inference":
        raise ValueError("evaluation requires a v3 inference manifest")
    expected_all = manifest.get("task_inputs")
    if not isinstance(expected_all, list) or len(
        {r["task_id"] for r in expected_all}
    ) != len(expected_all):
        raise ValueError("invalid inference task manifest")
    expected = [
        r for r in expected_all if benchmark is None or r["benchmark"] == benchmark
    ]
    selected = [t for t in tasks if benchmark is None or t.benchmark == benchmark]
    if not selected or sorted(expected, key=lambda r: r["task_id"]) != sorted(
        [task_identity(t) for t in selected], key=lambda r: r["task_id"]
    ):
        raise ValueError(
            "evaluation tasks must exactly match the selected inference manifest"
        )
    raw = read_rows(run_dir / "predictions.jsonl")
    # Reject duplicate/foreign records even when evaluating one benchmark.
    manifest_by_id = {r["task_id"]: r for r in expected_all}
    if len({r.get("task_id") for r in raw}) != len(raw) or any(
        r.get("task_id") not in manifest_by_id for r in raw
    ):
        raise ValueError("unknown or duplicate prediction ID")
    for row in raw:
        if row.get("protocol") != PROTOCOL or any(
            row.get(k) != v for k, v in manifest_by_id[row["task_id"]].items()
        ):
            raise ValueError("prediction disagrees with inference manifest")
    rows = [r for r in raw if benchmark is None or r.get("benchmark") == benchmark]
    validate_prediction_rows(rows, selected)
    registry = dataset_plugins(specs)
    plugins = {}
    for name in sorted({t.benchmark for t in selected}):
        if name not in registry:
            raise ValueError(f"unknown dataset plugin: {name}")
        plugin = registry[name]
        if (
            plugin.parse_answer is None
            or plugin.score_task is None
            or plugin.aggregate is None
        ):
            raise ValueError(
                f"evaluation plugin requires parse_answer, score_task, aggregate: {name}"
            )
        if plugin.validate_dataset:
            plugin.validate_dataset([t for t in selected if t.benchmark == name])
        if (
            plugin.requires_judge
            and judge is None
            and not (exact_proxy and plugin.supports_exact_proxy)
        ):
            raise ValueError(
                f"{name} requires a judge or explicit diagnostic --exact-proxy"
            )
        plugins[name] = plugin
    if set(options or {}) - set(plugins):
        raise ValueError("evaluation options contain an unselected benchmark")
    identity = {
        "protocol": PROTOCOL,
        "phase": "evaluation",
        "inference_manifest_sha256": hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest(),
        "predictions_sha256": json_hash(raw),
        "evaluation_tasks_sha256": json_hash([t.to_dict() for t in selected]),
        "plugins": {
            name: plugin_identity(p, phase="evaluation") for name, p in plugins.items()
        },
        "options": options or {},
        "allow_partial": allow_partial,
        "judge": judge_identity
        if any(p.requires_judge for p in plugins.values())
        else None,
        "exact_proxy": exact_proxy,
        "harness_sha256": harness_fingerprint(),
        "python": platform.python_version(),
        "packages": environment_versions(),
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    write_json(output_dir / "manifest.json", identity)
    by_id = {r["task_id"]: r for r in rows}
    parsed = []
    for task in selected:
        result = {
            "task_id": task.id,
            "benchmark": task.benchmark,
            "split": task.split,
            "status": "ready",
        }
        prediction = by_id.get(task.id)
        if prediction is None or prediction.get("status") != "completed":
            result.update(
                status="missing_prediction"
                if prediction is None
                else "inference_failed",
                inference_status=prediction.get("status") if prediction else None,
            )
        else:
            try:
                answer = plugins[task.benchmark].parse_answer(
                    task.public_input(), prediction["delivery"]
                )
                json.dumps(answer, allow_nan=False)
                result["answer"] = answer
            except (ValueError, TypeError) as exc:
                result.update(
                    status="invalid_prediction", error_type=type(exc).__name__
                )
            except Exception as exc:
                result.update(
                    status="evaluation_error",
                    stage="parse",
                    error_type=type(exc).__name__,
                )
        parsed.append(result)
    failures = [r for r in parsed if r["status"] != "ready"]
    report = {
        "protocol": PROTOCOL,
        "phase": "evaluation",
        "expected_tasks": len(selected),
        "complete_split": False,
        "diagnostic_partial": allow_partial,
        "preflight_failures": failures,
        "benchmarks": {},
    }
    if failures and not allow_partial:
        report["status"] = "preflight_failed"
        write_json(output_dir / "report.json", report)
        return report
    contexts = {}
    setup_errors = {}
    for name, plugin in plugins.items():
        context = EvaluationContext(
            judge=judge if plugin.requires_judge else None,
            exact_proxy=exact_proxy,
            options=dict((options or {}).get(name, {})),
            diagnostic_partial=allow_partial,
        )
        contexts[name] = context
        try:
            if plugin.setup_evaluation:
                plugin.setup_evaluation(context)
        except Exception as exc:
            setup_errors[name] = type(exc).__name__
    with (output_dir / "scores.jsonl").open("x", encoding="utf-8") as handle:
        for task, row in zip(selected, parsed, strict=True):
            if row["status"] == "ready":
                started = time.monotonic()
                try:
                    if task.benchmark in setup_errors:
                        row.update(
                            status="evaluation_error",
                            error_type=setup_errors[task.benchmark],
                        )
                    else:
                        metrics = plugins[task.benchmark].score_task(
                            task, by_id[task.id]["delivery"], contexts[task.benchmark]
                        )
                        if not isinstance(metrics, dict):
                            raise ValueError("scorer must return an object")
                        json.dumps(metrics, allow_nan=False)
                        row.update(status="scored", metrics=metrics)
                except Exception as exc:
                    row.update(status="evaluation_error", error_type=type(exc).__name__)
                row["elapsed_seconds"] = time.monotonic() - started
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
        os.fsync(handle.fileno())
    complete = all(row["status"] == "scored" for row in parsed)
    aggregation_failed = False
    for name, plugin in plugins.items():
        scored = [
            r for r in parsed if r["benchmark"] == name and r["status"] == "scored"
        ]
        scored_ids = {r["task_id"] for r in scored}
        group_tasks = [t for t in selected if t.benchmark == name]
        group = {
            "expected_tasks": len(group_tasks),
            "scored_tasks": len(scored),
            "aggregate": None,
        }
        if scored and (complete or allow_partial):
            try:
                value = plugin.aggregate(
                    [t for t in group_tasks if t.id in scored_ids],
                    scored,
                    contexts[name],
                )
                if not isinstance(value, dict):
                    raise ValueError("aggregate must return an object")
                json.dumps(value, allow_nan=False)
                group["aggregate"] = value
            except Exception as exc:
                aggregation_failed = True
                group.update(status="aggregation_error", error_type=type(exc).__name__)
        report["benchmarks"][name] = group
    report.update(
        complete_split=complete and not aggregation_failed,
        status="completed"
        if complete and not aggregation_failed
        else "evaluation_failed",
        status_counts=dict(Counter(r["status"] for r in parsed)),
    )
    report["evaluation_provenance"] = {
        name: context.provenance for name, context in contexts.items()
    }
    write_json(output_dir / "report.json", report)
    return report
