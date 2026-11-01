"""Create or materialize independent train/test bundles without changing a baseline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dcc_harness.preprocessing.pipeline import extension  # noqa: E402
from dcc_harness.preprocessing.splits import export_splits  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--train-fraction", default="0.8")
    create.add_argument("--seed", type=int, default=42)
    create.add_argument("--builder", default="dcc_harness.preprocessing.splits:make_definition", help="trusted module:function")
    create.add_argument("--group-key", help="trusted module:function (NBA builder)")
    create.add_argument("--stratum-key", help="trusted module:function (NBA builder)")
    export = commands.add_parser("export")
    export.add_argument("--definition", type=Path, required=True)
    for command in (create, export):
        command.add_argument("--bundle", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "create":
        if args.output.exists():
            raise FileExistsError(f"definition already exists: {args.output}")
        options = {key: getattr(args, key) for key in ("train_fraction", "seed", "group_key", "stratum_key") if getattr(args, key) is not None}
        definition = extension(args.builder, {})(args.bundle, **options)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(definition, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        print(json.dumps(definition["statistics"], ensure_ascii=False, indent=2))
    else:
        print(json.dumps(export_splits(args.bundle, json.loads(args.definition.read_text()), args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
