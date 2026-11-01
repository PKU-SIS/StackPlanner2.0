"""Shared evidence documents, including transitive/cross-task reuse, cannot cross splits."""

import json

import pytest

from dcc_harness.data import load_tasks
from dcc_harness.preprocessing.document_splits import document_groups, make_definition
from dcc_harness.preprocessing.models import Asset, Sample
from dcc_harness.preprocessing.native import context_for_task
from dcc_harness.preprocessing.pipeline import prepare, read_jsonl, render
from dcc_harness.preprocessing.ragflow import index_bundle
from dcc_harness.preprocessing.releases import validate_ready_bundle
from dcc_harness.preprocessing.splits import export_splits


def rag_bundle(tmp_path, *, all_shared=False):
    from test_dcc_preprocessing import FakeRagflow

    samples = []
    for kind in ["finre", "finmr", "finsm"]:
        for index in range(4):
            shared = {"finre": ["SHARED X"], "finmr": ["SHARED X", "SHARED Y"], "finsm": ["SHARED Y"]}[kind]
            documents = shared if index == 0 else [f"Unique filing {kind}-{index}"]
            if all_shared:
                documents = ["THE SAME EVIDENCE FOR EVERY SAMPLE"]
            samples.append(
                Sample(
                    f"{kind}-{index}",
                    f"finauditing_{kind}",
                    "test",
                    "Original question.",
                    tuple(Asset(f"d-{i}", text=text) for i, text in enumerate(documents)),
                    (Asset("common-reference", text="PUBLIC REFERENCE RULES"),),
                    gold={"answer": "PRIVATE GOLD"},
                    metadata={"source_revision": "fixture"},
                )
            )
    cfg = {"ragflow": {"base_url": "https://rag.example", "poll_interval": 0, "parse_timeout": 3}}
    bundle = tmp_path / "original"
    prepare(cfg, bundle, samples=samples)
    index_bundle(bundle, cfg["ragflow"], client=FakeRagflow())
    render(bundle, cfg)
    return bundle, cfg


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_document_components_are_transitive_across_subtasks_and_ignore_reference_rules(tmp_path):
    bundle, _ = rag_bundle(tmp_path)
    samples = read_jsonl(bundle / "public/samples.jsonl")
    groups = document_groups(samples, bundle)
    assert groups["finre-0"] == groups["finmr-0"] == groups["finsm-0"]
    assert len(set(groups.values())) == 10
    definition = make_definition(bundle, train_fraction="0.75")
    assert definition == make_definition(bundle, train_fraction="0.75")
    assert len(definition["partitions"]["train"]) == 9
    assert len(definition["partitions"]["test"]) == 3
    assert all(stats["samples"] == 3 for stats in definition["statistics"]["train"]["by_dataset"].values())
    assert all(stats["samples"] == 1 for stats in definition["statistics"]["test"]["by_dataset"].values())
    assert definition["document_isolation"]["shared_documents"] == 0


def test_impossible_quota_fails_instead_of_splitting_shared_documents(tmp_path):
    bundle, _ = rag_bundle(tmp_path, all_shared=True)
    with pytest.raises(ValueError, match="document groups cannot satisfy"):
        make_definition(bundle, train_fraction="0.75")


def test_rag_split_export_preserves_scopes_inputs_and_source_without_network_writes(tmp_path):
    bundle, cfg = rag_bundle(tmp_path)
    original = files(bundle)
    source_tasks = {t.id: t for t in load_tasks(bundle / "public/tasks.jsonl")}
    source_index = json.loads((bundle / "index.json").read_text())
    definition = make_definition(bundle, train_fraction="0.75")
    out = tmp_path / "new-partitions"
    export_splits(bundle, definition, out)
    hashes = {}
    kb_ids = {}
    for split in ["train", "test"]:
        target = out / split
        manifest, samples = validate_ready_bundle(target)
        hashes[split] = {d["sha256"] for s in samples for d in s["documents"]}
        tasks = load_tasks(target / "public/tasks.jsonl")
        index = json.loads((target / "index.json").read_text())
        assert index["read_only_view"]["split"] == split
        assert index["public_sha256"] == manifest["public_sha256"]
        assert set(index["scopes"]) == {t.id for t in tasks}
        kb_ids[split] = {key for scope in index["scopes"].values() for key in scope["dataset_ids"]}
        for task in tasks:
            assert task.split == split and task.input_sha256() == source_tasks[task.id].input_sha256()
            assert index["scopes"][task.id] == source_index["scopes"][task.id]
            assert context_for_task(task, target)["retrieval"]["dataset_ids"] == index["scopes"][task.id]["dataset_ids"]
        with pytest.raises(ValueError, match="read.only"):
            index_bundle(target, cfg["ragflow"], client=object())
    assert not hashes["train"] & hashes["test"]
    assert not kb_ids["train"] & kb_ids["test"]
    assert files(bundle) == original
