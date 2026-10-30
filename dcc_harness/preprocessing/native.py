"""Optional adapter to the existing BenchmarkPlugin interface; no harness edits."""

from __future__ import annotations

import json
import os
from pathlib import Path

from dcc_harness.data import PublicTask

from .loaders import inside
from .pipeline import digest


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
    if task.document or "dcc-context.json" not in files:
        raise ValueError("DCC inputs require public rule/context files; render tasks again")
    context = json.loads(files["dcc-context.json"])
    if context.get("schema_version") != 2:
        raise ValueError("public context uses the old input contract; render tasks again")
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
    return stage_files(task, thread_id)


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
