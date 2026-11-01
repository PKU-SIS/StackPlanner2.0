"""Opt-in grouped partitions; baseline bundles and running experiments stay immutable."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections import Counter, defaultdict
from copy import deepcopy
from fractions import Fraction
from pathlib import Path

from dcc_harness.data import load_tasks, validate_disjoint

from .loaders import inside
from .pipeline import (
    atomic_json,
    digest,
    document_mode,
    encoded,
    extension,
    inline_text,
    read_jsonl,
)
from .releases import validate_ready_bundle

GROUP_KEY = "dcc_harness.preprocessing.splits:nba_context_group"
STRATUM_KEY = "dcc_harness.preprocessing.splits:nba_stratum"


def nba_context_group(sample: dict, bundle: Path) -> str:
    """Keep identical team/player backgrounds together even when operations differ."""
    if sample["dataset"] != "rulearena_nba":
        raise ValueError("configure a group_key extension for this dataset")
    text = inline_text(bundle, sample["documents"])
    if not text.startswith("Team Situations:\n") or text.count("\n\nOperations:\n") != 1 or "\n\nPlayer Situations:\n" not in text:
        raise ValueError("NBA scenario differs from its original three-section format")
    return digest(text.partition("\n\nOperations:\n")[0].encode("utf-8"))


def nba_stratum(label: dict) -> tuple[str, bool]:
    level, positive = label["metadata"]["complexity"], label["gold"]["answer"]
    if type(level) is not int or level not in {0, 1, 2} or type(positive) is not bool:
        raise ValueError("NBA stratification requires original complexity and Boolean labels")
    return str(level), positive


def _records(bundle, samples, labels, group_key, stratum_key):
    group = extension(group_key, {})
    stratum = extension(stratum_key, {})
    result = []
    for sample, label in zip(samples, labels, strict=True):
        level, positive = stratum(label)
        if not isinstance(level, str) or type(positive) is not bool:
            raise TypeError("stratum_key must return (str, bool)")
        key = group(sample, bundle)
        if not isinstance(key, str) or not key:
            raise TypeError("group_key must return a nonempty string")
        result.append({"id": sample["id"], "group": key, "level": level, "positive": positive})
    return result


def _test_groups(groups, target, negative_target, seed):
    # Hash ordering avoids dependence on Python RNG versions or model results.
    ordered = sorted(groups, key=lambda key: digest(f"{seed}:{key}".encode()))
    possible = {(0, 0): ()}
    for key in ordered:
        size = len(groups[key])
        negative = sum(not row["positive"] for row in groups[key])
        for (count, negatives), selected in list(possible.items()):
            if count + size <= target:
                possible.setdefault((count + size, negatives + negative), (*selected, key))
    choices = [state for state in possible if state[0] == target]
    if not choices:
        raise ValueError("group sizes cannot satisfy this split quota; choose a different ratio")
    best = min(choices, key=lambda state: (abs(state[1] - negative_target), state[1]))
    return set(possible[best])


def make_definition(bundle: Path, *, train_fraction="0.8", seed=42, group_key=GROUP_KEY, stratum_key=STRATUM_KEY) -> dict:
    bundle = Path(bundle).resolve()
    manifest, samples = validate_ready_bundle(bundle)
    if document_mode(manifest) != "inline":
        raise ValueError("split export currently supports inline bundles only")
    ratio = Fraction(str(train_fraction))
    if not 0 < ratio < 1 or type(seed) is not int:
        raise ValueError("require a train fraction in (0, 1) and an integer seed")
    labels = read_jsonl(bundle / "private/gold.jsonl")
    records = _records(bundle, samples, labels, group_key, stratum_key)
    levels = Counter(row["level"] for row in records)
    total_test = round(len(records) * (1 - ratio))
    if total_test in {0, len(records)}:
        raise ValueError("both train and test must be nonempty")
    desired = {level: Fraction(count * total_test, len(records)) for level, count in levels.items()}
    quotas = {level: int(value) for level, value in desired.items()}
    for level in sorted(levels, key=lambda key: (-(desired[key] - quotas[key]), key))[: total_test - sum(quotas.values())]:
        quotas[level] += 1
    grouped = defaultdict(list)
    for row in records:
        grouped[row["group"]].append(row)
    if any(len({row["level"] for row in rows}) != 1 for rows in grouped.values()):
        raise ValueError("group spans difficulty strata; use a compatible stratum_key extension")
    selected_groups = set()
    for level, count in sorted(levels.items()):
        negative = sum(not row["positive"] for row in records if row["level"] == level)
        candidates = {key: rows for key, rows in grouped.items() if rows[0]["level"] == level}
        selected_groups.update(_test_groups(candidates, quotas[level], round(Fraction(negative * quotas[level], count)), seed))
    partitions = {name: [row["id"] for row in records if (row["group"] in selected_groups) == (name == "test")] for name in ("train", "test")}
    statistics = {}
    for name, ids in partitions.items():
        chosen = [row for row in records if row["id"] in set(ids)]
        statistics[name] = {
            "samples": len(chosen),
            "groups": len({row["group"] for row in chosen}),
            "by_complexity": {
                level: {
                    "samples": sum(row["level"] == level for row in chosen),
                    "violating": sum(row["level"] == level and row["positive"] for row in chosen),
                    "compliant": sum(row["level"] == level and not row["positive"] for row in chosen),
                }
                for level in sorted(levels)
            },
        }
    return {
        "schema_version": 1,
        "official_split": False,
        "datasets": sorted({sample["dataset"] for sample in samples}),
        "source_bundle": {
            "public_sha256": manifest["public_sha256"],
            "gold_sha256": manifest["gold_sha256"],
            "tasks_sha256": digest((bundle / "public/tasks.jsonl").read_bytes()),
            "upstream_revisions": sorted({str(label["metadata"].get("source_revision", "unknown")) for label in labels}),
        },
        "strategy": {
            "algorithm": "sha256-grouped-subset-sum-v1",
            "train_fraction": str(ratio),
            "seed": seed,
            "group_key": group_key,
            "stratum_key": stratum_key,
        },
        "groups": len(grouped),
        "statistics": statistics,
        "partitions": partitions,
    }


def export_splits(bundle: Path, definition: dict, output: Path) -> dict:
    bundle, output = Path(bundle).resolve(), Path(output).resolve()
    if output == bundle or output.is_relative_to(bundle):
        raise ValueError("output must be outside the source bundle")
    if output.exists():
        raise FileExistsError(f"split output already exists: {output}")
    strategy = definition["strategy"]
    expected = make_definition(bundle, **{key: strategy[key] for key in ("train_fraction", "seed", "group_key", "stratum_key")})
    if definition != expected:
        raise ValueError("split definition differs from the verified source or deterministic assignment")
    manifest, samples = validate_ready_bundle(bundle)
    labels = read_jsonl(bundle / "private/gold.jsonl")
    tasks = read_jsonl(bundle / "public/tasks.jsonl")
    records = _records(bundle, samples, labels, strategy["group_key"], strategy["stratum_key"])
    groups = {row["id"]: row["group"] for row in records}
    original_tasks = {task.id: task for task in load_tasks(bundle / "public/tasks.jsonl")}
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".dcc-splits-", dir=output.parent))
    try:
        all_tasks = []
        for split, ids in definition["partitions"].items():
            selected = set(ids)
            target = staging / split
            (target / "public").mkdir(parents=True)
            (target / "private").mkdir(mode=0o700)
            subsets = [[deepcopy(row) for row in rows if row["id"] in selected] for rows in (samples, labels, tasks)]
            for name, rows in zip(("public/samples.jsonl", "private/gold.jsonl", "public/tasks.jsonl"), subsets, strict=True):
                for row in rows:
                    row["split"] = split
                    if name == "public/tasks.jsonl":
                        row["group_id"] = "dcc-context-" + groups[row["id"]]
                (target / name).write_bytes(b"".join(encoded(row) for row in rows))
            files = {asset["path"] for sample in subsets[0] for asset in sample["documents"] + sample["rules"]}
            for task in subsets[2]:
                files.add(task["metadata"]["dcc"]["context_file"])
                files.update(str(inside(bundle / "public", item["path"]).relative_to(bundle)) for item in task["files"] if "path" in item)
            for name in sorted(files):
                source = inside(bundle, name)
                destination = target / source.relative_to(bundle)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
            derived = {
                **manifest,
                "counts": dict(Counter(f"{row['dataset']}/{split}" for row in subsets[0])),
                "samples": len(selected),
                "public_sha256": digest((target / "public/samples.jsonl").read_bytes()),
                "gold_sha256": digest((target / "private/gold.jsonl").read_bytes()),
                "unique_documents": len({asset["sha256"] for row in subsets[0] for asset in row["documents"]}),
                "unique_rules": len({asset["sha256"] for row in subsets[0] for asset in row["rules"]}),
                "split_definition_sha256": digest(encoded(definition)),
                "source_bundle": definition["source_bundle"],
            }
            atomic_json(target / "manifest.json", derived)
            validate_ready_bundle(target)
            loaded = load_tasks(target / "public/tasks.jsonl")
            if any(task.input_sha256() != original_tasks[task.id].input_sha256() for task in loaded):
                raise ValueError("split export changed a public input")
            all_tasks.append(loaded)
        validate_disjoint(*all_tasks)
        atomic_json(staging / "split-definition.json", definition)
        if output.exists():
            raise FileExistsError(f"split output already exists: {output}")
        os.rename(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return definition["statistics"]
