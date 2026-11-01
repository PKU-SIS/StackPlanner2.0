"""Optional CLAUSE RAGFlow transport; original file plugin and metrics stay intact."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

from dcc_harness.data import load_tasks
from dcc_harness.datasets import clause
from .models import Asset, Sample
from .pipeline import digest, encoded, read_jsonl, render, verified_samples
from .native import prepare_inputs as native_inputs


def load(options):
    """Read an already verified CLAUSE manifest; labels remain private."""
    tasks = load_tasks(options["source"])
    clause.validate_dataset(tasks)
    for task in tasks:
        yield Sample(
            task.id,
            "clause",
            task.split,
            task.prompt,
            (Asset("document", text=task.files[0].text, title="document"),),
            gold=task.gold,
            metadata=task.metadata,
            public_metadata=task.public_metadata,
        )


def render_clause(bundle: Path, config: dict, *, variant: str | None = None):
    """Write separate public/private CLAUSE tasks after the shared index is ready."""
    bundle = Path(bundle).resolve()
    manifest, samples = verified_samples(bundle)
    if manifest.get("document_mode") != "ragflow":
        raise ValueError("CLAUSE RAG requires a ragflow bundle")
    gold_bytes = (bundle / "private/gold.jsonl").read_bytes()
    if digest(gold_bytes) != manifest["gold_sha256"]:
        raise ValueError("private labels changed after preparation")
    private = {row["id"]: row for row in read_jsonl(bundle / "private/gold.jsonl")}
    if set(private) != {s["id"] for s in samples}:
        raise ValueError("private/public sample inventory differs")
    # Use shared context rendering and scope/configuration integrity checks.
    rows = render(bundle, config)
    public_rows, private_rows = [], []
    for row, sample in zip(rows, samples, strict=True):
        if (
            sample["dataset"] != "clause"
            or len(sample["documents"]) != 1
            or sample["rules"]
        ):
            raise ValueError("expected one CLAUSE document and no rules")
        row.update(
            benchmark="clause_ragflow",
            prompt=clause.prompt_text(variant) if variant else sample["question"],
            public_metadata={"prompt_variant": variant}
            if variant
            else sample["public_metadata"],
        )
        # Original text is kept in the host manifest for hashing and offline
        # validation ONLY. prepare_inputs never uploads it to the SP sandbox.
        row["files"].append(
            {
                "name": "document.txt",
                "path": str(Path(sample["documents"][0]["path"]).relative_to("public")),
            }
        )
        public_rows.append(row)
        scored = {
            **row,
            "gold": private[row["id"]]["gold"],
            "metadata": {**private[row["id"]]["metadata"], **row["metadata"]},
            "files": [{**f, "path": "../public/" + f["path"]} for f in row["files"]],
        }
        private_rows.append(scored)
    # Validate before publishing either task manifest.
    from dcc_harness.data import Task

    for rows_to_check, parent in [
        (public_rows, bundle / "public"),
        (private_rows, bundle / "private"),
    ]:
        tasks = [
            Task.from_dict(
                {
                    **r,
                    "files": [
                        {
                            "name": f["name"],
                            "text": (parent / f["path"]).read_bytes().decode("utf-8"),
                        }
                        for f in r["files"]
                    ],
                }
            )
            for r in rows_to_check
        ]
        validate_dataset(tasks)
    for directory, rows_to_write in [
        ("public", public_rows),
        ("private", private_rows),
    ]:
        path = (
            bundle
            / directory
            / (f"clause-{variant}-tasks.jsonl" if variant else "clause-tasks.jsonl")
        )
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(b"".join(encoded(r) for r in rows_to_write))
        temporary.replace(path)
    return public_rows


def validate_dataset(tasks):
    original = []
    for task in tasks:
        files = {f.name: f for f in task.files}
        if task.benchmark != "clause_ragflow" or set(files) != {
            "document.txt",
            "dcc-context.json",
        }:
            raise ValueError("CLAUSE RAG requires document and scope context")
        context = json.loads(files["dcc-context.json"].text)
        if (
            context.get("schema_version") != 2
            or context.get("rules") != []
            or context.get("document_mode", "ragflow") != "ragflow"
            or len(context.get("documents", [])) != 1
            or context["documents"][0]["sha256"]
            != digest(files["document.txt"].text.encode())
        ):
            raise ValueError("CLAUSE RAG document/context mismatch")
        from .ragflow import validate_sample_scopes

        validate_sample_scopes(
            {
                "schema_version": 2,
                "knowledge_base_scope": "sample",
                "scopes": {"sample": context.get("retrieval", {})},
            },
            ["sample"],
        )
        original.append(
            replace(task, benchmark="clause", files=(files["document.txt"],))
        )
    clause.validate_dataset(original)


def prepare_inputs(public, thread_id):
    files = {f.name: f for f in public.files}
    if set(files) != {"document.txt", "dcc-context.json"} or public.document:
        raise ValueError("invalid CLAUSE RAG inputs")
    bundle = Path(os.environ["DCC_BUNDLE"]).resolve()
    manifest, samples = verified_samples(bundle)
    state = json.loads((bundle / "index.json").read_text())
    context = json.loads(files["dcc-context.json"].text)
    if state.get("public_sha256") != manifest["public_sha256"]:
        raise ValueError("index belongs to another bundle")
    matches = [
        s
        for s in samples
        if {**state["scopes"].get(s["id"], {}), "base_url": state["base_url"]}
        == context.get("retrieval")
    ]
    if (
        len(matches) != 1
        or len(matches[0]["documents"]) != 1
        or matches[0]["documents"][0]["sha256"]
        != digest(files["document.txt"].text.encode())
    ):
        raise ValueError("document differs from indexed sample scope")
    return native_inputs(replace(public, files=(files["dcc-context.json"],)), thread_id)


def prepare_task(public):
    return (
        public.prompt
        + "\n\n"
        + (
            Path(__file__).resolve().parents[2]
            / "configs/stackplanner_skills/clause-ragflow/public/clause/SKILL.md"
        )
        .read_text()
        .split("---", 2)[-1]
        .strip()
    )


def setup_evaluation(context):
    clause.setup_evaluation(context)
    context.provenance["input_mode"] = "sp2-ragflow"


def aggregate(tasks, rows, context):
    result = clause.aggregate(tasks, rows, context)
    result["input_mode"] = "sp2-ragflow"
    return result


def make_plugin():
    from deerflow.config.tool_config import ToolConfig

    return replace(
        clause.create_plugin(),
        name="clause_ragflow",
        prepare_task=prepare_task,
        prepare_inputs=prepare_inputs,
        validate_dataset=validate_dataset,
        skills_path=Path(__file__).resolve().parents[2]
        / "configs/stackplanner_skills/clause-ragflow",
        tools=(
            ToolConfig(
                name="ragflow_retrieve",
                group="dcc",
                use="dcc_harness.preprocessing.native_tools:ragflow_retrieve",
            ),
        ),
        aggregate=aggregate,
        setup_evaluation=setup_evaluation,
        evaluation_version="intext-yes-whole-document-v1/GPT-DFS/no-Judge/sp2-ragflow",
    )
