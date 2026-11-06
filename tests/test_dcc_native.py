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


@pytest.mark.parametrize(
    "name,mode,type_",
    [
        ("finre", "text", "string"),
        ("finmr", "json", "object"),
        ("finsm", "json", "array"),
    ],
)
def test_build_inputs_derives_shape_only_from_exact_public_finauditing_template(
    tmp_path, monkeypatch, name, mode, type_
):
    from dcc_harness.data import InputFile, PublicTask
    from dcc_harness.datasets.benchmark_plugins import BenchmarkPlugin, build_inputs

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "native"))
    public = PublicTask(
        "dcc",
        "Audit the supplied filing.",
        "",
        {},
        (
            InputFile(
                "rule0.txt",
                (ROOT / f"configs/dcc/prompts/finauditing/{name}.md").read_text(),
            ),
        ),
    )
    plugin = BenchmarkPlugin(
        name="dcc",
        prepare_task=lambda task: task.prompt,
        prepare_inputs=lambda task, thread: {},
        skills_path=tmp_path,
    )
    contract = build_inputs(plugin, public, "contract-thread")["sp_task_contract"]
    assert contract["output_parse_mode"] == mode
    assert contract["output_schema"]["type"] == type_
    assert "PRIVATE" not in json.dumps(contract)


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
            "configuration_sha256": digest(
                encoded(index_identity(manifest, config["ragflow"]))
            ),
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


def test_plugin_configures_native_tool_skill_and_official_evaluation_hooks(
    native_bundle,
):
    from deerflow.config.app_config import AppConfig

    from dcc_harness.datasets.benchmark_plugins import (
        configure_dataset_app,
        resolve_dataset_plugin,
    )

    plugin = resolve_dataset_plugin(
        "dcc", ("dcc_harness.preprocessing.native:make_plugin",)
    )
    app = configure_dataset_app(
        AppConfig.from_file(str(ROOT / "configs/stackplanner_benchmarks.yaml")), plugin
    )
    assert "ragflow_retrieve" in {tool.name for tool in app.tools}
    assert Path(app.skills.path) == ROOT / "configs/dcc/skills"
    assert callable(plugin.parse_answer) and callable(plugin.score_task)
    assert callable(plugin.aggregate) and callable(plugin.setup_evaluation)
    assert plugin.evaluation_resources and not plugin.requires_judge
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
        assert (
            not get_paths()
            .sandbox_uploads_dir("thread-one", user_id="dcc-bob")
            .exists()
        )
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


