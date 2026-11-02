"""Transport integration with fake RAGFlow, never SP or a real service."""

import json
from dataclasses import replace

import pytest

from dcc_harness.data import InputFile, load_tasks
from dcc_harness.datasets.benchmark_plugins import build_inputs, resolve_dataset_plugin
from dcc_harness.preprocessing.clause import (
    load,
    make_plugin,
    render_clause,
    validate_dataset,
)
from dcc_harness.preprocessing.pipeline import prepare
from dcc_harness.preprocessing.ragflow import index_bundle, retrieve, RagflowError
from test_clause_plugin import sample
from test_dcc_preprocessing import FakeRagflow, config


def bundle(tmp_path, variant="l1"):
    from dcc_harness.datasets.clause import prompt_text

    task = replace(
        sample(),
        prompt=prompt_text(variant),
        public_metadata={"prompt_variant": variant},
    )
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps(task.to_dict()) + "\n")
    cfg = config()
    cfg.update(
        loader="dcc_harness.preprocessing.clause:load",
        loader_options={"source": str(source)},
    )
    path = tmp_path / "bundle"
    prepare(cfg, path)
    fake = FakeRagflow()
    index_bundle(path, cfg["ragflow"], client=fake)
    render_clause(path, cfg)
    return path, fake


@pytest.mark.parametrize("variant", ["l1", "l2"])
def test_roundtrip_scope_tools_and_offline_inputs(tmp_path, monkeypatch, variant):
    path, fake = bundle(tmp_path, variant)
    public = load_tasks(path / "public/clause-tasks.jsonl")
    private = load_tasks(path / "private/clause-tasks.jsonl")
    assert public[0].input_sha256() == private[0].input_sha256()
    validate_dataset(public)
    monkeypatch.setenv("DCC_BUNDLE", str(path))
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "runtime"))
    plugin = make_plugin()
    inputs = build_inputs(plugin, public[0].public_input(), "rag-thread")
    assert [f["name"] for f in inputs["uploaded_files"]] == ["dcc-context.json"]
    text = str(inputs)
    assert "PRIVATE" not in text and sample().files[0].text not in text
    assert "ragflow_retrieve" in text
    assert plugin.tools[0].name == "ragflow_retrieve"
    assert plugin.parse_answer(public[0].public_input(), "[]") == []
    context = json.loads(
        next(f.text for f in public[0].files if f.name == "dcc-context.json")
    )
    retrieve(fake, context["retrieval"], "payment")
    assert fake.payload["dataset_ids"] == context["retrieval"]["dataset_ids"]
    assert fake.payload["document_ids"] == context["retrieval"]["document_ids"]
    assert fake.uploads == [sample().files[0].text.encode()]
    # Existing file transport still stages its source and has no RAG tool.
    original = resolve_dataset_plugin("clause")
    assert not original.tools
    assert (
        build_inputs(original, sample().public_input(), "file-thread")[
            "uploaded_files"
        ][0]["name"]
        == "document.txt"
    )


def test_tampered_document_or_scope_fails_before_staging(tmp_path, monkeypatch):
    path, _ = bundle(tmp_path)
    monkeypatch.setenv("DCC_BUNDLE", str(path))
    task = load_tasks(path / "public/clause-tasks.jsonl")[0]
    modified = replace(
        task,
        files=tuple(
            InputFile(f.name, "wrong") if f.name == "document.txt" else f
            for f in task.files
        ),
    )
    with pytest.raises(ValueError):
        validate_dataset([modified])
    with pytest.raises(ValueError, match="scope"):
        make_plugin().prepare_inputs(modified.public_input(), "no-stage")
    state = json.loads((path / "index.json").read_text())
    state["scopes"][task.id]["document_ids"] = ["foreign"]
    (path / "index.json").write_text(json.dumps(state))
    with pytest.raises(ValueError, match="scope"):
        make_plugin().prepare_inputs(task.public_input(), "no-stage")


