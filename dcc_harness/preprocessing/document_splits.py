"""Deterministic document-disjoint partitions, including transitive cross-task reuse."""

from __future__ import annotations

from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

from .pipeline import digest, encoded, read_jsonl
from .releases import validate_ready_bundle

BUILDER = "dcc_harness.preprocessing.document_splits:make_definition"
GROUP_BUILDER = "dcc_harness.preprocessing.document_splits:document_groups"


def document_groups(samples: list[dict], bundle: Path | None = None) -> dict[str, str]:
    """Connect samples sharing any evidence bytes; public reference rules are excluded."""
    parents = {sample["id"]: sample["id"] for sample in samples}
    owners = {}

    def root(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    for sample in samples:
        if not sample["documents"]:
            raise ValueError("document grouping requires evidence documents for every sample")
        for asset in sample["documents"]:
            previous = owners.setdefault(asset["sha256"], sample["id"])
            parents[root(sample["id"])] = root(previous)
    hashes = defaultdict(set)
    for sample in samples:
        hashes[root(sample["id"])].update(asset["sha256"] for asset in sample["documents"])
    keys = {key: digest(encoded(sorted(values))) for key, values in hashes.items()}
    return {sample["id"]: keys[root(sample["id"])] for sample in samples}


def _select_groups(grouped: dict, levels: list[str], quotas: dict, seed: int) -> set[str]:
    # A mixed-radix bitset records reachable vectors of per-subtask counts.
    # Source masks prevent overflow into another dimension. Whole components
    # are the only selectable units, even when they span multiple subtasks.
    targets = [quotas[level] for level in levels]
    strides, states = [], 1
    for target in targets:
        strides.append(states)
        states *= target + 1
    if states > 2_000_000:
        raise ValueError("too many stratification states; use a custom split builder")
    final = states - 1
    reachable, history, masks = 1, [], {}
    for key in sorted(grouped, key=lambda value: digest(f"{seed}:{value}".encode())):
        counts = Counter(sample["dataset"] for sample in grouped[key])
        vector = tuple(counts[level] for level in levels)
        if any(value > target for value, target in zip(vector, targets, strict=True)):
            continue
        if vector not in masks:
            mask = (1 << (targets[0] - vector[0] + 1)) - 1
            for dimension in range(1, len(targets)):
                previous = mask
                mask = sum(previous << (i * strides[dimension]) for i in range(targets[dimension] - vector[dimension] + 1))
            masks[vector] = mask
        offset = sum(value * stride for value, stride in zip(vector, strides, strict=True))
        before = reachable
        reachable |= (before & masks[vector]) << offset
        history.append((key, offset, before))
        if (reachable >> final) & 1:
            break
    if not (reachable >> final) & 1:
        raise ValueError("document groups cannot satisfy subtask quotas; choose a different ratio or split builder")
    selected, remaining = set(), final
    for key, offset, before in reversed(history):
        if not (before >> remaining) & 1:
            selected.add(key)
            remaining -= offset
    return selected


def make_definition(bundle: Path, *, train_fraction="0.8", seed=42) -> dict:
    """Split whole evidence components and preserve per-dataset sample proportions."""
    bundle = Path(bundle).resolve()
    manifest, samples = validate_ready_bundle(bundle)
    ratio = Fraction(str(train_fraction))
    if not 0 < ratio < 1 or type(seed) is not int:
        raise ValueError("require a train fraction in (0, 1) and an integer seed")
    total_test = round(len(samples) * (1 - ratio))
    if total_test in {0, len(samples)}:
        raise ValueError("both train and test must be nonempty")
    counts = Counter(sample["dataset"] for sample in samples)
    levels = sorted(counts)
    desired = {level: Fraction(count * total_test, len(samples)) for level, count in counts.items()}
    quotas = {level: int(value) for level, value in desired.items()}
    for level in sorted(levels, key=lambda value: (-(desired[value] - quotas[value]), value))[: total_test - sum(quotas.values())]:
        quotas[level] += 1
    groups = document_groups(samples, bundle)
    grouped = defaultdict(list)
    for sample in samples:
        grouped[groups[sample["id"]]].append(sample)
    selected = _select_groups(grouped, levels, quotas, seed)
    partitions, statistics, document_sets = {}, {}, {}
    for split in ("train", "test"):
        chosen = [sample for sample in samples if (groups[sample["id"]] in selected) == (split == "test")]
        partitions[split] = [sample["id"] for sample in chosen]
        document_sets[split] = {asset["sha256"] for sample in chosen for asset in sample["documents"]}
        statistics[split] = {
            "samples": len(chosen),
            "groups": len({groups[sample["id"]] for sample in chosen}),
            "unique_documents": len(document_sets[split]),
            "by_dataset": {level: {"samples": sum(sample["dataset"] == level for sample in chosen)} for level in levels},
        }
    labels = read_jsonl(bundle / "private/gold.jsonl")
    source = {
        "public_sha256": manifest["public_sha256"],
        "gold_sha256": manifest["gold_sha256"],
        "tasks_sha256": digest((bundle / "public/tasks.jsonl").read_bytes()),
        "upstream_revisions": sorted({str(label["metadata"].get("source_revision", "unknown")) for label in labels}),
    }
    if (bundle / "index.json").exists():
        source["index_sha256"] = digest((bundle / "index.json").read_bytes())
    return {
        "schema_version": 1,
        "official_split": False,
        "builder": BUILDER,
        "datasets": levels,
        "source_bundle": source,
        "strategy": {
            "algorithm": "sha256-document-components-subtask-dp-v1",
            "train_fraction": str(ratio),
            "seed": seed,
            "group_builder": GROUP_BUILDER,
            "stratum": "dataset",
        },
        "groups": len(grouped),
        "largest_group": max(map(len, grouped.values())),
        "document_isolation": {
            "total_unique_documents": len(document_sets["train"] | document_sets["test"]),
            "shared_documents": len(document_sets["train"] & document_sets["test"]),
        },
        "statistics": statistics,
        "partitions": partitions,
    }