@pytest.mark.parametrize("user_channel", ["contextvar", "runtime", "conflicting"])
@pytest.mark.parametrize(("page", "page_size"), [(1, 10), (2, 100)])
def test_native_tool_uses_thread_scope_and_excludes_runtime_from_agent_schema(
    native_bundle, monkeypatch, user_channel, page, page_size
):
    from contextvars import Context

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
            return {
                "chunks": [{"doc_id": "doc", "content": "Evidence", "id": "chunk"}],
                "total": 1,
            }

    monkeypatch.setattr(native_tools, "RagflowClient", FakeClient)
    token = set_current_user(SimpleNamespace(id="dcc-alice"))
    try:
        prepare_inputs(task.public_input(), "thread-one")
        runtime = SimpleNamespace(context={"thread_id": "thread-one"}, config={})
        if user_channel != "contextvar":
            runtime.context["user_id"] = "dcc-alice"
        coroutine = native_tools.ragflow_retrieve.coroutine(
            question="fact", runtime=runtime, page=page, page_size=page_size
        )
        if user_channel == "runtime":
            result = Context().run(asyncio.run, coroutine)
        elif user_channel == "conflicting":
            other_token = set_current_user(SimpleNamespace(id="dcc-bob"))
            try:
                result = asyncio.run(coroutine)
            finally:
                reset_current_user(other_token)
        else:
            result = asyncio.run(coroutine)
        assert json.loads(result)["chunks"][0]["id"] == "chunk"
        # Externalized retrieval output is read with inclusive line ranges.
        # Keep each JSON field/chunk addressable without one giant result line.
        assert len(result.splitlines()) > 5
        assert max(map(len, result.splitlines())) < len(result)
        assert observed[0]["document_ids"] == ["doc"]
        assert observed[0]["dataset_ids"] == ["kb"]
        assert observed[0]["page"] == page
        assert observed[0]["page_size"] == page_size
        assert json.loads(result)["page"] == page
        schema = native_tools.ragflow_retrieve.tool_call_schema.model_json_schema()
        assert "runtime" not in schema["properties"]
        assert "dataset_ids" not in schema["properties"]
    finally:
        reset_current_user(token)


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        ({"document_keyword": "instance.xml"}, "instance.xml"),
        ({"docnm_kwd": "legacy.xml", "document_keyword": "instance.xml"}, "legacy.xml"),
        (
            {
                "document_name": "canonical.xml",
                "docnm_kwd": "legacy.xml",
                "document_keyword": "instance.xml",
            },
            "canonical.xml",
        ),
        (
            {
                "document_name": "",
                "docnm_kwd": None,
                "document_keyword": "instance.xml",
            },
            "instance.xml",
        ),
    ],
)
def test_native_retrieval_normalizes_filename_aliases_without_changing_xml(
    native_bundle, monkeypatch, names, expected
):
    from deerflow.runtime.user_context import reset_current_user, set_current_user

    from dcc_harness.preprocessing import native_tools

    _, task = native_bundle
    observed = []
    content = '<inst:Amount contextRef="ctx-1" unitRef="USD">123.45</inst:Amount>\n<period>2023</period>'

    class FakeClient:
        base_url = "https://rag.example"

        def __init__(self, config):
            self.session = SimpleNamespace(close=lambda: None)

        def retrieve(self, payload):
            observed.append(payload)
            return {
                "total": 7,
                "chunks": [
                    {
                        "id": "chunk",
                        "document_id": "doc",
                        "content": content,
                        "positions": [[1, 2]],
                        **names,
                    }
                ],
            }

    monkeypatch.setattr(native_tools, "RagflowClient", FakeClient)
    token = set_current_user(SimpleNamespace(id="dcc-alice"))
    try:
        prepare_inputs(task.public_input(), "thread-filename")
        result = json.loads(
            native_tools._retrieve_for_thread(
                "thread-filename", "amount", 2, 100, user_id="dcc-alice"
            )
        )
    finally:
        reset_current_user(token)
    assert result["total"] == 7 and result["page"] == 2
    assert len(result["chunks"]) == 1
    chunk = result["chunks"][0]
    assert chunk["document_name"] == expected
    assert chunk["id"] == "chunk" and chunk["document_id"] == "doc"
    assert chunk["content"] == content and chunk["positions"] == [[1, 2]]
    assert observed[0]["document_ids"] == ["doc"] and observed[0]["dataset_ids"] == [
        "kb"
    ]
    assert observed[0]["page"] == 2 and observed[0]["page_size"] == 100


def test_native_document_enumeration_is_scoped_complete_and_cached(
    native_bundle, monkeypatch
):
    from deerflow.runtime.user_context import get_effective_user_id

    from dcc_harness.preprocessing import native_tools

    _, task = native_bundle
    prepare_inputs(task.public_input(), "document-thread")
    observed = []

    class Client:
        def __init__(self, config):
            self.session = SimpleNamespace(close=lambda: None)

        def document_chunks(self, dataset, document, page, page_size):
            observed.append((dataset, document, page, page_size))
            return {
                "total": 1,
                "doc": {"size": 17, "update_time": 123},
                "chunks": [
                    {"id": "c", "document_id": "doc", "content": "<fact>100</fact>"}
                ],
            }

    monkeypatch.setattr(native_tools, "RagflowClient", Client)
    for _ in range(2):
        value = json.loads(
            native_tools._retrieve_for_thread(
                "document-thread",
                "read source",
                1,
                100,
                user_id=get_effective_user_id(),
                document_id="doc",
            )
        )
        assert value["chunks"][0]["content"] == "<fact>100</fact>"
        assert value["source_coverage"]["mode"] == "document"
    assert observed == [("kb", "doc", 1, 100)]
    with pytest.raises(ValueError, match="scope"):
        native_tools._retrieve_for_thread(
            "document-thread",
            "read",
            1,
            100,
            user_id=get_effective_user_id(),
            document_id="other",
        )


