#!/usr/bin/env python3
"""Collect disjoint, identically configured native inference shards for scoring."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dcc_harness.data import load_tasks  # noqa: E402
from dcc_harness.evaluation.shards import merge_runs  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args()
    result = merge_runs(
        load_tasks(args.tasks),
        args.run_dir,
        args.output_dir,
        allow_missing=args.allow_missing,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
