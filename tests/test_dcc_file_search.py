"""Raw evidence files are a separate transport; RAGFlow and source data stay immutable."""

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dcc_harness.data import InputFile, load_tasks
from dcc_harness.preprocessing.file_search import export_file_bundle
from dcc_harness.preprocessing.models import Asset, Sample
from dcc_harness.preprocessing.native import (
    context_for_task,
    make_file_plugin,
    prepare_inputs,
)
from dcc_harness.preprocessing.pipeline import prepare, read_jsonl, render
from dcc_harness.preprocessing.ragflow import index_bundle
from dcc_harness.preprocessing.releases import validate_ready_bundle

ROOT = Path(__file__).resolve().parents[1]
XML = '<link:calculationArc xmlns:link="http://www.xbrl.org/2003/linkbase" xlink:from="Total" xlink:to="Item" weight="-1"/>'


def fixture_bundle(tmp_path, monkeypatch):
    import dcc_harness.preprocessing.ragflow as ragflow

    def forbidden(*args, **kwargs):
        pytest.fail("file transport must not access RAGFlow")

    monkeypatch.setattr(ragflow.RagflowClient, "__init__", forbidden)
    monkeypatch.delenv("RAGFLOW_API_KEY", raising=False)
    monkeypatch.delenv("RAGFLOW_API_KEY_FILE", raising=False)
    cfg = {"document_mode": "files", "ragflow": {"base_url": "https://unused.invalid"}}
    sample = Sample(
        "PRIVATE_SAMPLE_ID",
        "demo",
        "test",
        "Check Total and Item.",
        (Asset("xml", text=XML, title="Calculation document"),),
        (Asset("policy", text="PUBLIC RULE"),),
        gold="PRIVATE GOLD",
        metadata={"private": "PRIVATE META"},
    )
    bundle = tmp_path / "files"
    prepare(cfg, bundle, samples=[sample])
    render(bundle, cfg)
    monkeypatch.setenv("DCC_BUNDLE", str(bundle))
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "native"))
    return bundle, cfg


def test_file_transport_preserves_original_xml_without_inline_expansion_or_ragflow(tmp_path, monkeypatch):
    bundle, _ = fixture_bundle(tmp_path, monkeypatch)
    manifest, samples = validate_ready_bundle(bundle)
    assert manifest["document_mode"] == "files" and not (bundle / "index.json").exists()
    task = load_tasks(bundle / "public/tasks.jsonl")[0]
    assert task.benchmark == "dcc_files" and not task.document and XML not in task.prompt
    context = context_for_task(task, bundle)
    assert context["document_mode"] == "files" and "retrieval" not in context
    evidence = next(f for f in task.files if f.name == context["documents"][0]["file"])
    assert evidence.text == XML == (bundle / samples[0]["documents"][0]["path"]).read_text()
    assert "grep" in task.prompt and "read_file" in task.prompt and "ragflow_retrieve" not in task.prompt
    public = "\n".join(p.read_text() for p in (bundle / "public").rglob("*") if p.is_file())
    assert "PRIVATE GOLD" not in public and "PRIVATE META" not in public
    with pytest.raises(ValueError, match="files.*index|index.*files"):
        index_bundle(bundle, {}, client=object())


def test_file_plugin_stages_exact_evidence_and_only_native_file_tools(tmp_path, monkeypatch):
    from deerflow.config.app_config import AppConfig
    from deerflow.config.paths import get_paths
    from deerflow.runtime.user_context import reset_current_user, set_current_user

    from dcc_harness.datasets.benchmark_plugins import (
        build_inputs,
        configure_dataset_app,
    )

    bundle, _ = fixture_bundle(tmp_path, monkeypatch)
    task = load_tasks(bundle / "public/tasks.jsonl")[0]
    plugin = make_file_plugin()
    app = configure_dataset_app(AppConfig.from_file(str(ROOT / "configs/stackplanner_benchmarks.yaml")), plugin)
    names = {t.name for t in app.tools}
    assert {"read_file", "grep", "glob", "bash"} <= names and "ragflow_retrieve" not in names
    assert plugin.name == "dcc_files" and callable(plugin.setup_evaluation)
    token = set_current_user(SimpleNamespace(id="files-user"))
    try:
        inputs = build_inputs(plugin, task.public_input(), "files-thread")
        uploads = get_paths().sandbox_uploads_dir("files-thread", user_id="files-user")
        assert {p.name for p in uploads.iterdir()} == {f.name for f in task.files}
        for item in task.files:
            assert (uploads / item.name).read_bytes() == item.text.encode()
        assert "PRIVATE_SAMPLE_ID" not in inputs["messages"][0].content
        assert "PRIVATE GOLD" not in inputs["messages"][0].content
    finally:
        reset_current_user(token)


