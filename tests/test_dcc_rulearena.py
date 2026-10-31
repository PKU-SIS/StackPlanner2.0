from __future__ import annotations

import dataclasses
import json
from copy import deepcopy
from pathlib import Path

import pytest

from dcc_harness.data import load_tasks
from dcc_harness.preprocessing import rulearena
from dcc_harness.preprocessing.models import Asset, Sample
from dcc_harness.preprocessing.pipeline import (
    atomic_json,
    digest,
    prepare,
    read_jsonl,
    render,
)
from dcc_harness.preprocessing.ragflow import index_bundle
from dcc_harness.preprocessing.releases import install_release, verify_release
from scripts.dcc_preprocess import load_config, main

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def nba_source(tmp_path, monkeypatch):
    source = tmp_path / "original NBA"
    source.mkdir()
    template = (
        "The historical Salary Cap is $140,588,000.\nReference Rules:\n$reference_rules\n$example\n"
        "Decide whether any operation violates the rules:\n$question\n"
        'Conclude with "Answer: False." or "Answer: True. Illegal Operation: X. Problematic Team: Y."'
    )
    files = {
        "LICENSE": "MIT test fixture",
        "README.md": "Original source card",
        "nba/reference_rules.txt": "PUBLIC RULES: cap $140,588,000; preserve 5% and Ω.\n",
        "nba/auto_test.py": f"system_prompt = 'You are a helpful NBA team consultant.'\nprompt_template = {template!r}\nraise RuntimeError('MUST NEVER EXECUTE SOURCE')\n",
    }
    base = {
        "n_teams": 999,
        "n_players": 888,
        "n_operations": 777,
        "team_situations": ["Team A has a salary of $1,000,000."],
        "player_situations": ["Player A: Ω, $500,000, 5% annual increase."],
        "operations": ["A. Team A signs Player A."],
        "answer": False,
        "relevant_rules": ["PRIVATE_RULE_LABEL"],
    }
    for level in range(3):
        rows = [deepcopy(base)]
        if level == 0:
            violation = {**deepcopy(base), "answer": True, "illegal_operation": "A", "problematic_team": "A"}
            rows.append(violation)
        else:
            rows[0]["operations"] = [f"{chr(65 + level)}. Team A signs Player A."]
        files[f"nba/annotated_problems/comp_{level}.json"] = json.dumps(rows, ensure_ascii=False)
    for name, content in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    manifest = {
        "repository": "SkyRiver-2000/RuleArena",
        "revision": "fixture-pinned",
        "license": "MIT",
        "files": [{"path": name, "sha256": digest((source / name).read_bytes())} for name in files],
        "counts": {"comp_0": 2, "comp_1": 1, "comp_2": 1},
    }
    atomic_json(source / "manifest.json", manifest)
    specification = tmp_path / "source-spec.json"
    atomic_json(specification, manifest)
    monkeypatch.setattr(rulearena, "SOURCE_SPEC", specification)
    cfg = load_config(ROOT / "configs/dcc/preprocessing.rulearena-nba.example.json")
    cfg["loader_options"]["source"] = str(source)
    prompt = tmp_path / "nba.md"
    prompt.write_text(rulearena.prompt_layer(source))
    cfg["prompts"]["datasets"]["rulearena_nba"] = [str(prompt)]
    return source, cfg


def test_loader_preserves_official_scenario_gold_and_duplicate_samples(nba_source):
    source, cfg = nba_source
    samples = list(rulearena.load(cfg["loader_options"]))
    assert len(samples) == 4
    assert samples[0].documents[0].text == (
        "Team Situations:\nTeam A has a salary of $1,000,000.\n\n"
        "Player Situations:\nPlayer A: Ω, $500,000, 5% annual increase.\n\n"
        "Operations:\nA. Team A signs Player A."
    )
    assert samples[0].gold == {"answer": False, "relevant_rules": ["PRIVATE_RULE_LABEL"]}
    assert samples[1].gold["answer"] is True
    assert samples[1].gold["illegal_operation"] == "A"
    assert samples[0].id != samples[1].id and samples[0].documents == samples[1].documents
    assert samples[0].rules[0].read_bytes() == (source / "nba/reference_rules.txt").read_bytes()
    assert all(item.split == "test" and not item.public_metadata for item in samples)
    assert [item.metadata["difficulty_level"] for item in samples] == [1, 1, 2, 3]
    assert samples[0].metadata["n_teams"] == 999


def test_inline_prepare_render_cli_needs_no_ragflow(nba_source, tmp_path, monkeypatch, capsys):
    _source, cfg = nba_source

    def forbidden(*args, **kwargs):
        pytest.fail("inline processing must never initialize RAGFlow")

    monkeypatch.setattr("scripts.dcc_preprocess.RagflowClient", forbidden)
    monkeypatch.setattr("dcc_harness.preprocessing.ragflow.RagflowClient", forbidden)
    monkeypatch.delenv("RAGFLOW_API_KEY", raising=False)
    monkeypatch.delenv("RAGFLOW_API_KEY_FILE", raising=False)
    config_path = tmp_path / "config.json"
    atomic_json(config_path, cfg)
    bundle = tmp_path / "bundle"
    assert main(["prepare", "--config", str(config_path), "--output", str(bundle)]) == 0
    assert main(["render", "--config", str(config_path), "--bundle", str(bundle)]) == 0
    assert main(["doctor", "--config", str(config_path)]) == 0
    assert main(["status", "--bundle", str(bundle)]) == 0
    assert '"index_required": false' in capsys.readouterr().out
    assert not (bundle / "index.json").exists()
    tasks = load_tasks(bundle / "public/tasks.jsonl")
    for task in tasks:
        assert task.benchmark == "dcc_inline" and task.document in task.prompt
        assert "$140,588,000" in task.prompt and "$$140" not in task.prompt
        assert "PRIVATE_" not in task.prompt and "999" not in task.prompt
        assert "ragflow" not in task.prompt.lower()
        assert task.gold is None
        context = json.loads(next(item.text for item in task.files if item.name == "dcc-context.json"))
        assert "retrieval" not in context and context["document_mode"] == "inline"
    with pytest.raises(ValueError, match="inline"):
        index_bundle(bundle, {})
    with pytest.raises(ValueError, match="inline"):
        main(["index", "--config", str(config_path), "--bundle", str(bundle)])
    with pytest.raises(ValueError, match="inline"):
        main(["retrieve", "--config", str(config_path), "--bundle", str(bundle), "--sample-id", "x", "--question", "x"])


