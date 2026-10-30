from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from dcc_harness.preprocessing.loaders import finauditing, manifest
from dcc_harness.preprocessing.models import Asset, Sample
from dcc_harness.preprocessing.pipeline import prepare, render
from dcc_harness.preprocessing.prompts import compose
from dcc_harness.preprocessing.ragflow import (
    RagflowClient,
    RagflowError,
    index_bundle,
    retrieve,
)


def sample(identifier="one", text="<xbrli:fact unitRef='USD'>10</xbrli:fact>"):
    return Sample(
        identifier,
        "demo",
        "test",
        "Check this filing.",
        documents=(Asset("filing", text=text),),
        rules=(Asset("policy", text="PUBLIC_RULE"),),
        gold={"answer": "PRIVATE_GOLD"},
        metadata={"source": "PRIVATE_SOURCE"},
    )


def config():
    return {"loader": "manifest", "ragflow": {"base_url": "https://rag.example", "dataset_prefix": "dcc-test", "poll_interval": 0, "parse_timeout": 3}}


class FakeRagflow:
    base_url = "https://rag.example"

    def __init__(self):
        self.datasets = {}
        self.docs = {}
        self.uploads = []
        self.parses = []
        self.fail = False

    def ensure_dataset(self, name, options):
        self.datasets.setdefault(name, {"id": "kb-" + str(len(self.datasets))})
        return self.datasets[name]["id"]

    def list_documents(self, dataset_id):
        return list(self.docs.get(dataset_id, {}).values())

    def upload_document(self, dataset_id, path):
        doc = {"id": "doc-" + str(len(self.uploads)), "name": path.name, "run": "UNSTART", "chunk_count": 0}
        self.docs.setdefault(dataset_id, {})[doc["id"]] = doc
        self.uploads.append(path.read_bytes())
        return doc["id"]

    def parse_documents(self, dataset_id, document_ids):
        self.parses.append(document_ids)
        for identifier in document_ids:
            self.docs[dataset_id][identifier].update(run="FAIL" if self.fail else "DONE", chunk_count=0 if self.fail else 2)

    def retrieve(self, payload):
        self.payload = payload
        return {"chunks": [{"doc_id": payload["document_ids"][0], "content": "evidence", "id": "chunk-1"}]}


def test_prepare_deduplicates_documents_and_separates_gold(tmp_path):
    prepare(config(), tmp_path / "bundle", samples=[sample(), sample("two")])
    bundle = tmp_path / "bundle"
    public = "\n".join(p.read_text() for p in (bundle / "public").rglob("*") if p.is_file())
    assert "PRIVATE_" not in public
    assert len(list((bundle / "public/documents").iterdir())) == 1
    assert "unitRef='USD'" in public
    assert "PRIVATE_GOLD" in (bundle / "private/gold.jsonl").read_text()
    with pytest.raises(FileExistsError):
        prepare(config(), bundle, samples=[sample()])


