# ruff: noqa: E402
# These standalone entry points bootstrap the source checkout before imports.
"""Score previously collected SP deliveries; never invoke SP or its model API."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dcc_harness.paths import native_harness_path

sys.path.insert(0, str(native_harness_path(ROOT)))

from dcc_harness.data import load_tasks
from dcc_harness.datasets.benchmark_plugins import resolve_dataset_plugin
from dcc_harness.evaluation.offline import evaluate_run


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
            "use a plain HTTP(S) judge URL without credentials or query"
        )
    return value.rstrip("/")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--tasks",
        type=Path,
        required=True,
        help="Same public tasks, with evaluation-only gold/metadata",
    )
    p.add_argument(
        "--run-dir", type=Path, required=True, help="Read-only v3 inference run"
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="New evaluation directory; existing outputs are never overwritten",
    )
    p.add_argument("--benchmark", help="Evaluate only this benchmark from the run")
    p.add_argument("--dataset-plugin", action="append", default=[])
    p.add_argument(
        "--options",
        type=Path,
        help="JSON object keyed by benchmark, supplied only to evaluation",
    )
    p.add_argument(
        "--allow-partial",
        action="store_true",
        help="Explicit diagnostic evaluation of valid predictions only",
    )
    p.add_argument(
        "--judge-base-url", type=endpoint, default=os.environ.get("DCC_JUDGE_BASE_URL")
    )
    p.add_argument("--judge-model", default=os.environ.get("DCC_JUDGE_MODEL"))
    p.add_argument("--judge-revision", default="unknown")
    p.add_argument(
        "--exact-proxy",
        action="store_true",
        help="Explicit WideSearch exact-match proxy, not judged results",
    )
    return p


def main_args(args) -> int:
    tasks = load_tasks(args.tasks)
    specs = tuple(args.dataset_plugin)
    selected = {
        task.benchmark
        for task in tasks
        if args.benchmark is None or task.benchmark == args.benchmark
    }
    needs_judge = any(
        resolve_dataset_plugin(name, specs).requires_judge for name in selected
    )
    judge = None
    judge_identity = None
    if needs_judge:
        if args.exact_proxy:
            judge_identity = {"protocol": "strict_exact_proxy"}
        else:
            if not args.judge_base_url or not args.judge_model:
                raise ValueError(
                    "selected benchmark requires --judge-base-url and --judge-model (or --exact-proxy)"
                )
            from dcc_harness.widesearch_metrics import OpenAIJudge

            judge = OpenAIJudge(
                endpoint(args.judge_base_url),
                args.judge_model,
                os.environ.get("DCC_JUDGE_API_KEY", "EMPTY"),
            )
            judge_identity = {
                "protocol": "custom_judge",
                "base_url": endpoint(args.judge_base_url),
                "model": args.judge_model,
                "revision": args.judge_revision,
            }
    options = (
        json.loads(args.options.read_text(encoding="utf-8")) if args.options else {}
    )
    if not isinstance(options, dict) or any(
        not isinstance(value, dict) for value in options.values()
    ):
        raise ValueError("evaluation options must be objects keyed by benchmark")
    report = evaluate_run(
        tasks,
        run_dir=args.run_dir,
        output_dir=args.output_dir,
        specs=specs,
        benchmark=args.benchmark,
        judge=judge,
        judge_identity=judge_identity,
        exact_proxy=args.exact_proxy,
        allow_partial=args.allow_partial,
        options=options,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report["complete_split"] else 2


def main() -> int:
    p = parser()
    try:
        return main_args(p.parse_args())
    except (ValueError, OSError) as exc:
        p.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
