"""Inspect CLAUSE predictions and optionally export only accepted JSON files."""

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/packages/harness")]
from dcc_harness.datasets.clause_policy import validated_answer  # noqa: E402


def inspect_run(run, export=None):
    paths = (
        [run / "predictions.jsonl"]
        if not (run / "shards").is_dir()
        else sorted((run / "shards").glob("*/predictions.jsonl"))
    )
    seen = set()
    invalid = {}
    for path in paths:
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.endswith("\n"):  # a writer may still be appending
                    break
                row = json.loads(line)
                key = row["task_id"]
                if (
                    not key
                    or key in (".", "..")
                    or Path(key).name != key
                    or key in seen
                ):
                    raise ValueError("duplicate or unsafe task ID")
                seen.add(key)
                try:
                    validated_answer(row)
                except ValueError as exc:
                    invalid[key] = row.get("detail") or str(exc)
                    continue
                if export is not None:
                    export.mkdir(parents=True, exist_ok=True)
                    target = export / (key + ".json")
                    content = row["delivery"].encode("utf-8")
                    if target.exists():
                        if target.read_bytes() != content:
                            raise ValueError(
                                "export directory contains a different answer for "
                                + key
                            )
                        continue
                    with tempfile.NamedTemporaryFile(dir=export, delete=False) as tmp:
                        tmp.write(content)
                    try:
                        os.link(tmp.name, target)
                    finally:
                        os.unlink(tmp.name)
    return {
        "recorded": len(seen),
        "valid": len(seen) - len(invalid),
        "invalid": invalid,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--export-dir", type=Path)
    parser.add_argument("--interval", type=float, default=0)
    args = parser.parse_args()
    while True:
        print(json.dumps(inspect_run(args.run, args.export_dir)), flush=True)
        if args.interval <= 0:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
