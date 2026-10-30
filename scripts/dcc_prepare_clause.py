# ruff: noqa: E402
"""Prepare CLAUSE from a pinned release, verified directory, or public upstream."""

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dcc_harness.datasets.clause_data import prepare


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--archive", type=Path, help="Existing prepared CLAUSE release tar.gz"
    )
    source.add_argument(
        "--ready-dir",
        type=Path,
        help="Prepared release directory; every file is verified",
    )
    source.add_argument(
        "--download",
        action="store_true",
        help="Download fixed upstream and NLTK, reconstruct the release, then convert",
    )
    source.add_argument(
        "--source-archive",
        type=Path,
        help="Original upstream tar.gz (not the prepared release); reconstruct offline source",
    )
    p.add_argument("--output-dir", type=Path, default=Path(".benchmark-data/clause"))
    p.add_argument("--prompt", choices=("l1", "l2"), default="l1")
    p.add_argument(
        "--work-dir",
        type=Path,
        default=Path(".benchmark-data/clause-rebuild-cache"),
        help="Download cache and temporary source reconstruction; allow about 2 GB free",
    )
    p.add_argument(
        "--resources-from",
        type=Path,
        help="Reuse verified NLTK from an existing release resources/; only with --download/--source-archive",
    )
    return p


def main_args(args):
    output = args.output_dir.absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError("CLAUSE output already exists; choose a new directory")
    reconstruct = args.download or args.source_archive is not None
    if args.resources_from is not None and not reconstruct:
        raise ValueError("--resources-from requires --download or --source-archive")
    if not reconstruct:
        return prepare(
            args.archive, output, variant=args.prompt, ready_dir=args.ready_dir
        )
    from dcc_harness.datasets.clause_source import rebuild

    work = args.work_dir.resolve()
    if work == output or output in work.parents:
        raise ValueError("Work directory must not be inside the new output directory")
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="prepare-", dir=work) as temporary:
        ready = Path(temporary) / "ready"
        provenance = rebuild(
            ready,
            work,
            source_archive=args.source_archive,
            resources_from=args.resources_from,
        )
        # The source-ready staging directory is temporary; retain content identities,
        # not a path that will disappear after the public/private handoff succeeds.
        provenance = {
            key: value for key, value in provenance.items() if key != "output"
        }
        return prepare(
            None,
            output,
            variant=args.prompt,
            ready_dir=ready,
            source_provenance=provenance,
        )


def main():
    p = parser()
    args = p.parse_args()
    try:
        print(json.dumps(main_args(args), ensure_ascii=False, indent=2))
    except (ValueError, OSError, RuntimeError) as exc:
        p.error(str(exc))


if __name__ == "__main__":
    main()