def test_file_input_rejects_modified_evidence_before_staging(tmp_path, monkeypatch):
    bundle, _ = fixture_bundle(tmp_path, monkeypatch)
    task = load_tasks(bundle / "public/tasks.jsonl")[0]
    files = tuple(InputFile(f.name, "changed XML" if f.text == XML else f.text) for f in task.files)
    with pytest.raises(ValueError, match="document|evidence"):
        prepare_inputs(dataclasses.replace(task.public_input(), files=files), "tampered")


def test_file_cli_doctor_requires_no_service_key_and_native_context_rejects_index(tmp_path, monkeypatch, capsys):
    from dcc_harness.preprocessing.pipeline import atomic_json
    from scripts.dcc_preprocess import main

    bundle, cfg = fixture_bundle(tmp_path, monkeypatch)
    path = tmp_path / "files.json"
    atomic_json(path, cfg)
    assert main(["doctor", "--config", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["ragflow_required"] is False
    atomic_json(bundle / "index.json", {"ready": True})
    task = load_tasks(bundle / "public/tasks.jsonl")[0]
    with pytest.raises(ValueError, match="index"):
        prepare_inputs(task.public_input(), "bad-index")


def test_export_existing_split_to_files_preserves_groups_labels_and_source(tmp_path, monkeypatch):
    from test_dcc_document_splits import files, rag_bundle

    from dcc_harness.preprocessing.document_splits import make_definition
    from dcc_harness.preprocessing.splits import export_splits

    source, _ = rag_bundle(tmp_path)
    export_splits(source, make_definition(source, train_fraction="0.75"), tmp_path / "partitions")
    source = tmp_path / "partitions/test"
    before = files(source)
    target = tmp_path / "file-test"
    export_file_bundle(source, target, {"document_mode": "files"})
    manifest, samples = validate_ready_bundle(target)
    assert manifest["samples"] == 3 and manifest["document_mode"] == "files"
    assert not (target / "index.json").exists()
    assert (source / "public/samples.jsonl").read_bytes() == (target / "public/samples.jsonl").read_bytes()
    assert (source / "private/gold.jsonl").read_bytes() == (target / "private/gold.jsonl").read_bytes()
    original = {t.id: t for t in load_tasks(source / "public/tasks.jsonl")}
    assert {t.id: t.group_id for t in load_tasks(target / "public/tasks.jsonl")} == {key: t.group_id for key, t in original.items()}
    for sample in samples:
        for asset in sample["documents"] + sample["rules"]:
            assert (source / asset["path"]).read_bytes() == (target / asset["path"]).read_bytes()
    train_hashes = {a["sha256"] for s in read_jsonl(tmp_path / "partitions/train/public/samples.jsonl") for a in s["documents"]}
    test_hashes = {a["sha256"] for s in samples for a in s["documents"]}
    assert not train_hashes & test_hashes and files(source) == before
    with pytest.raises(FileExistsError):
        export_file_bundle(source, target, {"document_mode": "files"})
    with pytest.raises(ValueError, match="source"):
        export_file_bundle(source, source / "inside", {"document_mode": "files"})


def test_file_plugin_uses_existing_official_evaluator_without_ragflow_credentials(tmp_path, monkeypatch):
    from dcc_harness.evaluation.offline import EvaluationContext

    monkeypatch.delenv("RAGFLOW_API_KEY", raising=False)
    monkeypatch.delenv("RAGFLOW_API_KEY_FILE", raising=False)
    sample = Sample(
        "case",
        "finauditing_finre",
        "test",
        "Classify the relationship.",
        (Asset("evidence", text=XML),),
        (Asset("reference", text="PUBLIC RULE"),),
        gold={"answer": "Reversal"},
    )
    bundle = tmp_path / "evaluation-files"
    prepare({"document_mode": "files"}, bundle, samples=[sample])
    render(bundle, {"document_mode": "files"})
    task = load_tasks(bundle / "public/tasks.jsonl")[0]
    plugin = make_file_plugin()
    context = EvaluationContext(options={"bundle": str(bundle)})
    plugin.setup_evaluation(context)
    assert plugin.score_task(task, "Reversal", context)["accuracy"] == 1.0