@pytest.mark.parametrize(
    ("total", "page", "returned", "total_pages", "next_page", "out_of_range"),
    [
        (10, 1, 10, 1, None, False),
        (7, 1, 7, 1, None, False),
        *((total, page, 0, 1, None, True) for total in (10, 7) for page in (2, 3, 4)),
        (210, 1, 100, 3, 2, False),
        (210, 2, 100, 3, 3, False),
        (210, 3, 10, 3, None, False),
        (0, 1, 0, 0, None, True),
    ],
)
def test_native_document_pagination_is_transport_only_and_preserves_source_proof(
    native_bundle,
    monkeypatch,
    total,
    page,
    returned,
    total_pages,
    next_page,
    out_of_range,
):
    from deerflow.runtime.user_context import get_effective_user_id
    from deerflow.sp.source_access import (
        collect_source_access,
        public_audit_completion_gaps,
        source_access_gaps,
    )

    from dcc_harness.preprocessing import native_tools

    _, task = native_bundle
    prepare_inputs(task.public_input(), "pagination-thread")
    chunks = [
        {
            "id": f"chunk-{(page - 1) * 100 + i}",
            "document_id": "doc",
            "content": f'<fact id="{i}">Exact original XML quotation.\nSecond line.</fact>',
        }
        for i in range(returned)
    ]
    observed = []

    class Client:
        def __init__(self, config):
            self.session = SimpleNamespace(close=lambda: None)

        def document_chunks(self, dataset, document, page, page_size):
            observed.append((dataset, document, page, page_size))
            return {
                "total": total,
                "chunks": chunks,
                "doc": {"size": 9000, "update_time": 123},
            }

    monkeypatch.setattr(native_tools, "RagflowClient", Client)

    def call():
        return native_tools._retrieve_for_thread(
            "pagination-thread",
            "Read original source",
            page,
            100,
            user_id=get_effective_user_id(),
            document_id="doc",
        )

    rendered = call()
    value = json.loads(rendered)
    assert value["total"] == total and value["page"] == page
    assert value["chunks"] == chunks
    assert [chunk["content"].encode() for chunk in value["chunks"]] == [
        chunk["content"].encode() for chunk in chunks
    ]
    assert value["pagination"] == {
        "total_chunks": total,
        "returned_chunks": returned,
        "page": page,
        "page_size": 100,
        "total_pages": total_pages,
        "has_more": next_page is not None,
        "next_page": next_page,
        "end_of_indexed_document": next_page is None,
        "out_of_range": out_of_range,
    }
    assert rendered.index('"pagination"') < rendered.index('"chunks"')
    coverage = value["source_coverage"]
    assert coverage == {
        "mode": "document",
        "document_id": "doc",
        "page": page,
        "page_size": 100,
        "total": total,
        "returned": returned,
        "chunk_ids": [chunk["id"] for chunk in chunks],
        "document_bytes": 9000,
        "snapshot_update_time": 123,
    }
    # Cached snapshots get the same transport facts without another service call.
    assert json.loads(call()) == value
    assert observed == [("kb", "doc", page, 100)]
    # Legacy cached wrappers gain only transient pagination; keep their original
    # snapshot bytes and source proof untouched, without a new service request.
    from deerflow.config.paths import get_paths

    uploads = get_paths().sandbox_uploads_dir(
        "pagination-thread", user_id=get_effective_user_id()
    )
    cache = uploads.parent.parent / ".dcc-document-cache" / f"doc-{page}-100.json"
    legacy = {key: item for key, item in value.items() if key != "pagination"}
    cache.write_text(json.dumps(legacy, ensure_ascii=False))
    original_cache_bytes = cache.read_bytes()
    assert json.loads(call()) == value
    assert cache.read_bytes() == original_cache_bytes
    assert observed == [("kb", "doc", page, 100)]
    messages = [
        {
            "type": "ai",
            "tool_calls": [
                {
                    "id": "read",
                    "name": "ragflow_retrieve",
                    "args": {"document_id": "doc", "page": page, "page_size": 100},
                }
            ],
        },
        {
            "type": "tool",
            "name": "ragflow_retrieve",
            "tool_call_id": "read",
            "content": rendered,
        },
    ]
    state = {
        "sp_loop_run_id": "run",
        "sp_task_contract": {
            "source_requirements": {
                "audit_agent": "researcher",
                "files": [],
                "document_ids": ["doc"],
            }
        },
        "sp_source_access": {"run_id": "run", **collect_source_access(messages)},
    }
    # EOF on an out-of-range empty page does not certify unseen earlier chunks.
    assert bool(source_access_gaps(state)) is (total == 0 or returned != total)
    assert public_audit_completion_gaps(state)


def test_clause_source_contract_requires_full_document_and_preserves_audit_role():
    from dcc_harness.data import InputFile, PublicTask
    from dcc_harness.preprocessing.output_contracts import public_output_contract

    public = PublicTask(
        "clause",
        "Audit the entire document.",
        files=(InputFile("document.txt", "first\nsecond\nlast\n"),),
    )
    contract = public_output_contract(public)
    assert contract["delivery_mode"] == "artifact"
    assert contract["source_requirements"]["audit_agent"] == "reporter"
    assert contract["source_requirements"]["skill_names"] == ["clause"]
    assert contract["source_requirements"]["files"] == [
        {"path": "/mnt/user-data/uploads/document.txt", "total_lines": 3}
    ]
    assert contract["output_file_extension"] == ".json"
    from deerflow.sp.output_contract import output_contract_gaps

    for text in ["[]", '[{"text":"Exact sentence.","explanation":""}]']:
        assert output_contract_gaps({"sp_task_contract": contract}, text) == []
    for text in [
        '[{"text":"  ","explanation":"reason"}]',
        '[{"text":"sentence","explanation":"reason","extra":1}]',
        '{"summary":"Done"}',
    ]:
        assert output_contract_gaps({"sp_task_contract": contract}, text)