def test_duplicate_samples_fail_without_publishing_bundle(tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        prepare(config(), tmp_path / "bundle", samples=[sample(), sample()])
    assert not (tmp_path / "bundle").exists()


def test_manifest_resolves_files_and_rejects_path_escape(tmp_path):
    (tmp_path / "filing.pdf").write_bytes(b"%PDF-test")
    row = {
        "id": "a",
        "dataset": "other",
        "split": "test",
        "question": "Q",
        "documents": [{"id": "pdf", "path": "filing.pdf"}],
        "rules": [{"id": "r", "text": "Rule"}],
    }
    path = tmp_path / "data.jsonl"
    path.write_text(json.dumps(row) + "\n")
    loaded = list(manifest({"source": str(path)}))
    assert loaded[0].documents[0].read_bytes() == b"%PDF-test"
    row["documents"][0]["path"] = "../outside.pdf"
    path.write_text(json.dumps(row))
    with pytest.raises(ValueError, match="outside"):
        list(manifest({"source": str(path)}))


def test_finauditing_extracts_rules_documents_and_preserves_official_splits(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    query = (
        "Public instruction.\n"
        + "".join(
            "##" + heading + "\n" + body + "\n"
            for heading, body in [
                ("Schema document", "None"),
                ("Presentation linkbase document", "<link weight='-1'/>"),
                ("Calculation linkbase document", "None"),
                ("Definition linkbase document", "None"),
                ("Label linkbase document", "None"),
                ("Instance document", "<fact>1,000</fact>"),
                ("US GAAP Taxonomy", "TAXONOMY_RULES"),
            ]
        )
        + "Question: Check A?\nAnswer:\n"
    )
    directory = tmp_path / "FinRE"
    directory.mkdir()
    pq.write_table(
        pa.Table.from_pylist([{"id": 0, "query": json.dumps(query), "answer": json.dumps("PRIVATE_GOLD"), "gold": 2, "dqc_id": json.dumps("DQC_US_0001")}]),
        directory / "test.parquet",
    )
    item = next(iter(finauditing({"source": str(tmp_path), "tasks": ["FinRE"]})))
    assert item.question == "Question: Check A?"
    assert len(item.documents) == 2
    assert len(item.rules) == 2
    assert item.gold["answer"] == "PRIVATE_GOLD"
    assert item.split == "test"
    assert all("PRIVATE_" not in asset.read_bytes().decode() for asset in (*item.documents, *item.rules))


def test_prompt_layers_are_ordered_and_unknown_variables_fail(tmp_path):
    layer = tmp_path / "custom.md"
    layer.write_text("DATASET ${question}")
    assert compose(["literal:${question}", str(layer)], {"question": "Q"}) == "Q\n\nDATASET Q"
    with pytest.raises(ValueError, match="missing"):
        compose(["literal:${gold}"], {"question": "Q"})


@pytest.mark.parametrize(
    ("dataset", "required", "excluded"),
    [
        ("finauditing_finre", ("Reversal", "Inappropriateness", "CombinationErr", "Only the label"), ("extracted_value",)),
        ("finauditing_finmr", ("extracted_value", "calculated_value", "JSON object", "not computable"), ("CombinationErr",)),
        ("finauditing_finsm", ("JSON array of strings", "us-gaap:Revenues", "erroneous"), ("extracted_value", "CombinationErr")),
    ],
)
def test_finauditing_injects_subtask_requirements_and_output_contract(tmp_path, dataset, required, excluded):
    from scripts.dcc_preprocess import load_config

    path = Path(__file__).resolve().parents[1] / "configs/dcc/preprocessing.finauditing.example.json"
    cfg = load_config(path)
    cfg["ragflow"] = config()["ragflow"]
    item = dataclasses.replace(sample("PRIVATE_SAMPLE_ID"), dataset=dataset)
    bundle = tmp_path / "bundle"
    prepare(cfg, bundle, samples=[item])
    index_bundle(bundle, cfg["ragflow"], client=FakeRagflow())
    prompt = render(bundle, cfg)[0]["prompt"]
    assert all(text in prompt for text in required)
    assert all(text not in prompt for text in excluded)
    assert "PRIVATE_" not in prompt
    assert "ragflow_retrieve" in prompt


def test_index_resume_and_scoped_retrieval_do_not_upload_rules(tmp_path):
    bundle = tmp_path / "bundle"
    prepare(config(), bundle, samples=[sample(), sample("two", "Different filing")])
    client = FakeRagflow()
    state = index_bundle(bundle, config()["ragflow"], client=client)
    assert len(client.uploads) == 2
    assert all(b"PUBLIC_RULE" not in body and b"PRIVATE_GOLD" not in body for body in client.uploads)
    assert state["ready"] is True
    index_bundle(bundle, config()["ragflow"], client=client)
    assert len(client.uploads) == 2
    tasks = render(bundle, config())
    assert len(tasks) == 2 and all("PRIVATE_" not in json.dumps(t) for t in tasks)
    context = json.loads((bundle / tasks[0]["metadata"]["dcc"]["context_file"]).read_text())
    response = retrieve(client, context["retrieval"], "fact value")
    assert response["chunks"][0]["content"] == "evidence"
    assert len(client.payload["document_ids"]) == 1
    assert "PUBLIC_RULE" not in tasks[0]["prompt"]  # Rules are read as files.


@pytest.mark.parametrize("fail_first", [False, True])
def test_parallel_index_keeps_sample_scopes_and_resumes_after_failure(tmp_path, monkeypatch, fail_first):
    from threading import Lock
    from unittest.mock import Mock

    import dcc_harness.preprocessing.ragflow as module

    client = FakeRagflow()
    client.session = Mock()
    lock = Lock()
    for name in ("ensure_dataset", "upload_document", "parse_documents", "list_documents"):
        original = getattr(client, name)

        def guarded(*args, _method=original):
            with lock:
                return _method(*args)

        monkeypatch.setattr(client, name, guarded)
    monkeypatch.setattr(module, "RagflowClient", lambda cfg: client)
    bundle = tmp_path / "parallel"
    prepare(config(), bundle, samples=[sample(str(index)) for index in range(9)])
    cfg = {**config()["ragflow"], "index_workers": 3}
    if fail_first:
        client.fail = True
        with pytest.raises(RagflowError, match="FAIL"):
            index_bundle(bundle, cfg)
        assert not json.loads((bundle / "index.json").read_text())["ready"]
        client.fail = False
    state = index_bundle(bundle, cfg, retry_failed=fail_first)
    assert state["ready"] and len(state["scopes"]) == 9
    assert len({scope["dataset_ids"][0] for scope in state["scopes"].values()}) == 9
    assert len(client.uploads) == 9
    index_bundle(bundle, cfg)
    assert len(client.uploads) == 9
    assert len(render(bundle, config())) == 9


def test_independent_samples_have_dedicated_knowledge_bases_even_with_shared_content(tmp_path):
    bundle = tmp_path / "bundle"
    first = dataclasses.replace(sample(), documents=(Asset("shared", text="SHARED DOCUMENT"), Asset("first", text="FIRST ONLY")))
    second = dataclasses.replace(sample("two"), documents=(Asset("shared", text="SHARED DOCUMENT"), Asset("second", text="SECOND ONLY")))
    manifest = prepare(config(), bundle, samples=[first, second])
    client = FakeRagflow()
    state = index_bundle(bundle, config()["ragflow"], client=client)
    first_scope, second_scope = state["scopes"]["one"], state["scopes"]["two"]
    assert manifest["unique_documents"] == 3  # Local content storage can deduplicate.
    assert len(client.datasets) == 2  # RAGFlow libraries must not be shared.
    assert len(client.uploads) == 4
    assert len(first_scope["dataset_ids"]) == len(second_scope["dataset_ids"]) == 1
    assert first_scope["dataset_ids"] != second_scope["dataset_ids"]
    assert set(first_scope["document_ids"]).isdisjoint(second_scope["document_ids"])
    assert len(client.docs[first_scope["dataset_ids"][0]]) == len(client.docs[second_scope["dataset_ids"][0]]) == 2
    index_bundle(bundle, config()["ragflow"], client=client)
    assert len(client.uploads) == 4 and len(client.parses) == 2


def test_shared_knowledge_base_configuration_and_legacy_ledgers_are_rejected(tmp_path):
    bundle = tmp_path / "bundle"
    prepare(config(), bundle, samples=[sample()])
    client = FakeRagflow()
    with pytest.raises(ValueError, match="sample"):
        index_bundle(bundle, {**config()["ragflow"], "knowledge_base_scope": "dataset"}, client=client)
    assert not client.datasets
    (bundle / "index.json").write_text(json.dumps({"schema_version": 1, "configuration_sha256": "legacy", "ready": True}))
    with pytest.raises(ValueError, match="new bundle"):
        index_bundle(bundle, config()["ragflow"], client=client)
    assert not client.datasets


def test_render_rejects_shared_knowledge_base_scopes(tmp_path):
    bundle = tmp_path / "bundle"
    prepare(config(), bundle, samples=[sample(), sample("two")])
    state = index_bundle(bundle, config()["ragflow"], client=FakeRagflow())
    state["scopes"]["two"]["dataset_ids"] = state["scopes"]["one"]["dataset_ids"]
    (bundle / "index.json").write_text(json.dumps(state))
    with pytest.raises(RagflowError, match="knowledge base"):
        render(bundle, config())


def test_resume_rejects_foreign_documents_in_a_samples_knowledge_base(tmp_path):
    bundle = tmp_path / "bundle"
    prepare(config(), bundle, samples=[sample()])
    client = FakeRagflow()
    state = index_bundle(bundle, config()["ragflow"], client=client)
    dataset_id = state["scopes"]["one"]["dataset_ids"][0]
    client.docs[dataset_id]["foreign"] = {"id": "foreign", "name": "other-sample.txt", "run": "DONE", "chunk_count": 1}
    with pytest.raises(RagflowError, match="foreign documents"):
        index_bundle(bundle, config()["ragflow"], client=client)
    assert not json.loads((bundle / "index.json").read_text())["ready"]


def test_retrieval_requires_one_sample_knowledge_base():
    with pytest.raises(ValueError, match="scope"):
        retrieve(FakeRagflow(), {"dataset_ids": ["first", "second"], "document_ids": ["doc"]}, "Q")


def test_failed_parsing_and_empty_scope_fail_closed(tmp_path):
    bundle = tmp_path / "bundle"
    prepare(config(), bundle, samples=[sample()])
    client = FakeRagflow()
    client.fail = True
    with pytest.raises(RagflowError, match="pars"):
        index_bundle(bundle, config()["ragflow"], client=client)
    assert not json.loads((bundle / "index.json").read_text())["ready"]
    with pytest.raises(RagflowError, match="ready"):
        render(bundle, config())
    with pytest.raises(ValueError, match="scope"):
        retrieve(client, {"dataset_ids": ["kb"], "document_ids": []}, "Q")


def test_retrieval_rejects_cross_sample_hits():
    client = FakeRagflow()
    client.retrieve = lambda payload: {"chunks": [{"doc_id": "foreign", "content": "PRIVATE"}]}
    with pytest.raises(RagflowError, match="scope"):
        retrieve(client, {"dataset_ids": ["kb"], "document_ids": ["allowed"]}, "Q")


def test_changed_index_configuration_cannot_resume(tmp_path):
    bundle = tmp_path / "bundle"
    prepare(config(), bundle, samples=[sample()])
    client = FakeRagflow()
    index_bundle(bundle, config()["ragflow"], client=client)
    with pytest.raises(ValueError, match="configuration"):
        index_bundle(bundle, {**config()["ragflow"], "dataset_options": {"chunk_method": "paper"}}, client=client)


def test_ambiguous_upload_failure_is_reconciled_by_filename_on_resume(tmp_path):
    bundle = tmp_path / "bundle"
    prepare(config(), bundle, samples=[sample()])
    client = FakeRagflow()
    original = client.upload_document

    def interrupted(dataset_id, path):
        original(dataset_id, path)
        raise RagflowError("connection dropped after upload")

    client.upload_document = interrupted
    with pytest.raises(RagflowError, match="dropped"):
        index_bundle(bundle, config()["ragflow"], client=client)
    client.upload_document = original
    assert index_bundle(bundle, config()["ragflow"], client=client)["ready"]
    assert len(client.uploads) == 1


def test_modified_public_assets_and_changed_render_endpoint_are_rejected(tmp_path):
    bundle = tmp_path / "bundle"
    prepare(config(), bundle, samples=[sample()])
    client = FakeRagflow()
    index_bundle(bundle, config()["ragflow"], client=client)
    changed = {**config(), "ragflow": {**config()["ragflow"], "base_url": "https://other.example"}}
    with pytest.raises(ValueError, match="configuration"):
        render(bundle, changed)
    next((bundle / "public/documents").iterdir()).write_text("changed")
    with pytest.raises(ValueError, match="content changed"):
        index_bundle(bundle, config()["ragflow"], client=client)


def test_zero_chunks_cannot_be_rendered_as_a_ready_index(tmp_path):
    bundle = tmp_path / "bundle"
    prepare(config(), bundle, samples=[sample()])
    client = FakeRagflow()
    original = client.parse_documents

    def empty(dataset_id, document_ids):
        original(dataset_id, document_ids)
        for document in client.docs[dataset_id].values():
            document["chunk_count"] = 0

    client.parse_documents = empty
    with pytest.raises(RagflowError, match="zero indexed chunks"):
        index_bundle(bundle, config()["ragflow"], client=client)
    assert not json.loads((bundle / "index.json").read_text())["ready"]


def test_http_client_uses_document_file_and_application_error_contract(monkeypatch, tmp_path):
    monkeypatch.setenv("RAGFLOW_API_KEY", "PRIVATE_KEY")
    client = RagflowClient({"base_url": "https://rag.example/api/v1"})
    calls = []
    responses = [[], {"id": "kb"}, [{"id": "doc"}], None, {"docs": [{"id": "doc", "name": "input.txt"}]}]

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        response = Response()
        data = responses.pop(0)
        response.json = lambda: {"code": 0, "data": data}
        return response

    client.session.request = request
    path = tmp_path / "input.txt"
    path.write_text("EVIDENCE")
    assert client.ensure_dataset("name", {"chunk_method": "naive"}) == "kb"
    assert client.upload_document("kb", path) == "doc"
    client.parse_documents("kb", ["doc"])
    assert client.list_documents("kb")[0]["id"] == "doc"
    assert calls[1][2]["json"] == {"name": "name", "chunk_method": "naive"}
    assert "name" not in calls[0][2]["params"]
    assert calls[2][2]["files"]["file"][0] == "input.txt"
    assert calls[3][1] == "https://rag.example/api/v1/datasets/kb/chunks"
    assert calls[3][2]["json"] == {"document_ids": ["doc"]}


def test_managed_library_inventory_is_loaded_once_for_many_samples(monkeypatch):
    monkeypatch.setenv("RAGFLOW_API_KEY", "PRIVATE_KEY")
    client = RagflowClient({"base_url": "https://rag.example"})
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path))
        return [] if method == "GET" else {"id": kwargs["json"]["name"]}

    client._request = request
    assert client.ensure_dataset("sample-one", {}) == "sample-one"
    assert client.ensure_dataset("sample-two", {}) == "sample-two"
    assert client.ensure_dataset("sample-one", {}) == "sample-one"
    assert [method for method, _ in calls] == ["GET", "POST", "POST"]


