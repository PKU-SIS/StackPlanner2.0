#!/usr/bin/env python3
"""Explicitly selected, isolated SP2 tender-audit cases; batch mode is opt-in."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.experiments.tender_audit.runner import DEFAULT_OUTPUT, run_cli


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checks", type=Path, required=True)
    parser.add_argument("--ground-truth", type=Path, required=True)
    parser.add_argument("--case", type=int, action="append")
    parser.add_argument("--full-batch", action="store_true")
    parser.add_argument("--model", default="qwen3-8-27b")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--batch-name")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--no-progress-timeout", type=float, default=180)
    parser.add_argument("--timeout-multiplier", type=int, choices=(1, 2), default=1, help="Scale all experiment execution/network timeouts; token/retry budgets stay unchanged")
    parser.add_argument(
        "--llm-request-timeout",
        type=float,
        default=150.0,
        help="Native per-request wall deadline, shorter than no-progress timeout; 0 disables",
    )
    parser.add_argument("--worker-input", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.timeout <= 0 or args.no_progress_timeout <= 0 or args.max_tokens <= 0:
        parser.error("Timeouts and token limit must be positive")
    try:
        return run_cli(args)
    except (ValueError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
