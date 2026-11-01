#!/usr/bin/env python3
"""Prepare, index, render, verify, pack or install a complete CLAUSE release."""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from dcc_preprocess import bundle_lock, load_config
from dcc_harness.preprocessing import clause_release as release
from dcc_harness.preprocessing.pipeline import atomic_json
from dcc_harness.preprocessing.ragflow import index_bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "prepare",
            "index",
            "render",
            "verify",
            "pack",
            "install",
            "download",
            "audit",
        ],
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--descriptor", type=Path)
    parser.add_argument("--version", default="dcc-clause-eval3-v1")
    args = parser.parse_args()
    if args.command == "prepare":
        if not args.source or not args.config:
            parser.error("prepare requires --source and --config")
        release.prepare_all(args.source, args.root, load_config(args.config.resolve()))
        result = {"prepared": str(args.root)}
    elif args.command in {"install", "download"}:
        if not args.descriptor:
            parser.error("install/download requires --descriptor")
        descriptor = json.loads(args.descriptor.read_text())
        if args.command == "install":
            if not args.archive:
                parser.error("install requires --archive")
            result = release.install(args.archive, args.root, descriptor["sha256"])
        else:
            asset = descriptor["asset"]
            if Path(asset).name != asset:
                raise ValueError("invalid asset name")
            with tempfile.TemporaryDirectory(prefix="clause-download-") as tmp:
                subprocess.run(
                    [
                        "gh",
                        "release",
                        "download",
                        descriptor["tag"],
                        "--repo",
                        descriptor["repository"],
                        "--pattern",
                        asset,
                        "--dir",
                        tmp,
                    ],
                    check=True,
                )
                result = release.install(
                    Path(tmp) / asset, args.root, descriptor["sha256"]
                )
    else:
        with bundle_lock(args.root):
            if args.command == "index":
                config = (
                    load_config(args.config.resolve())
                    if args.config
                    else json.loads((args.root / "config.json").read_text())
                )
                for split in release.SPLITS:
                    index_bundle(args.root / split, config["ragflow"])
                    print(f"{split}: index ready", flush=True)
                result = {"indexed": True}
            elif args.command == "audit":
                result = release.audit_service(args.root)
                if args.descriptor:
                    atomic_json(args.descriptor, result)
            elif args.command == "render":
                result = release.render_all(args.root)
            elif args.command == "verify":
                result = (
                    release.verify_release(args.root)
                    if (args.root / "release.json").exists()
                    else release.verify_data(args.root)
                )
            else:
                if not args.archive or not args.descriptor:
                    parser.error("pack requires --archive and --descriptor")
                result = release.pack(args.root, args.archive, args.version)
                atomic_json(args.descriptor, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