def test_custom_processor_and_loader_plugins(tmp_path, monkeypatch):
    import sys
    import types

    module = types.ModuleType("dcc_test_extension")
    module.loader = lambda options: iter([sample()])
    module.processor = lambda item, options: dataclasses.replace(item, question=item.question + options["suffix"])
    monkeypatch.setitem(sys.modules, module.__name__, module)
    cfg = {**config(), "loader": "dcc_test_extension:loader", "processors": [{"use": "dcc_test_extension:processor", "options": {"suffix": " CUSTOM"}}]}
    prepare(cfg, tmp_path / "bundle")
    assert "CUSTOM" in (tmp_path / "bundle/public/samples.jsonl").read_text()


class Response:
    status_code = 200

    def json(self):
        return {"code": 401, "message": "Unauthorized"}


def test_client_checks_application_errors_and_does_not_expose_key(monkeypatch):
    monkeypatch.setenv("RAGFLOW_API_KEY", "PRIVATE_KEY")
    client = RagflowClient({"base_url": "https://rag.example"})
    client.session.request = lambda *args, **kwargs: Response()
    with pytest.raises(RagflowError) as exc:
        client.list_documents("kb")
    assert "401" in str(exc.value) and "PRIVATE_KEY" not in str(exc.value)


def test_embedding_readiness_filters_non_embedding_models_and_secret_fields(monkeypatch):
    monkeypatch.setenv("RAGFLOW_API_KEY", "PRIVATE_KEY")
    client = RagflowClient({"base_url": "https://rag.example"})
    rows = [
        {"name": "ocr", "model_type": ["ocr"]},
        {"name": "embed", "model_type": ["embedding"], "api_key": "PRIVATE_PROVIDER_KEY"},
        {"name": "disabled", "model_type": "embedding", "enable": False},
    ]
    client._request = lambda *args, **kwargs: rows
    assert client.embedding_models() == [{"name": "embed", "model_type": ["embedding"]}]


