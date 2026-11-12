"""Portable originals and immutable, read-only reuse of shared remote parses."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from app.experiments.collusion_audit import prepare, shared_knowledge
from app.experiments.collusion_audit.knowledge import KnowledgeStore, order_chunks


def sample(tmp_path, monkeypatch):
    source = tmp_path / "originals"
    source.mkdir()
    p = source / "招标文件.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with p.open("wb") as f:
        writer.write(f)
    prepared = tmp_path / "prepared"
    manifest = prepare.prepare(source, prepared)
    doc = manifest["documents"][0]
    rows = order_chunks([{"id": "chunk1", "document_id": "remote1", "content": "synthetic text", "positions": [[1, 0, 1, 0, 1]]}])
    body = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    mapping = {
        "ready": True,
        "base_url": "https://rag.example",
        "options": {},
        "fingerprint": "synthetic",
        "project_manifest": manifest,
        "documents": {doc["id"]: {"dataset_id": "dataset1", "document_id": "remote1", "source_sha256": doc["expected_sha256"], "chunk_export_sha256": hashlib.sha256(body.encode()).hexdigest(), "chunk_count": 1, "original_pages": 1}},
    }
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps(mapping))
    calls = []

    class Client:
        session = SimpleNamespace(close=lambda: None)

        def __init__(self, *args):
            pass

        def documents(self, dataset):
            calls.append(("documents", dataset))
            return [{"id": "remote1", "run": "DONE"}]

        def chunks(self, dataset, doc):
            calls.append(("chunks", dataset, doc))
            return rows

    monkeypatch.setattr(shared_knowledge, "RagClient", Client)
    monkeypatch.setattr(shared_knowledge, "ragflow_settings", lambda _: {"RAGFLOW_API_URL": "https://rag.example", "RAGFLOW_API_KEY": "synthetic"})
    return path, prepared / "project_complete", tmp_path / "snapshot", rows, calls


def test_stage_originals_and_reuse_shared_mapping_without_uploads(tmp_path, monkeypatch):
    mapping, project, output, _, calls = sample(tmp_path, monkeypatch)
    state = shared_knowledge.restore(mapping, project, output)
    store = KnowledgeStore(output)
    assert state["ready"] and store.rows("d001")[0]["id"] == "chunk1"
    assert calls == [("documents", "dataset1"), ("chunks", "dataset1", "remote1")]
    relocated = tmp_path / "relocated"
    manifest = prepare.prepare(project, relocated, shared_mapping=mapping)
    assert manifest == json.loads((project / "manifest.json").read_text())


@pytest.mark.parametrize("mutation", ["original", "chunk", "document_id", "incomplete"])
def test_restore_rejects_changed_original_or_remote_content(tmp_path, monkeypatch, mutation):
    mapping, project, output, rows, calls = sample(tmp_path, monkeypatch)
    if mutation == "original":
        (project / "招标文件.pdf").write_bytes(b"changed")
    elif mutation == "chunk":
        rows[0]["content"] = "changed"
    elif mutation == "document_id":
        rows[0]["document_id"] = "foreign"
    else:
        value = json.loads(mapping.read_text())
        value["ready"] = False
        mapping.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        shared_knowledge.restore(mapping, project, output)
    assert not output.exists()
    if mutation in {"original", "incomplete"}:
        assert calls == []


def test_preparation_never_overwrites_previous_originals(tmp_path, monkeypatch):
    _, project, _, _, _ = sample(tmp_path, monkeypatch)
    before = (project / "manifest.json").read_bytes()
    with pytest.raises(ValueError):
        prepare.prepare(project, project.parent)
    assert (project / "manifest.json").read_bytes() == before


def test_worker_passes_external_project_to_native_task(tmp_path, monkeypatch):
    from app.experiments.collusion_audit import runner

    project = tmp_path / "project"
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps([{"合并规则ID": "M01"}]))
    seen = []

    async def run(rule, directory, **options):
        seen.append(options["project"])
        return {"status": "completed"}

    monkeypatch.setattr(runner, "setup_env", lambda *args: None)
    monkeypatch.setattr(runner, "run_rule_task", run)
    args = SimpleNamespace(
        worker_directory=tmp_path / "outputs",
        env_file=None,
        rules=rules,
        project=project,
        rule=["M01"],
        timeout=30,
        model="qwen3-8-27b",
        knowledge_root=None,
        rule_level_review=False,
        quality_controls=False,
    )
    assert runner.worker(args) == 0
    assert seen == [project]
