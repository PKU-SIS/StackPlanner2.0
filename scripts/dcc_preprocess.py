#!/usr/bin/env python3
"""Prepare, index, render, and inspect DCC bundles without running an agent."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dcc_harness.preprocessing.pipeline import prepare, read_jsonl, render
from dcc_harness.preprocessing.ragflow import RagflowClient, index_bundle, retrieve


def load_config(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    options = value.get("loader_options", {})
    if "source" in options:
        options["source"] = str((path.parent / options["source"]).resolve())
    prompts = value.get("prompts", {})
    groups = [prompts.get("layers", [])]
    groups.extend(prompts.get("datasets", {}).values())
    groups.extend(prompts.get("samples", {}).values())
    for layers in groups:
        for index, layer in enumerate(layers):
            if not layer.startswith("literal:"):
                layers[index] = str((path.parent / layer).resolve())
    key_file = value.get("ragflow", {}).get("api_key_file")
    if key_file:
        value["ragflow"]["api_key_file"] = str((path.parent / key_file).resolve())
    return value


@contextlib.contextmanager
def bundle_lock(bundle: Path):
    with (bundle / ".dcc.lock").open("a") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another index/render command is operating on this bundle") from None
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "index", "render", "retrieve", "status", "doctor"):
        command = commands.add_parser(name)
        if name != "status":
            command.add_argument("--config", type=Path, required=True)
        if name == "prepare":
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--limit", type=int)
        elif name != "doctor":
            command.add_argument("--bundle", type=Path, required=True)
        if name == "index":
            command.add_argument("--retry-failed", action="store_true")
        if name == "retrieve":
            command.add_argument("--sample-id", required=True)
            command.add_argument("--question", required=True)
            command.add_argument("--page", type=int, default=1)
            command.add_argument("--page-size", type=int, default=5)
    args = parser.parse_args(argv)
    config = load_config(args.config.resolve()) if args.command != "status" else {}
    if args.command == "doctor":
        client = RagflowClient(config["ragflow"])
        try:
            models = client.embedding_models()
            result = {
                "base_url": client.base_url,
                "authenticated": True,
                "embedding_models": models,
                "embedding_ready": bool(models),
                "requested_embedding_model": config["ragflow"].get("dataset_options", {}).get("embedding_model"),
            }
        finally:
            client.session.close()
    elif args.command == "prepare":
        result = prepare(config, args.output, limit=args.limit)
    elif args.command == "index":
        with bundle_lock(args.bundle):
            state = index_bundle(args.bundle, config["ragflow"], retry_failed=args.retry_failed)
        result = {
            "ready": state["ready"],
            "datasets": {
                key: {"id": value["id"], "documents": len(value["documents"]), "chunks": sum(doc.get("chunk_count", 0) for doc in value["documents"].values())}
                for key, value in state["datasets"].items()
            },
        }
    elif args.command == "render":
        with bundle_lock(args.bundle):
            tasks = render(args.bundle, config)
        result = {"tasks": len(tasks), "path": str((args.bundle / "public/tasks.jsonl").resolve())}
    elif args.command == "retrieve":
        tasks = read_jsonl(args.bundle / "public/tasks.jsonl")
        matches = [task for task in tasks if task["id"] == args.sample_id]
        if len(matches) != 1:
            raise ValueError("sample-id does not identify exactly one rendered task")
        from dcc_harness.preprocessing.native import context_for_task

        context = context_for_task(matches[0], args.bundle)
        client = RagflowClient(config["ragflow"])
        try:
            result = retrieve(
                client, context["retrieval"], args.question, page=args.page, page_size=args.page_size, options=config["ragflow"].get("retrieval_options", {})
            )
        finally:
            client.session.close()
    else:
        result = json.loads((args.bundle / "manifest.json").read_text())
        index_path = args.bundle / "index.json"
        result["index_ready"] = json.loads(index_path.read_text()).get("ready", False) if index_path.exists() else False
        result["tasks_rendered"] = (args.bundle / "public/tasks.jsonl").exists()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print(f"DCC preprocessing failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
