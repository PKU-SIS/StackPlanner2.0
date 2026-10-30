"""Integration with the real SP config, user paths, and LangChain tool interface."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("langchain")

from dcc_harness.data import load_tasks
from dcc_harness.preprocessing.models import Asset, Sample
from dcc_harness.preprocessing.native import (
    context_for_task,
    prepare_inputs,
)
from dcc_harness.preprocessing.pipeline import (
    atomic_json,
    digest,
    encoded,
    prepare,
    render,
)
from dcc_harness.preprocessing.ragflow import index_identity

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def native_bundle(tmp_path, monkeypatch):
    config = {"loader": "manifest", "ragflow": {"base_url": "https://rag.example"}}
    bundle = tmp_path / "bundle"
    sample = Sample(
        "one",
        "demo",
        "test",
        "Check facts",
        (Asset("d", text="DOCUMENT"),),
        (Asset("r", text="PUBLIC_RULE"),),
        gold="PRIVATE_GOLD",
        metadata={"secret": "PRIVATE_META"},
    )
    manifest = prepare(config, bundle, samples=[sample])
    atomic_json(
        bundle / "index.json",
        {
            **index_identity(manifest, config["ragflow"]),
            "ready": True,
            "configuration_sha256": digest(encoded(index_identity(manifest, config["ragflow"]))),
            "schema_version": 2,
            "scopes": {"one": {"dataset_ids": ["kb"], "document_ids": ["doc"]}},
        },
    )
    render(bundle, config)
    monkeypatch.setenv("DCC_BUNDLE", str(bundle))
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "native-home"))
    config_path = tmp_path / "config.json"
    atomic_json(config_path, config)
    monkeypatch.setenv("DCC_CONFIG", str(config_path))
    return bundle, load_tasks(bundle / "public/tasks.jsonl")[0]


def test_plugin_configures_native_tool_and_skill_without_scoring(native_bundle):
    from deerflow.config.app_config import AppConfig

    from dcc_harness.datasets.benchmark_plugins import (
        configure_dataset_app,
        resolve_dataset_plugin,
    )

    plugin = resolve_dataset_plugin("dcc", ("dcc_harness.preprocessing.native:make_plugin",))
    app = configure_dataset_app(AppConfig.from_file(str(ROOT / "configs/stackplanner_benchmarks.yaml")), plugin)
    assert "ragflow_retrieve" in {tool.name for tool in app.tools}
    assert Path(app.skills.path) == ROOT / "configs/dcc/skills"
    assert plugin.parse_answer is None and plugin.score_task is None
    assert (Path(plugin.skills_path) / "public/dcc-audit/SKILL.md").exists()


def test_only_public_rules_and_context_enter_current_users_thread(native_bundle):
    from deerflow.config.paths import get_paths
    from deerflow.runtime.user_context import reset_current_user, set_current_user

    _, task = native_bundle
    token = set_current_user(SimpleNamespace(id="dcc-alice"))
    try:
        inputs = prepare_inputs(task.public_input(), "thread-one")
        uploads = get_paths().sandbox_uploads_dir("thread-one", user_id="dcc-alice")
        assert len(inputs["uploaded_files"]) == 2
        content = "\n".join(file.read_text() for file in uploads.iterdir())
        assert "PUBLIC_RULE" in content and "PRIVATE_" not in content
        assert "DOCUMENT" not in content
        assert not get_paths().sandbox_uploads_dir("thread-one", user_id="dcc-bob").exists()
        with pytest.raises(FileExistsError):
            prepare_inputs(task.public_input(), "thread-one")
    finally:
        reset_current_user(token)


def test_context_integrity_and_sample_identity_are_checked(native_bundle):
    bundle, task = native_bundle
    metadata = task.metadata["dcc"]
    path = bundle / metadata["context_file"]
    path.write_text("{}")
    with pytest.raises(ValueError, match="context content changed"):
        context_for_task(task, bundle)


def test_native_adapter_rejects_old_shared_or_unready_index(native_bundle):
    bundle, task = native_bundle
    path = bundle / "index.json"
    state = json.loads(path.read_text())
    state["schema_version"] = 1
    atomic_json(path, state)
    with pytest.raises(ValueError, match="dedicated sample"):
        context_for_task(task, bundle)
    state["schema_version"] = 2
    state["ready"] = False
    atomic_json(path, state)
    with pytest.raises(ValueError, match="not ready"):
        context_for_task(task, bundle)


def test_native_adapter_rejects_context_from_an_older_knowledge_base(native_bundle):
    bundle, task = native_bundle
    path = bundle / "index.json"
    state = json.loads(path.read_text())
    state["scopes"]["one"]["dataset_ids"] = ["replacement-library"]
    atomic_json(path, state)
    with pytest.raises(ValueError, match="scope changed"):
        context_for_task(task, bundle)


def test_native_tool_uses_thread_scope_and_excludes_runtime_from_agent_schema(native_bundle, monkeypatch):
    from deerflow.runtime.user_context import reset_current_user, set_current_user

    from dcc_harness.preprocessing import native_tools

    _, task = native_bundle
    observed = []

    class FakeClient:
        base_url = "https://rag.example"

        def __init__(self, config):
            self.session = SimpleNamespace(close=lambda: None)

        def retrieve(self, payload):
            observed.append(payload)
            return {"chunks": [{"doc_id": "doc", "content": "Evidence", "id": "chunk"}], "total": 1}

    monkeypatch.setattr(native_tools, "RagflowClient", FakeClient)
    token = set_current_user(SimpleNamespace(id="dcc-alice"))
    try:
        prepare_inputs(task.public_input(), "thread-one")
        runtime = SimpleNamespace(context={"thread_id": "thread-one"}, config={})
        result = asyncio.run(native_tools.ragflow_retrieve.coroutine(question="fact", runtime=runtime))
        assert json.loads(result)["chunks"][0]["id"] == "chunk"
        assert observed[0]["document_ids"] == ["doc"]
        assert observed[0]["dataset_ids"] == ["kb"]
        schema = native_tools.ragflow_retrieve.tool_call_schema.model_json_schema()
        assert "runtime" not in schema["properties"]
        assert "dataset_ids" not in schema["properties"]
    finally:
        reset_current_user(token)


def test_v3_public_input_builder_stages_neutral_files_without_private_identifiers(native_bundle):
    from deerflow.config.paths import get_paths
    from deerflow.runtime.user_context import get_effective_user_id

    from dcc_harness.datasets.benchmark_plugins import (
        build_inputs,
        resolve_dataset_plugin,
    )

    bundle, task = native_bundle
    public = task.public_input()
    assert not hasattr(public, "id") and not hasattr(public, "gold") and not hasattr(public, "split")
    assert "dcc" not in public.metadata
    plugin = resolve_dataset_plugin("dcc", ("dcc_harness.preprocessing.native:make_plugin",))
    inputs = build_inputs(plugin, public, "public-contract")
    root = get_paths().sandbox_uploads_dir("public-contract", user_id=get_effective_user_id())
    context = json.loads((root / "dcc-context.json").read_text())
    assert "sample_id" not in context and "dataset" not in context and "split" not in context
    assert "PRIVATE_" not in repr(inputs) and str(bundle) not in repr(inputs)
    assert "Sample: one" not in inputs["messages"][0].content
    assert "Split: test" not in inputs["messages"][0].content
    assert inputs["sp_task_contract"]["authoritative"] is True
    assert {item["name"] for item in inputs["uploaded_files"]} == {"dcc-context.json", "dcc-rule-0.txt"}


def test_public_rule_file_integrity_is_checked_before_staging(native_bundle):
    import dataclasses

    from dcc_harness.data import InputFile

    _, task = native_bundle
    public = task.public_input()
    altered = dataclasses.replace(public, files=tuple(InputFile(item.name, "changed") if item.name == "dcc-rule-0.txt" else item for item in public.files))
    with pytest.raises(ValueError, match="rule content changed"):
        prepare_inputs(altered, "tampered-rule")
