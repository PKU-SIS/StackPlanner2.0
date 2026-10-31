"""Host-only label loading and dataset dispatch; input builders never see labels."""

from __future__ import annotations

import importlib
import json
import os
from collections import Counter
from pathlib import Path

from dcc_harness.data import load_tasks
from dcc_harness.preprocessing.pipeline import digest, extension, read_jsonl

from . import Evaluator

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "configs/dcc/evaluation"
OFFICIAL = Path(__file__).resolve().parents[1] / "dcc_official"
RESOURCES = tuple(
    sorted(
        [
            *Path(__file__).resolve().parent.glob("*.py"),
            *OFFICIAL.glob("*.py"),
            *OFFICIAL.glob("*.json"),
            OFFICIAL / "FINAUDITING_LICENSE",
            OFFICIAL / "RULEARENA_LICENSE",
            CONFIG / "adapters.json",
        ]
    )
)


def parse_answer(_public_task, text: str) -> dict:
    # Official parsers grade malformed *completed* deliveries as wrong. Keep raw
    # text here so no gold/private dataset routing reaches the public parser.
    if not isinstance(text, str):
        raise TypeError("DCC delivery must be text")
    return {"text": text}


def setup_evaluation(context) -> None:
    allowed = {"bundle", "evaluators", "datasets"}
    if set(context.options) - allowed:
        raise ValueError("unsupported DCC evaluation options")
    reference = context.options.get("bundle") or os.environ.get("DCC_BUNDLE")
    if not reference:
        raise ValueError("configure evaluation bundle or DCC_BUNDLE for private labels")
    bundle = Path(reference).resolve()
    manifest = json.loads((bundle / "manifest.json").read_text())
    for name, key in (("public/samples.jsonl", "public_sha256"), ("private/gold.jsonl", "gold_sha256")):
        if digest((bundle / name).read_bytes()) != manifest[key]:
            raise ValueError(f"evaluation bundle checksum differs: {name}")
    samples = read_jsonl(bundle / "public/samples.jsonl")
    labels = read_jsonl(bundle / "private/gold.jsonl")
    prepared = load_tasks(bundle / "public/tasks.jsonl")
    ids = [item["id"] for item in samples]
    if len(set(ids)) != manifest["samples"] or [row["id"] for row in labels] != ids or [task.id for task in prepared] != ids:
        raise ValueError("evaluation label/sample/task inventories differ")
    for sample, label in zip(samples, labels, strict=True):
        if (sample["dataset"], sample["split"]) != (label["dataset"], label["split"]):
            raise ValueError("private labels disagree with their public sample")
    registry = json.loads((CONFIG / "adapters.json").read_text())
    overrides = context.options.get("evaluators", {})
    settings = context.options.get("datasets", {})
    if not isinstance(overrides, dict) or not isinstance(settings, dict) or any(not isinstance(value, dict) for value in settings.values()):
        raise ValueError("evaluators and datasets must be configuration objects")
    registry.update(overrides)
    names = {sample["dataset"] for sample in samples}
    if set(settings) - names:
        raise ValueError("dataset options contain a dataset outside this bundle")
    evaluators, identities = {}, {}
    for name in sorted(names):
        if name not in registry:
            raise ValueError(f"no evaluator registered for dataset: {name}")
        factory = extension(registry[name], {})
        evaluator = factory()
        if not isinstance(evaluator, Evaluator) or evaluator.dataset != name:
            raise TypeError("evaluator factory must return a matching Evaluator")
        evaluators[name] = evaluator
        module = importlib.import_module(factory.__module__)
        path = Path(module.__file__) if getattr(module, "__file__", None) else None
        identities[name] = {
            "use": registry[name],
            "version": evaluator.version,
            "source_sha256": digest(path.read_bytes()) if path and path.is_file() else "unknown",
        }
    provenance = json.loads((OFFICIAL / "provenance.json").read_text())
    for name, entry in provenance["files"].items():
        if digest((OFFICIAL / name).read_bytes()) != entry["excerpt_sha256"]:
            raise ValueError(f"official evaluator resource changed: {name}")
    context.resources["dcc"] = {
        "bundle": bundle,
        "samples": {row["id"]: row for row in samples},
        "labels": {row["id"]: row for row in labels},
        "tasks": {task.id: task for task in prepared},
        "evaluators": evaluators,
        "settings": settings,
    }
    context.provenance.update(
        bundle=str(bundle),
        public_sha256=manifest["public_sha256"],
        gold_sha256=manifest["gold_sha256"],
        evaluators=identities,
        official_sources=provenance,
    )


def score_task(task, text: str, context) -> dict:
    resource = context.resources["dcc"]
    sample = resource["samples"].get(task.id)
    expected = resource["tasks"].get(task.id)
    if sample is None or expected is None or expected.input_sha256() != task.input_sha256() or expected.split != task.split:
        raise ValueError("evaluation input does not match the label bundle")
    if task.metadata.get("dcc", {}).get("dataset") != sample["dataset"]:
        raise ValueError("task dataset differs from its prepared labels")
    name = sample["dataset"]
    evaluator = resource["evaluators"][name]
    value = evaluator.score(sample, resource["labels"][task.id], text, resource["settings"].get(name, {}), context)
    if not isinstance(value, dict):
        raise TypeError("dataset evaluator must return an object")
    return {"dataset": name, "evaluation_version": evaluator.version, **value}


def aggregate(tasks, rows, context) -> dict:
    resource = context.resources["dcc"]
    by_id = {task.id: task for task in tasks}
    groups = {}
    for row in rows:
        task = by_id[row["task_id"]]
        name = resource["samples"][task.id]["dataset"]
        groups.setdefault(name, []).append(row["metrics"])
    available = Counter(row["dataset"] for row in resource["samples"].values())
    result = {}
    for name, metrics in groups.items():
        evaluator = resource["evaluators"][name]
        result[name] = {
            "evaluation_version": evaluator.version,
            "evaluated_samples": len(metrics),
            "available_samples": available[name],
            "complete_dataset": len(metrics) == available[name],
            **evaluator.aggregate(metrics, resource["settings"].get(name, {}), context),
        }
    return {"datasets": result}