def test_render_rejects_private_tamper(tmp_path):
    path, _ = bundle(tmp_path)
    (path / "private/gold.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="private labels"):
        render_clause(path, config())


def test_loader_rejects_modified_frozen_prompt(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps(replace(sample(), prompt="wrong").to_dict()))
    with pytest.raises(ValueError, match="pinned"):
        list(load({"source": str(source)}))


def test_retrieval_rejects_foreign_hits(tmp_path):
    path, fake = bundle(tmp_path)
    scope = next(iter(json.loads((path / "index.json").read_text())["scopes"].values()))
    fake.retrieve = lambda payload: {
        "chunks": [{"doc_id": "foreign", "content": "bad"}]
    }
    with pytest.raises(RagflowError, match="outside"):
        retrieve(fake, scope, "payment")


def test_private_labels_and_metric_parity(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from dcc_harness.clause_metrics import protocol, TEXT_KEYS
    from dcc_harness.datasets import clause
    from dcc_harness.evaluation.offline import EvaluationContext
    from dcc_harness.preprocessing.pipeline import digest

    task = sample()
    gold = [
        {
            "perturbation": [
                {
                    "contradiction_exists": "YES",
                    "changed_text": task.files[0].text,
                    "explanation": "PRIVATE_LABEL_SENTINEL",
                }
            ]
        }
    ]
    task = replace(
        task,
        gold=gold,
        metadata={
            **task.metadata,
            "protocol": protocol()["protocol"],
            "input_sha256": digest(task.files[0].text.encode()),
            "perturbations": 1,
        },
    )
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps(task.to_dict()))
    cfg = config()
    cfg.update(
        loader="dcc_harness.preprocessing.clause:load",
        loader_options={"source": str(source)},
    )
    path = tmp_path / "bundle"
    prepare(cfg, path)
    fake = FakeRagflow()
    index_bundle(path, cfg["ragflow"], client=fake)
    render_clause(path, cfg)
    public = load_tasks(path / "public/clause-tasks.jsonl")[0]
    private = load_tasks(path / "private/clause-tasks.jsonl")[0]
    assert public.gold is None and private.gold == task.gold
    assert "PRIVATE_LABEL_SENTINEL" not in str(public.public_input())
    assert all(b"PRIVATE_LABEL_SENTINEL" not in payload for payload in fake.uploads)
    monkeypatch.delenv("DCC_BUNDLE", raising=False)
    monkeypatch.delenv("DCC_CONFIG", raising=False)
    calc = SimpleNamespace(
        sentence_tokenize=lambda text: [text],
        score=lambda a, b: {key: 1.0 for key in TEXT_KEYS},
        truncated_groups=0,
        bert=object(),
    )
    context = EvaluationContext(
        resources={"text_metrics": calc},
        provenance={"bert": {}},
        diagnostic_partial=True,
    )
    answer = json.dumps(
        [{"text": task.files[0].text, "explanation": "conflicting dates"}]
    )
    metrics = make_plugin().score_task(private, answer, context)
    assert metrics == clause.score_task(task, answer, context)
    result = make_plugin().aggregate([private], [{"metrics": metrics}], context)
    assert result["input_mode"] == "sp2-ragflow" and result["scored_documents"] == 1
    assert not result["complete_benchmark_split"]


def test_plugin_registers_tool_in_native_app():
    from pathlib import Path
    from deerflow.config.app_config import AppConfig
    from dcc_harness.datasets.benchmark_plugins import configure_dataset_app

    root = Path(__file__).resolve().parents[1]
    plugin = resolve_dataset_plugin(
        "clause_ragflow", ("dcc_harness.preprocessing.clause:make_plugin",)
    )
    app = configure_dataset_app(
        AppConfig.from_file(str(root / "configs/stackplanner_benchmarks.yaml")), plugin
    )
    assert any(
        t.name == "ragflow_retrieve"
        and t.use == "dcc_harness.preprocessing.native_tools:ragflow_retrieve"
        for t in app.tools
    )
    assert Path(app.skills.path) == plugin.skills_path
    assert not plugin.requires_judge


def test_example_runtime_passes_bounded_reporter_budget_to_native_registry():
    from pathlib import Path
    from types import SimpleNamespace

    from deerflow.config.app_config import AppConfig
    from deerflow.subagents.executor import _reserve_subagent_loop_guard_budget
    from deerflow.subagents.registry import get_subagent_config

    root = Path(__file__).resolve().parents[1]
    app = AppConfig.from_file(
        str(root / "configs/clause/stackplanner-qwen.example.yaml")
    )
    reporter = get_subagent_config("sp-reporter", app_config=app)
    assert reporter.max_turns == 128
    guard = SimpleNamespace(tool_freq_hard_limit=50, warn_threshold=3, hard_limit=5)
    _reserve_subagent_loop_guard_budget([guard], max_turns=reporter.max_turns)
    assert guard.tool_freq_hard_limit == 51
    assert not hasattr(guard, "tool_total_hard_limit")
    assert (guard.warn_threshold, guard.hard_limit) == (3, 5)
    assert app.max_tool_calls_per_run == 200
    assert app.sandbox.read_file_output_max_chars == 262144
    assert app.models[0].extra_body["chat_template_kwargs"]["enable_thinking"] is False
