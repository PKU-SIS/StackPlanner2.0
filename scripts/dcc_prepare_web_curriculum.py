#!/usr/bin/env python3
"""Prepare independent public-registry training and validation tasks."""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dcc_harness.web_curriculum import (
    PUBLIC_TABLES,
    REGISTRIES,
    public_table_tasks,
    registry_tasks,
)


def balanced_training_tasks(tasks, *, limit=None, seed=42):
    """Cover source families, languages and list lengths before repeating them."""
    grouped = defaultdict(list)
    for task in sorted(tasks, key=lambda task: task.id):
        if task.split != "train":
            continue
        size = len(task.gold)
        key = (task.metadata.get("source_family", "iana-" + task.metadata.get("registry", "unknown")),
               task.metadata.get("language"), "small" if size <= 5 else "medium" if size <= 15 else "large")
        grouped[key].append(task)
    rng = random.Random(seed)
    for rows in grouped.values():
        rng.shuffle(rows)
    chosen = []
    while grouped and (limit is None or len(chosen) < limit):
        for key in sorted(grouped):
            chosen.append(grouped[key].pop())
            if not grouped[key]:
                del grouped[key]
            if limit is not None and len(chosen) >= limit:
                break
    return chosen


async def prepare(args):
    root = Path(args.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    raw = root / "sources"
    raw.mkdir(exist_ok=True)
    tasks = []
    mode = getattr(args, "source_mode", "mixed")
    sources = ([*REGISTRIES] if mode in {"iana", "mixed"} else []) + ([*PUBLIC_TABLES] if mode in {"public", "mixed"} else [])
    for registry in sources:
        is_registry = registry in REGISTRIES
        path = raw / f"{registry.key}.{'csv' if is_registry or registry.url.endswith('/CSV') else 'json'}"
        if not path.exists():
            if getattr(args, "no_download", False):
                raise FileNotFoundError(f"Missing cached source: {registry.key}")
            request = Request(registry.url, headers={"User-Agent": "Harness-RL-independent-curriculum/2.0"})
            with urlopen(request, timeout=30) as response:
                body = response.read(2_000_001)
                if len(body) > 2_000_000:
                    raise ValueError(f"Public source exceeded its size limit: {registry.key}")
                path.write_bytes(body)
        builder = registry_tasks if is_registry else public_table_tasks
        tasks.extend(builder(registry, path.read_text(encoding="utf-8-sig")))
    for split in ("train", "validation"):
        (root / f"{split}.jsonl").write_text("".join(json.dumps(task.to_dict(), ensure_ascii=False) + "\n" for task in tasks if task.split == split))
    manifest = {"source": "independent_curriculum", "version": "independent-public-tables-v3", "source_mode": mode,
                "validation_used_for_supervision": False,
                "prepared_at": datetime.now(timezone.utc).isoformat(),
                "counts": {split: sum(task.split == split for task in tasks) for split in ("train", "validation")},
                "families": {split: dict(Counter(task.metadata.get("source_family", "iana") for task in tasks if task.split == split))
                             for split in ("train", "validation")},
                "list_sizes": {split: dict(sorted(Counter(len(task.gold) for task in tasks if task.split == split).items()))
                               for split in ("train", "validation")},
                "registries": [{"key": source.key, "url": source.url, "split": source.split} for source in sources]}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--source-mode", choices=("iana", "public", "mixed"), default="mixed")
    parser.add_argument("--no-download", action="store_true", help="Require cached original source exports")
    asyncio.run(prepare(parser.parse_args()))