def test_inline_native_plugin_stages_rules_without_tool_or_key(nba_source, tmp_path, monkeypatch):
    pytest.importorskip("langchain")
    from dcc_harness.datasets.benchmark_plugins import (
        build_inputs,
        resolve_dataset_plugin,
    )
    from dcc_harness.preprocessing.native import context_for_task, prepare_inputs

    _, cfg = nba_source
    bundle = tmp_path / "bundle"
    prepare(cfg, bundle)
    render(bundle, cfg)
    monkeypatch.setenv("DCC_BUNDLE", str(bundle))
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "sp-home"))
    monkeypatch.delenv("DCC_CONFIG", raising=False)
    monkeypatch.delenv("RAGFLOW_API_KEY", raising=False)
    task = load_tasks(bundle / "public/tasks.jsonl")[0]
    assert context_for_task(task, bundle)["document_mode"] == "inline"
    plugin = resolve_dataset_plugin("dcc_inline", ("dcc_harness.preprocessing.native:make_inline_plugin",))
    assert not plugin.tools
    inputs = build_inputs(plugin, task.public_input(), "nba-inline")
    assert task.document in inputs["messages"][0].content
    assert len(inputs["messages"][0].content) <= 12_000
    assert {item["name"] for item in inputs["uploaded_files"]} == {"dcc-context.json", "dcc-rule-0.txt"}
    assert "PRIVATE_" not in repr(inputs) and str(bundle) not in repr(inputs)
    altered = dataclasses.replace(task.public_input(), document="Changed scenario")
    with pytest.raises(ValueError, match="content changed"):
        prepare_inputs(altered, "tampered")


def test_nba_release_is_portable_and_keeps_raw_annotations(nba_source, tmp_path):
    source, cfg = nba_source
    bundle = tmp_path / "prepared"
    prepare(cfg, bundle)
    render(bundle, cfg)
    archive = tmp_path / "nba.tar.gz"
    result = rulearena.build_release(bundle, source, cfg, archive, version="nba-test")
    destination = tmp_path / "another server"
    installed = install_release(archive, destination, sha256=result["sha256"])
    assert installed["samples"] == 4
    metadata = verify_release(destination)
    assert metadata["ragflow_required"] is False and metadata["processing_audit"]["scenario_rule_label_parity"]
    assert not (destination / "bundle/index.json").exists()
    portable = load_config(destination / "config.json")
    assert "ragflow" not in portable
    assert Path(portable["loader_options"]["source"]) == destination / "raw/RuleArenaNBA"
    assert len(list(rulearena.load(portable["loader_options"]))) == 4
    assert len(render(destination / "bundle", portable)) == 4
    assert (source / "nba/annotated_problems/comp_0.json").read_bytes() == (destination / "raw/RuleArenaNBA/nba/annotated_problems/comp_0.json").read_bytes()
    assert read_jsonl(destination / "bundle/private/gold.jsonl")[0]["gold"]["relevant_rules"] == ["PRIVATE_RULE_LABEL"]
    cfg["processors"] = [{"use": "method:optimize"}]
    with pytest.raises(ValueError, match="no processors"):
        rulearena.build_release(bundle, source, cfg, tmp_path / "not-built.tar.gz", version="invalid")


def test_source_changes_and_invalid_level_selections_are_rejected(nba_source):
    source, cfg = nba_source
    for levels in ([], [0, 0], [3], [True]):
        with pytest.raises(ValueError, match="levels"):
            list(rulearena.load({**cfg["loader_options"], "levels": levels}))
    (source / "nba/reference_rules.txt").write_text("Shortened or optimized rules")
    with pytest.raises(ValueError, match="checksum"):
        list(rulearena.load(cfg["loader_options"]))


def test_inline_mode_is_generic_and_rejects_hidden_or_binary_evidence(tmp_path):
    cfg = {"document_mode": "inline"}
    bundle = tmp_path / "bundle"
    item = Sample("private-id", "demo", "test", "Check the rule", (Asset("case", text="Full case: $42, Ω"),), (Asset("rule", text="PUBLIC RULE"),))
    prepare(cfg, bundle, samples=[item])
    tasks = render(bundle, cfg)
    assert item.documents[0].text in tasks[0]["prompt"]
    with pytest.raises(ValueError, match="complete document_text"):
        render(bundle, {**cfg, "prompts": {"layers": ["literal:Scenario omitted"]}})
    with pytest.raises(ValueError, match="differs"):
        render(bundle, {"document_mode": "ragflow"})
    binary = tmp_path / "document.pdf"
    binary.write_bytes(b"%PDF\xff")
    prepare(cfg, tmp_path / "binary", samples=[dataclasses.replace(item, documents=(Asset("pdf", path=binary),))])
    with pytest.raises(ValueError, match="UTF-8"):
        render(tmp_path / "binary", cfg)
