"""Shared-index release tests; fake RAGFlow and small pinned test protocol."""

import json
from dataclasses import replace

import pytest

from dcc_harness.data import load_tasks
from dcc_harness.datasets import clause
from dcc_harness.preprocessing import clause_release as release
from dcc_harness.preprocessing.clause import render_clause
from dcc_harness.preprocessing.pipeline import digest
from dcc_harness.preprocessing.ragflow import index_bundle
from test_clause_plugin import sample
from test_dcc_preprocessing import FakeRagflow, config


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "private").mkdir(parents=True)
    protocol = {**clause.protocol(), "splits": {}}
    for split in release.SPLITS:
        task = replace(sample(), id="sample-" + split, split=split)
        gold = [
            {
                "perturbation": [
                    {
                        "contradiction_exists": "YES",
                        "changed_text": task.files[0].text,
                        "explanation": "PRIVATE_LABEL",
                    }
                ]
            }
        ]
        task = replace(
            task,
            gold=gold,
            metadata={
                **task.metadata,
                "protocol": protocol["protocol"],
                "input_sha256": digest(task.files[0].text.encode()),
                "perturbations": 1,
            },
        )
        (source / "private" / f"{split}.jsonl").write_text(json.dumps(task.to_dict()))
        protocol["splits"][split] = {
            "documents": 1,
            "ids_sha256": clause.ids_hash([task.id]),
        }
    monkeypatch.setattr(clause, "protocol", lambda: protocol)
    root = tmp_path / "prepared"
    cfg = config()
    release.prepare_all(source, root, cfg)
    fake = FakeRagflow()
    for split in release.SPLITS:
        index_bundle(root / split, cfg["ragflow"], client=fake)
    release.render_all(root)
    return root, cfg


def test_shared_index_and_file_views(prepared):
    root, cfg = prepared
    for split in release.SPLITS:
        bundle = root / split
        before = (bundle / "index.json").read_bytes()
        for variant in release.VARIANTS:
            render_clause(bundle, cfg, variant=variant)
        assert before == (bundle / "index.json").read_bytes()
        for variant in release.VARIANTS:
            rag = load_tasks(bundle / "private" / f"clause-{variant}-tasks.jsonl")[0]
            file = load_tasks(
                bundle / "private" / f"clause-files-{variant}-tasks.jsonl"
            )[0]
            assert (
                rag.gold == file.gold
                and rag.prompt == file.prompt == clause.prompt_text(variant)
            )
            assert (
                next(f.text for f in rag.files if f.name == "document.txt")
                == file.files[0].text
            )
    assert release.verify_data(root) == {
        "documents": 3,
        "splits": {"train": 1, "validation": 1, "test": 1},
        "knowledge_bases": 3,
    }


def test_pack_install_portability_and_secret_exclusion(prepared, tmp_path):
    root, _ = prepared
    (root / "secret.key").write_text("DO_NOT_PACKAGE")
    cfg = json.loads((root / "config.json").read_text())
    cfg["ragflow"]["api_key"] = "DO_NOT_PACKAGE"
    cfg["ragflow"]["api_key_file"] = "/private/key"
    (root / "config.json").write_text(json.dumps(cfg))
    archive = tmp_path / "clause.tar.gz"
    descriptor = release.pack(root, archive, "test-v1")
    dest = tmp_path / "installed"
    assert release.install(archive, dest, descriptor["sha256"])["documents"] == 3
    assert not (dest / "secret.key").exists()
    with pytest.raises(ValueError, match="read-only"):
        index_bundle(dest / "test", cfg["ragflow"], client=FakeRagflow())
    assert "DO_NOT_PACKAGE" not in (dest / "config.json").read_text()
    (dest / ".dcc.lock").touch()
    release.verify_release(dest)
    with pytest.raises(FileExistsError):
        release.install(archive, dest, descriptor["sha256"])
    with pytest.raises(ValueError, match="SHA-256"):
        release.install(archive, tmp_path / "wrong", "0" * 64)
    p = dest / "test/public/clause-l2-tasks.jsonl"
    p.write_text(p.read_text() + "\n")
    with pytest.raises(ValueError, match="checksum"):
        release.verify_release(dest)


def test_prepare_rejects_incomplete_split(prepared, tmp_path):
    root, cfg = prepared
    source = tmp_path / "source"
    p = source / "private/test.jsonl"
    data = json.loads(p.read_text())
    data["id"] = "different"
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="complete pinned"):
        release.prepare_all(source, tmp_path / "bad", cfg)
    assert not (tmp_path / "bad").exists()


def test_verify_rejects_changed_gold(prepared):
    root, _ = prepared
    p = root / "test/private/gold.jsonl"
    p.write_text("{}\n")
    with pytest.raises(ValueError, match="labels differ"):
        release.verify_data(root)


def test_rejects_valid_but_changed_rendered_annotation(prepared):
    root, _ = prepared
    path = root / "test/private/clause-l1-tasks.jsonl"
    row = json.loads(path.read_text())
    row["gold"][0]["perturbation"][0]["explanation"] = "changed annotation"
    path.write_text(json.dumps(row))
    with pytest.raises(ValueError, match="canonical annotations"):
        release.verify_data(root)


@pytest.mark.parametrize("empty_hits", [False, True])
def test_full_service_audit_checks_every_sample(prepared, monkeypatch, empty_hits):
    from types import SimpleNamespace
    from dcc_harness.preprocessing import ragflow

    root, cfg = prepared
    scopes = {}
    for split in release.SPLITS:
        state = json.loads((root / split / "index.json").read_text())
        scopes.update(
            {s["dataset_ids"][0]: s["document_ids"] for s in state["scopes"].values()}
        )
    visited = []

    class Client:
        def __init__(self, config):
            self.base_url = config["base_url"]
            self.session = SimpleNamespace(close=lambda: None)

        def list_documents(self, kb):
            visited.append(kb)
            return [{"id": i, "run": "DONE", "chunk_count": 2} for i in scopes[kb]]

        def retrieve(self, payload):
            return {
                "chunks": []
                if empty_hits
                else [{"doc_id": payload["document_ids"][0], "content": "contract"}]
            }

    monkeypatch.setattr(ragflow, "RagflowClient", Client)
    if empty_hits:
        with pytest.raises(ValueError, match="no retrieval"):
            release.audit_service(root)
    else:
        result = release.audit_service(root)
        assert result["passed"] and result["checked_samples"] == 3 and len(visited) == 3