def test_missing_embedding_fails_before_creating_or_uploading(tmp_path):
    bundle = tmp_path / "bundle"
    prepare(config(), bundle, samples=[sample()])
    client = FakeRagflow()
    client.embedding_models = list
    with pytest.raises(RagflowError, match="embedding model"):
        index_bundle(bundle, {**config()["ragflow"], "check_embedding_models": True}, client=client)
    assert not client.uploads and not client.datasets


def test_cli_resolves_relative_manifest_and_prompt_paths(tmp_path, capsys):
    from scripts.dcc_preprocess import load_config, main

    (tmp_path / "samples.jsonl").write_text(json.dumps({"id": "x", "question": "Q", "documents": [{"id": "d", "text": "DOC"}]}))
    (tmp_path / "extra.md").write_text("${question}")
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"loader": "manifest", "loader_options": {"source": "samples.jsonl"}, "prompts": {"layers": ["extra.md"]}}))
    loaded = load_config(path)
    assert loaded["loader_options"]["source"] == str(tmp_path / "samples.jsonl")
    assert loaded["prompts"]["layers"] == [str(tmp_path / "extra.md")]
    assert main(["prepare", "--config", str(path), "--output", str(tmp_path / "bundle")]) == 0
    assert json.loads(capsys.readouterr().out)["samples"] == 1
