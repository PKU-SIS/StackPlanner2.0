import hashlib
import json
from dataclasses import replace

import pytest
from dcc_harness.evaluation.shards import merge_runs
from dcc_harness.evaluation.offline import task_identity
from test_clause_plugin import sample


def runs(tmp_path):
    tasks = [replace(sample(), id=str(i)) for i in range(2)]
    roots = []
    for i, t in enumerate(tasks):
        p = tmp_path / str(i)
        p.mkdir()
        roots.append(p)
        m = {
            "phase": "inference",
            "protocol": "sp-native-benchmarks-v3",
            "code_head": "same",
            "settings": {"seed": 42},
            "task_inputs": [task_identity(t)],
            "concurrency": 1,
        }
        (p / "manifest.json").write_text(json.dumps(m))
        row = {
            **task_identity(t),
            "protocol": m["protocol"],
            "status": "completed",
            "delivery": "[]",
            "delivery_sha256": hashlib.sha256(b"[]").hexdigest(),
        }
        (p / "predictions.jsonl").write_text(json.dumps(row) + "\n")
    return tasks, roots


def test_merge_preserves_native_deliveries_and_provenance(tmp_path):
    tasks, roots = runs(tmp_path)
    out = tmp_path / "out"
    assert merge_runs(tasks, roots, out)["complete_split"]
    m = json.loads((out / "manifest.json").read_text())
    assert len(m["execution_shards"]) == 2 and m["concurrency"] == 2
    assert len((out / "predictions.jsonl").read_text().splitlines()) == 2
    with pytest.raises(FileExistsError):
        merge_runs(tasks, roots, out)


@pytest.mark.parametrize(
    "change", ["different_settings", "duplicate", "missing", "tampered_delivery"]
)
def test_merge_rejects_incompatible_or_incomplete_runs(tmp_path, change):
    tasks, roots = runs(tmp_path)
    if change == "different_settings":
        p = roots[1] / "manifest.json"
        m = json.loads(p.read_text())
        m["settings"]["seed"] = 1
        p.write_text(json.dumps(m))
    elif change == "duplicate":
        roots = [roots[0], roots[0]]
    elif change == "missing":
        (roots[1] / "predictions.jsonl").write_text("")
    else:
        p = roots[1] / "predictions.jsonl"
        r = json.loads(p.read_text())
        r["delivery"] = "changed"
        p.write_text(json.dumps(r))
    with pytest.raises(ValueError):
        merge_runs(tasks, roots, tmp_path / "out")


def test_merge_keeps_failures_and_partial_coverage_explicit(tmp_path):
    tasks, roots = runs(tmp_path)
    (roots[1] / "predictions.jsonl").write_text("")
    report = merge_runs(tasks, roots, tmp_path / "out", allow_missing=True)
    assert report["predictions"] == 1 and not report["complete_split"]


def test_cli_collects_complete_task_inventory(tmp_path):
    import dataclasses
    import subprocess
    import sys
    from pathlib import Path

    tasks, roots = runs(tmp_path)
    manifest = tmp_path / "tasks.jsonl"
    manifest.write_text(
        "".join(json.dumps(dataclasses.asdict(t)) + "\n" for t in tasks)
    )
    output = tmp_path / "collected"
    script = Path(__file__).resolve().parents[1] / "scripts/collect_sp_benchmarks.py"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(script),
            "--tasks",
            str(manifest),
            "--run-dir",
            str(roots[0]),
            "--run-dir",
            str(roots[1]),
            "--output-dir",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["complete_split"]
