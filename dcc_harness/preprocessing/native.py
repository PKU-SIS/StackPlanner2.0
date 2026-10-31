"""Optional adapter to the existing BenchmarkPlugin interface; no harness edits."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

from dcc_harness.data import PublicTask

from .loaders import inside
from .pipeline import (
    digest,
    document_inventory,
    document_mode,
    inline_text,
    verified_samples,
)


def _validate_inline(context: dict, document: str, prompt: str, bundle: Path, *, sample_id: str | None = None) -> None:
    manifest, samples = verified_samples(bundle)
    if document_mode(manifest) != "inline" or "retrieval" in context or (bundle / "index.json").exists():
        raise ValueError("inline context must belong to an inline bundle without retrieval")
    if not document or digest(document.encode("utf-8")) != context.get("inline_document_sha256") or document not in prompt:
        raise ValueError("inline document content changed or is missing from the prompt")
    matches = [
        item
        for item in samples
        if (sample_id is None or item["id"] == sample_id)
        and document_inventory(item) == context["documents"]
        and [rule["sha256"] for rule in item["rules"]] == [rule["sha256"] for rule in context["rules"]]
    ]
    if not any(inline_text(bundle, item["documents"]) == document for item in matches):
        raise ValueError("inline evidence differs from the prepared bundle")


def _ready_index(bundle: Path) -> dict:
    from .ragflow import RagflowError, validate_sample_scopes

    state = json.loads((Path(bundle) / "index.json").read_text())
    if not state.get("ready"):
        raise ValueError("sample index is not ready")
    try:
        validate_sample_scopes(state, list(state.get("scopes", {})))
    except RagflowError as exc:
        raise ValueError(str(exc)) from None
    return state


def context_for_task(task, bundle: Path) -> dict:
    """Host-side CLI lookup; private Task identifiers never enter a builder."""
    row = task if isinstance(task, dict) else task.to_dict()
    metadata = row["metadata"]["dcc"]
    reference = metadata["context_file"]
    if not reference.startswith("public/contexts/"):
        raise ValueError("task context must be a prepared public context file")
    content = inside(Path(bundle), reference).read_bytes()
    if digest(content) != metadata["context_sha256"]:
        raise ValueError("public context content changed after task rendering")
    context = json.loads(content)
    if context.get("schema_version") != 2:
        raise ValueError("public context uses the old input contract; render tasks again")
    if context.get("document_mode") == "inline":
        _validate_inline(context, row.get("document", ""), row["prompt"], Path(bundle), sample_id=row["id"])
        return context
    state = _ready_index(bundle)
    expected = {**state.get("scopes", {}).get(row["id"], {}), "base_url": state["base_url"]}
    if context["retrieval"] != expected:
        raise ValueError("sample knowledge-base scope changed; render tasks again before execution")
    return context


def prepare_inputs(task: PublicTask, thread_id: str) -> dict:
    from dcc_harness.datasets.benchmark_plugins import stage_files

    if not isinstance(task, PublicTask):
        raise TypeError("DCC input builders require PublicTask; use task.public_input()")
    files = {item.name: item.text for item in task.files}
    if "dcc-context.json" not in files:
        raise ValueError("DCC inputs require public rule/context files; render tasks again")
    context = json.loads(files["dcc-context.json"])
    if context.get("schema_version") != 2:
        raise ValueError("public context uses the old input contract; render tasks again")
    inline = context.get("document_mode") == "inline"
    if inline:
        if task.benchmark != "dcc_inline":
            raise ValueError("inline evidence requires the dcc_inline plugin")
        _validate_inline(context, task.document, task.prompt, Path(os.environ["DCC_BUNDLE"]).resolve())
    else:
        if task.document:
            raise ValueError("RAGFlow inputs must not include inline document text")
        state = _ready_index(Path(os.environ["DCC_BUNDLE"]).resolve())
        matches = [scope for scope in state["scopes"].values() if {**scope, "base_url": state["base_url"]} == context["retrieval"]]
        if len(matches) != 1:
            raise ValueError("sample knowledge-base scope changed; render tasks again before execution")
    names = [rule["file"] for rule in context["rules"]]
    if len(set(names)) != len(names) or set(files) != {*names, "dcc-context.json"}:
        raise ValueError("DCC public file inventory differs from the rule/context contract")
    for rule in context["rules"]:
        if digest(files[rule["file"]].encode("utf-8")) != rule["sha256"]:
            raise ValueError("rule content changed after preparation")
    return stage_files(replace(task, document="") if inline else task, thread_id)


def make_inline_plugin():
    """Short evidence stays in the prompt; rules are staged with native tools."""
    from dcc_harness.datasets.benchmark_plugins import BenchmarkPlugin

    return BenchmarkPlugin(
        name="dcc_inline",
        prepare_task=lambda task: task.prompt,
        prepare_inputs=prepare_inputs,
        skills_path=Path(__file__).resolve().parents[2] / "configs/dcc/skills-inline",
    )


def make_plugin():
    """Register with --dataset-plugin dcc_harness.preprocessing.native:make_plugin.

    Scoring is deliberately absent. Add explicit benchmark-native parser/scorer
    hooks in the evaluation workstream before using the evaluation runner.
    """
    from deerflow.config.tool_config import ToolConfig

    from dcc_harness.datasets.benchmark_plugins import BenchmarkPlugin

    return BenchmarkPlugin(
        name="dcc",
        prepare_task=lambda task: task.prompt,
        prepare_inputs=prepare_inputs,
        skills_path=Path(__file__).resolve().parents[2] / "configs/dcc/skills",
        tools=(ToolConfig(name="ragflow_retrieve", group="dcc", use="dcc_harness.preprocessing.native_tools:ragflow_retrieve"),),
    )
