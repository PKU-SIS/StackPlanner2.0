"""Collect disjoint native runs with identical execution fingerprints, without rerunning SP."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from .offline import (
    inference_summary,
    read_rows,
    task_identity,
    validate_prediction_rows,
    write_json,
)


def merge_runs(tasks, run_dirs, output_dir: Path, *, allow_missing: bool = False):
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError(output_dir)
    if not tasks or not run_dirs or len({t.id for t in tasks}) != len(tasks):
        raise ValueError("requires unique tasks and nonempty native runs")
    expected = {t.id: task_identity(t) for t in tasks}
    task_map = {t.id: t for t in tasks}
    common = None
    inputs, predictions, provenance = {}, {}, []
    concurrency = 0
    for directory in map(Path, run_dirs):
        raw = (directory / "manifest.json").read_bytes()
        manifest = json.loads(raw)
        if (
            manifest.get("phase") != "inference"
            or manifest.get("protocol") != "sp-native-benchmarks-v3"
            or manifest.get("collection_mode")
        ):
            raise ValueError("requires original native inference manifests")
        identity = {
            k: v
            for k, v in manifest.items()
            if k not in {"task_inputs", "normalized_tasks_sha256", "concurrency"}
        }
        if common is not None and identity != common:
            raise ValueError(
                "cannot merge different code/model/prompt/tool/execution configurations"
            )
        common = identity
        group = manifest["task_inputs"]
        for item in group:
            key = item["task_id"]
            if key in inputs or expected.get(key) != item:
                raise ValueError("duplicate, foreign or changed shard input")
            inputs[key] = item
        rows = read_rows(directory / "predictions.jsonl")
        validate_prediction_rows(rows, [task_map[item["task_id"]] for item in group])
        if not allow_missing and len(rows) != len(group):
            raise ValueError("shard has missing predictions")
        predictions.update({r["task_id"]: r for r in rows})
        provenance.append(
            {
                "run_dir": str(directory.resolve()),
                "manifest_sha256": hashlib.sha256(raw).hexdigest(),
                "predictions_sha256": hashlib.sha256(
                    (directory / "predictions.jsonl").read_bytes()
                ).hexdigest()
                if (directory / "predictions.jsonl").exists()
                else None,
            }
        )
        concurrency += manifest.get("concurrency", 1)
    if inputs != expected:
        raise ValueError("shards do not cover the requested complete task inventory")
    task_inputs = [task_identity(t) for t in tasks]
    merged = {
        **common,
        "task_inputs": task_inputs,
        "normalized_tasks_sha256": hashlib.sha256(
            json.dumps(task_inputs, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
        "concurrency": concurrency,
        "collection_mode": "validated-disjoint-shards-v1",
        "execution_shards": provenance,
    }
    rows = [predictions[t.id] for t in tasks if t.id in predictions]
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".merge-runs-", dir=output_dir.parent))
    try:
        write_json(stage / "manifest.json", merged)
        (stage / "predictions.jsonl").write_text(
            "".join(
                json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in rows
            )
        )
        report = inference_summary(rows, len(tasks))
        report["run_dir"] = str(output_dir)
        write_json(stage / "report.json", report)
        os.rename(stage, output_dir)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return report