def test_public_nba_template_requires_complete_uploaded_rules_without_private_routing():
    from string import Template

    from dcc_harness.data import InputFile, PublicTask
    from dcc_harness.preprocessing.output_contracts import public_output_contract

    template = (ROOT / "configs/dcc/prompts/rulearena-nba/task.md").read_text()
    prompt = Template(template).substitute(
        rules="Read dcc-rule-0.txt", document_text="A public transaction scenario."
    )
    contract = public_output_contract(
        PublicTask(
            "dcc_inline",
            prompt,
            files=(InputFile("dcc-rule-0.txt", "first\nsecond\n"),),
        )
    )
    assert contract["source_requirements"] == {
        "audit_agent": "researcher",
        "files": [{"path": "/mnt/user-data/uploads/dcc-rule-0.txt", "total_lines": 2}],
        "document_ids": [],
    }
    assert contract["output_parse_mode"] == "text"
    assert contract["output_schema"]["type"] == "string"
    from deerflow.sp.output_contract import output_contract_gaps

    for text in [
        "Analysis of every operation.\nAnswer: False.",
        "The completed public audit.\nAnswer: True. Illegal Operation: A. Problematic Team: B.",
    ]:
        assert output_contract_gaps({"sp_task_contract": contract}, text) == []
    for text in [
        "Detailed analysis remains required.",
        "Answer: True. Illegal Operation: AA. Problematic Team: B.",
        '{"summary":"Done","artifact_content":"Answer: False."}',
    ]:
        assert output_contract_gaps({"sp_task_contract": contract}, text)
    assert contract["delivery_mode"] == "inline"
    assert (
        public_output_contract(
            PublicTask(
                "dcc_inline",
                "An unrelated question.",
                metadata={"dataset": "rulearena_nba"},
            )
        )
        == {}
    )


@pytest.mark.parametrize("subtask", ["finre", "finsm", "finmr"])
def test_published_filing_tasks_keep_inline_delivery_regardless_of_transport_guidance(
    subtask,
):
    from dcc_harness.data import PublicTask
    from dcc_harness.preprocessing.output_contracts import public_output_contract

    prompt = (ROOT / f"configs/dcc/prompts/finauditing/{subtask}.md").read_text()
    public = PublicTask(
        "dcc",
        "For a requested file, first delegate its creation and verification.\n"
        + prompt,
    )
    contract = public_output_contract(public)
    assert contract["delivery_mode"] == "inline"
    assert contract["source_requirements"]["audit_agent"] == "researcher"
    assert "output_schema" in contract


def test_v3_public_input_builder_stages_neutral_files_without_private_identifiers(
    native_bundle,
):
    from deerflow.config.paths import get_paths
    from deerflow.runtime.user_context import get_effective_user_id

    from dcc_harness.datasets.benchmark_plugins import (
        build_inputs,
        resolve_dataset_plugin,
    )

    bundle, task = native_bundle
    public = task.public_input()
    assert (
        not hasattr(public, "id")
        and not hasattr(public, "gold")
        and not hasattr(public, "split")
    )
    assert "dcc" not in public.metadata
    plugin = resolve_dataset_plugin(
        "dcc", ("dcc_harness.preprocessing.native:make_plugin",)
    )
    inputs = build_inputs(plugin, public, "public-contract")
    root = get_paths().sandbox_uploads_dir(
        "public-contract", user_id=get_effective_user_id()
    )
    context = json.loads((root / "dcc-context.json").read_text())
    assert (
        "sample_id" not in context
        and "dataset" not in context
        and "split" not in context
    )
    assert "PRIVATE_" not in repr(inputs) and str(bundle) not in repr(inputs)
    assert "Sample: one" not in inputs["messages"][0].content
    assert "Split: test" not in inputs["messages"][0].content
    assert inputs["sp_task_contract"]["authoritative"] is True
    assert {item["name"] for item in inputs["uploaded_files"]} == {
        "dcc-context.json",
        "dcc-rule-0.txt",
    }


def test_public_rule_file_integrity_is_checked_before_staging(native_bundle):
    import dataclasses

    from dcc_harness.data import InputFile

    _, task = native_bundle
    public = task.public_input()
    altered = dataclasses.replace(
        public,
        files=tuple(
            InputFile(item.name, "changed") if item.name == "dcc-rule-0.txt" else item
            for item in public.files
        ),
    )
    with pytest.raises(ValueError, match="rule content changed"):
        prepare_inputs(altered, "tampered-rule")
