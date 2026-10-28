from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

from dcc_harness.data import Task
from dcc_harness.datasets.benchmarks import prepare_task
from dcc_harness.evaluation.benchmarks import (
    DeliveryError,
    extract_delivery,
    parse_answer,
    score_task,
    summarize,
)


def task(**overrides):
    return Task(
        **dict(
            id="icmp-en",
            benchmark="widesearch",
            split="validation",
            prompt="List every matching entry.",
            gold=[{"name": "PRIVATE_GOLD"}],
            metadata={
                "answer_columns": ["name"],
                "private": "PRIVATE_META",
                "evaluation": {
                    "required": ["name"],
                    "unique_columns": ["name"],
                    "eval_pipeline": {"name": {"metric": ["exact_match"]}},
                },
            },
            **overrides,
        )
    )


def finished(**values):
    return {
        "sp_current_stage": "finished",
        "sp_last_handler_result": {"next_step": "finish"},
        "sp_last_run_summary": '[{"name":"delivered"}]',
        **values,
    }


def artifact_state(tmp_path, content):
    (tmp_path / "answer.json").write_text(content, encoding="utf-8")
    ref = {
        "artifact_id": "spart_chosen",
        "thread_id": "thread-1",
        "run_id": "run-1",
        "is_current": True,
        "virtual_path": "/mnt/user-data/outputs/answer.json",
        "type": "generated_file",
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        "metadata": {"completion_status": "complete"},
    }
    return finished(
        thread_data={"outputs_path": str(tmp_path)},
        sp_last_final_artifact_ref="spart_chosen",
        sp_current_artifact_refs={"generated_file": ref},
    ), ref


def test_public_input_excludes_gold_private_metadata_and_does_not_choose_roles():
    prompt = prepare_task(task())
    assert "List every matching" in prompt and "name" in prompt
    assert "PRIVATE_" not in prompt
    assert all(word not in prompt for word in ("researcher", "coder", "sp_delegate"))


def test_cuad_input_uses_native_uploaded_contract():
    sample = task()
    sample = dataclasses.replace(
        sample, benchmark="cuad", document="PUBLIC CONTRACT", gold=["SECRET"]
    )
    prompt = prepare_task(sample)
    assert "/mnt/user-data/uploads/contract.txt" in prompt
    assert "SECRET" not in prompt and "PUBLIC CONTRACT" not in prompt


def test_only_native_accepted_finish_can_be_scored():
    assert extract_delivery(finished(), thread_id="thread-1", run_id="run-1")[
        0
    ].startswith("[")
    with pytest.raises(DeliveryError, match="unfinished"):
        extract_delivery(
            {"sp_last_run_summary": "looks good"}, thread_id="thread-1", run_id="run-1"
        )
    with pytest.raises(DeliveryError, match="ask_human"):
        extract_delivery(
            finished(sp_pending_human_interaction={"question": "help"}),
            thread_id="thread-1",
            run_id="run-1",
        )


def test_reads_entire_selected_native_artifact_never_summary_or_last_specialist(
    tmp_path,
):
    content = json.dumps([{"name": "entry" * 10000}])
    state, ref = artifact_state(tmp_path, content)
    text, selected = extract_delivery(state, thread_id="thread-1", run_id="run-1")
    assert text == content and selected == ref


def test_native_existing_file_without_hash_records_snapshot_and_checks_preview(
    tmp_path,
):
    state, ref = artifact_state(tmp_path, "[]")
    ref["content_hash"] = ""
    ref["metadata"].update(
        registered_existing_file=True,
        finalization_preview="[]",
        finalization_preview_truncated=False,
    )
    text, selected = extract_delivery(state, thread_id="thread-1", run_id="run-1")
    assert (
        text == "[]"
        and selected["observed_sha256"] == hashlib.sha256(b"[]").hexdigest()
    )
    (tmp_path / "answer.json").write_text('["changed"]', encoding="utf-8")
    with pytest.raises(DeliveryError):
        extract_delivery(state, thread_id="thread-1", run_id="run-1")


@pytest.mark.parametrize(
    "damage", ["thread", "run", "partial", "hash", "traversal", "unselected"]
)
def test_rejects_stale_partial_corrupt_and_unsafe_delivery(tmp_path, damage):
    state, ref = artifact_state(tmp_path, "[]")
    if damage == "thread":
        ref["thread_id"] = "someone-else"
    if damage == "run":
        ref["run_id"] = "earlier-run"
    if damage == "partial":
        ref["metadata"]["completion_status"] = "partial"
    if damage == "hash":
        ref["content_hash"] = "wrong"
    if damage == "traversal":
        ref["virtual_path"] = "/mnt/user-data/outputs/../answer.json"
    if damage == "unselected":
        state["sp_last_final_artifact_ref"] = "not-registered"
    with pytest.raises(DeliveryError):
        extract_delivery(state, thread_id="thread-1", run_id="run-1")


def test_widesearch_native_markdown_and_existing_scorer():
    sample = dataclasses.replace(task(), gold=[{"name": "A"}])
    text = "| name |\n| --- |\n| A |"
    assert parse_answer(sample, text) == [{"name": "A"}]
    assert score_task(sample, text)["f1_by_row"] == 1
    assert score_task(sample, text)["evaluation_protocol"] == "strict_exact_proxy"


def test_cuad_rejects_malformed_entries_instead_of_silently_dropping_them():
    sample = dataclasses.replace(task(), benchmark="cuad", document="A", gold=["A"])
    assert parse_answer(sample, '```json\n["A"]\n```') == ["A"]
    assert parse_answer(sample, "[]") == []
    for text in ("[42]", '[{"text":"A","confidence":2}]', "explanatory prose"):
        with pytest.raises(ValueError):
            parse_answer(sample, text)


def test_summary_keeps_execution_failure_denominator_and_judge_failure_separate():
    rows = [
        {
            "benchmark": "widesearch",
            "status": "completed",
            "metrics": {"f1_by_row": 1.0, "f1_by_item": 0.5},
        },
        {"benchmark": "widesearch", "status": "unfinished", "metrics": {}},
        {"benchmark": "widesearch", "status": "judge_error", "metrics": {}},
    ]
    result = summarize(rows, expected_tasks=3)
    assert result["completion_rate"] == pytest.approx(2 / 3)
    assert result["widesearch"]["judge_failures"] == 1
    assert result["widesearch"]["metric_denominator"] == 2
    assert result["widesearch"]["f1_by_row"] == 0.5


@pytest.mark.parametrize("status", ["completed", "ask_human", "unfinished"])
def test_runner_passes_only_public_input_to_native_factory(tmp_path, monkeypatch, status):
    import deerflow.sp.runtime as native

    from dcc_harness.evaluation.benchmark_runner import (
        Settings,
        run_task,
    )

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "native"))
    captured = {}

    class Graph:
        async def astream(self, inputs, **kwargs):
            captured["inputs"] = inputs
            state = finished(sp_loop_iteration=7, sp_decision_attempts=0)
            if status == "ask_human":
                state["sp_pending_human_interaction"] = {"question": "help"}
            elif status == "unfinished":
                state["sp_current_stage"] = "research"
                state["sp_last_handler_result"] = {"next_step": "continue"}
            yield "values", state

    def build(config, *, app_config):
        captured["config"] = config
        assert "executor_provider" not in config
        return Graph()

    monkeypatch.setattr(native, "make_sp_agent", build)
    sample = dataclasses.replace(task(), gold=[{"name": "delivered"}])
    settings = Settings(
        config="configs/stackplanner_benchmarks.yaml",
        base_url="http://local/v1",
        model="local",
    )
    result = asyncio.run(
        run_task(
            sample,
            settings,
            output_dir=tmp_path / "attempt",
        )
    )
    assert result["status"] == status, result
    assert result["native_iterations"] == 7
    assert json.loads((tmp_path / "attempt" / "result.json").read_text())["native_iterations"] == 7
    assert "sp_training_model_factory" not in captured["config"]["configurable"]
    assert captured["config"]["configurable"]["sp_max_actions"] == settings.max_iterations
    assert captured["config"]["context"]["user_id"] == result["user_id"]
    assert "PRIVATE_" not in str(captured["inputs"])
    assert (tmp_path / "attempt" / "trace.jsonl").is_file()


def test_native_registered_tools_match_config_and_keep_file_capability():
    from deerflow.config.app_config import AppConfig
    from deerflow.tools import get_available_tools

    from dcc_harness.datasets.benchmark_plugins import (
        configure_dataset_app,
        resolve_dataset_plugin,
    )

    app = configure_dataset_app(AppConfig.from_file("configs/stackplanner_benchmarks.yaml"), resolve_dataset_plugin("widesearch"))
    tools = {
        tool.name: tool
        for tool in get_available_tools(app_config=app, include_mcp=False)
    }
    assert {
        "web_search",
        "web_fetch",
        "read_file",
        "write_file",
        "grep",
        "ls",
        "glob",
    } <= tools.keys()
    assert "bash" in tools and app.sandbox.allow_host_bash


def test_original_chinese_delegation_retains_native_role_routing():
    from deerflow.sp.agent_tools import _normalize_delegate_target

    payload = {
        "target_agent": "coder",
        "task": "请根据已有成果整理完整表格并 report the row count.",
        "expected_output": "A generated .json file containing all rows.",
    }
    role, reason = _normalize_delegate_target(payload)
    assert role == "reporter" and reason == "pure_report_synthesis"
    # The adapter reports this native decision; it must not rewrite the task
    # with misleading code/API keywords to force a different role.


def test_real_native_executor_can_read_contract_and_write_final_file(
    tmp_path, monkeypatch
):
    import deerflow.agents.lead_agent.agent as lead
    import deerflow.subagents.executor as specialists
    from langchain_core.language_models.fake_chat_models import (
        FakeMessagesListChatModel,
    )
    from langchain_core.messages import AIMessage

    from dcc_harness.evaluation.benchmark_runner import (
        Settings,
        run_task,
    )

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "native"))
    native_roles = []

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    text = '["California law"]'
    coder = Model(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "id": "read-1",
                        "args": {
                            "description": "Read uploaded contract",
                            "path": "/mnt/user-data/uploads/contract.txt",
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "id": "write-1",
                        "args": {
                            "description": "Save extracted spans",
                            "path": "/mnt/user-data/outputs/answer.json",
                            "content": text,
                        },
                    }
                ],
            ),
            AIMessage(
                content=json.dumps(
                    {
                        "summary": "Saved extracted spans.",
                        "artifact_content": text,
                        "artifact_type": "generated_file",
                        "artifact_metadata": {
                            "completion_status": "complete",
                            "filename": "answer.json",
                        },
                    }
                )
            ),
        ]
    )

    class Central(Model):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            import re

            from langchain_core.outputs import ChatGeneration, ChatResult

            if self.i == 0:
                self.i = 1
                name, args = (
                    "sp_delegate",
                    {
                        "target_agent": "reporter",
                        "stage": "reporting",
                        "task": "Read the uploaded contract and deliver its verbatim governing law spans as a JSON file.",
                        "expected_output": "A JSON array of extracted spans saved under outputs.",
                    },
                )
            else:
                joined = str(messages)
                refs = re.findall(r"spart_[a-z0-9]+", joined)
                assert refs, joined[-3000:]
                name, args = (
                    "sp_finish",
                    {
                        "summary": "Deliver the extracted spans.",
                        "final_artifact_ref": refs[-1],
                    },
                )
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": name,
                                    "args": args,
                                    "id": f"central-{self.i}-{len(messages)}",
                                }
                            ],
                        )
                    )
                ]
            )

    central = Central(responses=[AIMessage(content="unused")])

    def factory(*, role, **kwargs):
        native_roles.append(role)
        return central if role == "central" else coder

    monkeypatch.setattr(lead, "create_chat_model", lambda **kwargs: factory(role="central", **kwargs))
    monkeypatch.setattr(specialists, "create_chat_model", lambda **kwargs: factory(role="reporter", **kwargs))
    sample = Task(
        id="contract-law",
        benchmark="cuad",
        split="validation",
        prompt="What is the governing law?",
        document="This contract is governed by California law.",
        gold=["California law"],
    )
    result = asyncio.run(
        run_task(
            sample,
            Settings(
                config="configs/stackplanner_benchmarks.yaml",
                base_url="http://unused/v1",
                model="scripted",
            ),
            output_dir=tmp_path / "attempt",
        )
    )
    assert result["status"] == "completed", (
        result,
        (tmp_path / "attempt" / "state.json").read_text(encoding="utf-8")[-6000:],
    )
    assert result["answer"] == ["California law"] and result["metrics"]["f1"] == 1
    assert native_roles == ["central", "reporter"]
    state = json.loads(
        (tmp_path / "attempt" / "state.json").read_text(encoding="utf-8")
    )
    saved = Path(state["thread_data"]["outputs_path"]) / "answer.json"
    assert saved.read_text(encoding="utf-8") == text
    events = [json.loads(line)["data"] for line in (tmp_path / "attempt" / "trace.jsonl").read_text(encoding="utf-8").splitlines() if json.loads(line)["kind"] == "custom"]
    file_messages = [event.get("message", {}) for event in events if event.get("message", {}).get("name") in {"read_file", "write_file"}]
    assert file_messages and all(message.get("additional_kwargs", {}).get("deerflow_tool_meta", {}).get("status") != "error" for message in file_messages), file_messages
    assert (
        result["tool_events"] == []
    )  # Native file task has no mandatory Web-search gate.


def test_manifest_rejects_legacy_and_changed_identity(tmp_path):
    from scripts.evaluate_sp_benchmarks import ensure_manifest

    ensure_manifest(
        tmp_path, {"protocol": "sp-native-benchmarks-v1", "data": "a"}, resume=False
    )
    ensure_manifest(
        tmp_path, {"protocol": "sp-native-benchmarks-v1", "data": "a"}, resume=True
    )
    for identity in (
        {"protocol": "sp2-dcc-v5", "data": "a"},
        {"protocol": "sp-native-benchmarks-v1", "data": "b"},
    ):
        with pytest.raises(ValueError):
            ensure_manifest(tmp_path, identity, resume=True)
    with pytest.raises(ValueError):
        ensure_manifest(tmp_path, {"data": "a"}, resume=False)


def test_manifest_does_not_adopt_old_prediction_directory(tmp_path):
    from scripts.evaluate_sp_benchmarks import ensure_manifest

    (tmp_path / "predictions.jsonl").write_text("old records", encoding="utf-8")
    with pytest.raises(ValueError):
        ensure_manifest(tmp_path, {"protocol": "sp-native-benchmarks-v1"}, resume=False)


def test_cli_persists_identity_results_and_resumes_without_exposing_key(
    tmp_path, monkeypatch
):
    from scripts import evaluate_sp_benchmarks as cli

    monkeypatch.setenv("DCC_MODEL_API_KEY", "SENTINEL_SECRET_MUST_NOT_BE_RECORDED")
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "home"))
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(json.dumps(dataclasses.asdict(task())) + "\n", encoding="utf-8")
    args = cli.parser().parse_args(
        [
            "--tasks",
            str(tasks),
            "--run-dir",
            str(tmp_path / "run"),
            "--dataset-source",
            "independent-curriculum",
            "--base-url",
            "http://unused/v1",
            "--model",
            "scripted",
            "--exact-proxy",
        ]
    )
    called = []

    async def fake_run(sample, settings, **kwargs):
        called.append(sample.id)
        return {
            "task_id": sample.id,
            "protocol": cli.PROTOCOL,
            "benchmark": "widesearch",
            "status": "completed",
            "answer": [{"name": "x"}],
            "metrics": {"f1_by_row": 1.0, "f1_by_item": 1.0},
        }

    monkeypatch.setattr(cli, "run_task", fake_run)
    assert asyncio.run(cli.main_async(args)) == 0
    (tmp_path / "run" / "report.json").write_text("{}", encoding="utf-8")
    args.resume = True
    assert asyncio.run(cli.main_async(args)) == 0
    assert called == ["icmp-en"]
    manifest = (tmp_path / "run" / "manifest.json").read_text(encoding="utf-8")
    assert "SENTINEL_SECRET" not in manifest
    report = json.loads((tmp_path / "run" / "report.json").read_text(encoding="utf-8"))
    assert (
        report["prediction_coverage"] == 1
        and report["dataset_source"] == "independent-curriculum"
    )


@pytest.mark.parametrize("page_count", [1, 6])
@pytest.mark.filterwarnings("error::UserWarning")
def test_real_native_researcher_uses_registered_web_tools(tmp_path, monkeypatch, page_count):
    import deerflow.agents.lead_agent.agent as lead
    import deerflow.subagents.executor as specialists
    from langchain_core.language_models.fake_chat_models import (
        FakeMessagesListChatModel,
    )
    from langchain_core.messages import AIMessage

    from dcc_harness.evaluation.benchmark_runner import (
        Settings,
        run_task,
    )
    from dcc_harness.tools import WebTools

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "native"))
    calls = []

    async def search(self, **kwargs):
        calls.append("search")
        return {
            "results": [{"url": "https://example.org", "title": "Names"}],
            "backend": "fixture",
        }

    async def fetch(self, **kwargs):
        calls.append("fetch")
        start = kwargs["start"]
        assert kwargs["url"] == "https://example.org"
        return {
            "url": "https://example.org",
            "text": "name: A",
            "start": start,
            "end": start + 7,
            "total_chars": page_count * 7,
            "next_start": start + 7 if start + 7 < page_count * 7 else None,
        }

    monkeypatch.setattr(WebTools, "web_search", search)
    monkeypatch.setattr(WebTools, "fetch_url", fetch)

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    table = "| name |\n| --- |\n| A |"
    central = Model(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "sp_delegate",
                        "id": "d1",
                        "args": {
                            "target_agent": "researcher",
                            "task": "Search and read the public source for matching names.",
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "sp_finish",
                        "id": "f1",
                        "args": {"summary": table, "allow_without_artifact": True},
                    }
                ],
            ),
        ]
    )
    researcher = Model(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "web_search", "id": "s1", "args": {"query": "names"}}
                ],
            ),
            *[AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "web_fetch",
                        "id": f"r{index}",
                        "args": {"url": "https://example.org" + (f"#sp-read-start={index * 7}" if index else "")},
                    }
                ],
            ) for index in range(page_count)],
            AIMessage(
                content=json.dumps(
                    {
                        "summary": "Read source: A. https://example.org",
                        "artifact_content": table + "\nSource: https://example.org",
                        "artifact_type": "data_collection",
                        "artifact_metadata": {"completion_status": "complete"},
                    }
                )
            ),
        ]
    )

    def factory(*, role, **kwargs):
        return central if role == "central" else researcher

    monkeypatch.setattr(lead, "create_chat_model", lambda **kwargs: factory(role="central", **kwargs))
    monkeypatch.setattr(specialists, "create_chat_model", lambda **kwargs: factory(role="researcher", **kwargs))
    sample = dataclasses.replace(task(), gold=[{"name": "A"}])
    result = asyncio.run(
        run_task(
            sample,
            Settings(
                config="configs/stackplanner_benchmarks.yaml",
                base_url="http://unused",
                model="scripted",
            ),
            output_dir=tmp_path / "attempt",
        )
    )
    assert result["status"] == "completed", result
    assert calls == ["search"] + ["fetch"] * page_count
    assert [e["tool"] for e in result["tool_events"]] == ["web_search"] + ["web_fetch"] * page_count
    assert result["metrics"]["f1_by_row"] == 1


def test_web_fetch_continuation_urls_keep_native_loop_protection(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from deerflow.agents.middlewares.loop_detection_middleware import (
        LoopDetectionMiddleware,
    )
    from langchain_core.messages import AIMessage
    from langgraph.runtime import Runtime

    from dcc_harness.evaluation import benchmark_tools
    from dcc_harness.tools import WebTools

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "native"))
    source = "https://example.org/data?format=json&page=1"
    client = WebTools(max_text_window_chars=4)
    client.pages[source] = "aaaabbbbccccddddeeeeffff"
    monkeypatch.setattr(benchmark_tools, "_client", lambda _: client)
    tool_runtime = SimpleNamespace(context={"thread_id": "paging"}, config={})
    guard_runtime = Runtime(context={"thread_id": "paging", "run_id": "paging"})
    guard = LoopDetectionMiddleware()
    url = source
    observed = []

    async def read_pages():
        nonlocal url
        for index in range(6):
            message = AIMessage(content="", tool_calls=[{
                "name": "web_fetch", "args": {"url": url}, "id": f"read-{index}",
            }])
            warning, stopped = guard._track_and_check({"messages": [message]}, guard_runtime)
            assert warning is None and not stopped
            result = json.loads(await benchmark_tools.web_fetch.coroutine(url=url, runtime=tool_runtime))
            observed.append(result["text"])
            assert result["url"] == source
            url = result["next_url"]
        assert url is None

    asyncio.run(read_pages())
    assert observed == ["aaaa", "bbbb", "cccc", "dddd", "eeee", "ffff"]
    repeated = AIMessage(content="", tool_calls=[{
        "name": "web_fetch", "args": {"url": source + "#sp-read-start=4"}, "id": "repeat",
    }])
    outcomes = [guard._track_and_check({"messages": [repeated]}, guard_runtime) for _ in range(4)]
    assert outcomes[-1][1] is True


@pytest.mark.parametrize("text", [
    'Source: registry.\n```json\n[{"name":"A"}]\n```\nEnd.',
    '```JSON\n[{"name":"A"}]\n```',
])
def test_widesearch_fenced_json_delivery_uses_the_same_scorer(text):
    sample = dataclasses.replace(task(), gold=[{"name": "A"}])
    assert parse_answer(sample, text) == [{"name": "A"}]
    assert score_task(sample, text)["f1_by_row"] == 1


@pytest.mark.parametrize("fragment,expected,error", [
    ("section", "abcd", False),
    ("sp-read-start=4", "efgh", False),
    ("sp-read-start=-1", None, True),
    ("sp-read-start=no", None, True),
    ("sp-read-start=9999999999999999999", None, True),
    ("sp-read-start=50", None, True),
])
def test_web_fetch_cursor_keeps_source_query_and_reports_invalid_reads(
    tmp_path, monkeypatch, fragment, expected, error
):
    from types import SimpleNamespace

    from dcc_harness.evaluation import benchmark_tools
    from dcc_harness.tools import WebTools

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "native"))
    source = "https://example.org/data?start=server-value&page=2"
    client = WebTools(max_text_window_chars=4)
    client.pages[source] = "abcdefgh"
    monkeypatch.setattr(benchmark_tools, "_client", lambda _: client)
    runtime = SimpleNamespace(context={"thread_id": "cursor"}, config={})
    result = json.loads(asyncio.run(benchmark_tools.web_fetch.coroutine(
        url=source + "#" + fragment, runtime=runtime
    )))
    assert bool(result.get("error")) is error
    if not error:
        assert result["url"] == source
        assert result["text"] == expected
    else:
        assert "text" not in result


def test_web_fetch_navigation_and_repeat_hints_use_read_urls(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from dcc_harness.evaluation import benchmark_tools
    from dcc_harness.tools import WebTools

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "native"))
    source = "https://example.org/archive.zip?version=2"
    client = WebTools(max_text_window_chars=4)
    client.pages[source] = "aaaabbbbcccc"
    client.page_worksheets[source] = [{"name": "Sheet1", "start": 4, "end": 8}]
    client.page_archives[source] = [{
        "name": "part.csv", "start": 8, "end": 12,
        "content_start": 8, "content_end": 12, "readable": True,
    }]
    monkeypatch.setattr(benchmark_tools, "_client", lambda _: client)
    runtime = SimpleNamespace(context={"thread_id": "navigation"}, config={})

    async def read():
        await benchmark_tools.web_fetch.coroutine(url=source, runtime=runtime)
        result = json.loads(await benchmark_tools.web_fetch.coroutine(url=source, runtime=runtime))
        assert result["worksheets"][0]["read_url"] == source + "#sp-read-start=4"
        assert result["archive_members"][0]["read_url"] == source + "#sp-read-start=8"
        for key in ("worksheets_note", "archive_members_note", "read_progress_note"):
            assert "next_start" not in result[key]
            assert "start set" not in result[key]
        part = json.loads(await benchmark_tools.web_fetch.coroutine(
            url=result["archive_members"][0]["read_url"], runtime=runtime
        ))
        assert part["text"] == "cccc"

    asyncio.run(read())


@pytest.mark.parametrize("threshold", [12000, 6000])
@pytest.mark.parametrize("navigation", [False, True])
def test_web_fetch_windows_remain_readable_through_native_output_budget(tmp_path, monkeypatch, threshold, navigation):
    from types import SimpleNamespace

    from deerflow.agents.middlewares.loop_detection_middleware import (
        LoopDetectionMiddleware,
    )
    from deerflow.agents.middlewares.tool_output_budget_middleware import (
        _budget_content,
    )
    from deerflow.config.app_config import AppConfig
    from deerflow.config.tool_output_config import ToolOutputConfig
    from langchain_core.messages import AIMessage
    from langgraph.runtime import Runtime

    from dcc_harness.evaluation import benchmark_tools
    from dcc_harness.tools import WebTools

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "native"))
    config = ToolOutputConfig(tool_overrides={"web_fetch": threshold})
    source = "https://example.org/data?format=json&page=1"
    body = ('{"name":"鸟类\\record","value":"quoted\\\"text"}\n' * 900)
    client = WebTools()
    client.pages[source] = body
    client.page_links[source] = [{"url": "https://example.org/help", "title": "Source documentation"}]
    if navigation:
        client.page_worksheets[source] = [{"name": f"Sheet{i}", "start": i * 1000, "end": (i + 1) * 1000} for i in range(5)]
        client.page_archives[source] = [{"name": f"part{i}.csv", "start": i * 1000, "end": (i + 1) * 1000} for i in range(5)]
    monkeypatch.setattr(benchmark_tools, "_client", lambda _: client)
    app = AppConfig.from_file("configs/stackplanner_benchmarks.yaml")
    app.tool_output = config
    runtime = SimpleNamespace(context={"thread_id": "budget", "app_config": app}, config={})
    guard = LoopDetectionMiddleware(tool_freq_warn=100, tool_freq_hard_limit=200)
    guard_runtime = Runtime(context={"thread_id": "budget", "run_id": "budget"})

    async def read():
        url = source
        pieces = []
        while url is not None:
            message = AIMessage(content="", tool_calls=[{"name": "web_fetch", "args": {"url": url}, "id": str(len(pieces))}])
            warning, stopped = guard._track_and_check({"messages": [message]}, guard_runtime)
            assert warning is None and not stopped
            raw = await benchmark_tools.web_fetch.coroutine(url=url, runtime=runtime)
            assert _budget_content(raw, tool_name="web_fetch", tool_call_id="read", outputs_path=str(tmp_path), config=config) is None
            result = json.loads(raw)
            assert result["start"] == sum(map(len, pieces))
            assert result["outgoing_links"] == client.page_links[source]
            assert result["end"] > result["start"]
            pieces.append(result["text"])
            url = result["next_url"]
            assert len(pieces) < 100
        assert "".join(pieces) == body
        repeat_url = source + f"#sp-read-start={len(body) - 20}"
        await benchmark_tools.web_fetch.coroutine(url=repeat_url, runtime=runtime)
        repeated = json.loads(await benchmark_tools.web_fetch.coroutine(url=repeat_url, runtime=runtime))
        assert repeated["read_progress"]["new_text"] is False
        assert repeated["read_progress"]["first_read"]["end"] == repeated["end"]
        assert repeated["next_url"] is None

    asyncio.run(read())


def test_budgeted_repeat_keeps_the_same_window_and_reports_already_read(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from dcc_harness.evaluation import benchmark_tools
    from dcc_harness.tools import WebTools

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "native"))
    source = "https://example.org/long"
    client = WebTools()
    client.pages[source] = "A" * 30000
    monkeypatch.setattr(benchmark_tools, "_client", lambda _: client)
    runtime = SimpleNamespace(context={"thread_id": "repeat-budget"}, config={})

    async def read():
        first = json.loads(await benchmark_tools.web_fetch.coroutine(url=source, runtime=runtime))
        for _ in range(12):
            raw = await benchmark_tools.web_fetch.coroutine(url=source, runtime=runtime)
            assert len(raw) <= 12000
            repeated = json.loads(raw)
            assert repeated["text"] == first["text"]
            assert repeated["end"] == first["end"]
            assert repeated["read_progress"]["new_text"] is False

    asyncio.run(read())


def test_web_fetch_keeps_oversized_navigation_for_native_externalization(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from dcc_harness.evaluation import benchmark_tools
    from dcc_harness.tools import WebTools

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "native"))
    source = "https://example.org/data"
    client = WebTools()
    client.pages[source] = "complete body"
    client.page_links[source] = [{"url": f"https://example.org/{i}", "title": "X" * 300} for i in range(100)]
    monkeypatch.setattr(benchmark_tools, "_client", lambda _: client)
    runtime = SimpleNamespace(context={"thread_id": "oversized"}, config={})
    result = json.loads(asyncio.run(benchmark_tools.web_fetch.coroutine(url=source, runtime=runtime)))
    assert result["text"] == "complete body"
    assert result["outgoing_links"] == client.page_links[source]
    assert result["next_url"] is None


@pytest.mark.parametrize("text", [
    '| name |\n| --- |\n| A |\n\nSources by row: A — https://example.org/source',
    '```json\n[{"name":"A"}]\n```\nSources by row: A — https://example.org/source',
])
def test_answer_columns_with_separate_row_citations_keep_existing_scoring(text):
    sample = dataclasses.replace(task(), gold=[{"name": "A"}])
    assert parse_answer(sample, text) == [{"name": "A"}]
    assert score_task(sample, text)["f1_by_row"] == 1


@pytest.mark.parametrize("answer", [
    '| name |\n| --- |\n| A |',
    '```json\n[{"name":"A"}]\n```',
])
def test_native_delivery_and_scoring_select_the_same_answer_beside_counts(answer):
    sample = dataclasses.replace(task(), gold=[{"name": "A"}])
    text = '| Stage | Count |\n| --- | --- |\n| Fetched | 3 |\n\nAnswer:\n' + answer
    assert parse_answer(sample, text) == [{"name": "A"}]
    result = score_task(sample, text)
    assert result["num_pred_rows"] == 1
    assert result["f1_by_row"] == 1


@pytest.mark.parametrize(
    "message,status",
    [
        (
            {"type": "ai", "content": "[DSML]tool-call", "tool_calls": []},
            "protocol_error",
        ),
        (
            {
                "type": "ai",
                "content": "",
                "additional_kwargs": {"error_type": "AuthenticationError"},
            },
            "infrastructure_error",
        ),
        ({"type": "ai", "content": "normal response"}, "unfinished"),
    ],
)
def test_native_failure_classification_does_not_call_everything_evidence_failure(
    message, status
):
    from dcc_harness.evaluation.benchmark_runner import failure_status

    assert failure_status({"messages": [message]}) == status


@pytest.mark.parametrize("mode,code", [
    ("empty", "No results found"),
    ("unavailable", "WEB_SEARCH_UNAVAILABLE"),
])
def test_search_failures_match_native_protocol_and_stop_native_retry_guard(tmp_path, monkeypatch, mode, code):
    from types import SimpleNamespace

    from deerflow.agents.middlewares.tool_result_meta import normalize_tool_result
    from deerflow.agents.middlewares.web_search_budget_middleware import (
        WebSearchBudgetMiddleware,
    )
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from dcc_harness.evaluation import benchmark_tools
    from dcc_harness.tools import InfrastructureError

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "native"))

    class Provider:
        async def web_search(self, **kwargs):
            if mode == "unavailable":
                raise InfrastructureError("All public search providers are unavailable")
            return {"results": [], "backend": "wikipedia", "search_scope": "encyclopedia", "fallback_reason": "duckduckgo_lite: challenge; seznam: no search results"}

    monkeypatch.setattr(benchmark_tools, "_client", lambda _: Provider())
    runtime = SimpleNamespace(context={"thread_id": "failed-search"}, config={})
    messages = [HumanMessage(content="Research independent facts.")]

    async def search():
        for index in range(2):
            call = {"id": f"search-{index}", "name": "web_search", "args": {"query": f"distinct query {index}"}}
            raw = await benchmark_tools.web_search.coroutine(**call["args"], runtime=runtime)
            data = json.loads(raw)
            assert data["error"] == code
            if mode == "empty":
                assert data["results"] == [] and data["backend"] == "wikipedia"
                assert data["fallback_reason"].startswith("duckduckgo_lite: challenge")
            message = normalize_tool_result(ToolMessage(content=raw, name="web_search", tool_call_id=call["id"]))
            assert message.additional_kwargs["deerflow_tool_meta"]["status"] == "error"
            messages.extend([AIMessage(content="", tool_calls=[call]), message])

    asyncio.run(search())
    next_call = {"id": "search-next", "name": "web_search", "args": {"query": "another query"}}
    messages.append(AIMessage(content="", tool_calls=[next_call]))
    stopped = WebSearchBudgetMiddleware._short_circuit(SimpleNamespace(tool_call=next_call, state={"messages": messages}))
    assert json.loads(stopped.content)["error"] == "WEB_SEARCH_ATTEMPTS_EXHAUSTED"
    events = [json.loads(line) for line in (tmp_path / "native" / "threads" / "failed-search" / "benchmark-tools.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(events) == 2 and all(e["result"]["error"] == code for e in events)


def test_successful_fallback_search_is_not_changed_into_failure(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from dcc_harness.evaluation import benchmark_tools

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "native"))
    result = {"results": [{"title": "Source", "url": "https://example.org", "snippet": "Fact"}], "backend": "seznam", "fallback_reason": "duckduckgo_lite: challenge"}

    class Provider:
        async def web_search(self, **kwargs):
            return result

    monkeypatch.setattr(benchmark_tools, "_client", lambda _: Provider())
    runtime = SimpleNamespace(context={"thread_id": "success-search"}, config={})
    raw = asyncio.run(benchmark_tools.web_search.coroutine(query="fact", runtime=runtime))
    assert json.loads(raw) == result


def test_diagnostics_keep_failed_provider_attempts_when_fallback_succeeds():
    rows = [
        {
            "benchmark": "widesearch",
            "status": "completed",
            "metrics": {"f1_by_row": 1},
            "tool_events": [
                {
                    "tool": "web_search",
                    "result": {
                        "backend": "seznam",
                        "results": [1],
                        "fallback_reason": "duckduckgo_lite: provider error",
                    },
                },
                {"tool": "web_fetch", "result": {"error": "read failed"}},
            ],
        }
    ]
    result = summarize(rows, expected_tasks=1)
    assert result["retrieval"]["tool_calls"] == 2
    assert result["retrieval"]["tool_failures"] == 1
    assert result["retrieval"]["provider_errors"] == 1


def test_empty_markdown_cells_are_recorded_as_json_null_without_changing_scorer():
    sample = task()
    answer = parse_answer(sample, "| name | number |\n| --- | --- |\n| A | |")
    json.dumps(answer, allow_nan=False)


def test_native_compaction_model_uses_actual_endpoint_model_and_credential(monkeypatch):
    from deerflow.agents.middlewares.summarization_middleware import (
        create_summarization_middleware,
    )

    from dcc_harness.evaluation.benchmark_runner import (
        Settings,
        effective_app,
    )

    monkeypatch.setenv("DCC_MODEL_API_KEY", "SENTINEL_COMPACTION_KEY")
    settings = Settings(
        config="configs/stackplanner_benchmarks.yaml",
        base_url="http://actual-endpoint/v1",
        model="actual-model",
    )
    app = effective_app(settings)
    model = create_summarization_middleware(app_config=app).model.bound
    assert (
        model.model_name == "actual-model"
        and model.openai_api_base == "http://actual-endpoint/v1"
    )
    assert model.openai_api_key.get_secret_value() == "SENTINEL_COMPACTION_KEY"
    assert model.max_tokens == settings.max_tokens


@pytest.mark.parametrize("recover_qwen_tool_calls", [False, True])
def test_native_transport_preserves_provider_request_and_stream_defaults(
    recover_qwen_tool_calls,
):
    from deerflow.config.app_config import AppConfig
    from deerflow.models.factory import create_chat_model

    from dcc_harness.evaluation.benchmark_runner import (
        Settings,
        effective_app,
    )

    app = effective_app(Settings(
        config="configs/stackplanner_benchmarks.yaml",
        base_url="http://127.0.0.1:1/v1", model="transport-test",
        recover_qwen_tool_calls=recover_qwen_tool_calls,
    ))
    original = app.model_dump(mode="python")
    original["models"][0]["use"] = (
        "deerflow.models.vllm_provider:VllmChatModel" if recover_qwen_tool_calls
        else "langchain_openai:ChatOpenAI"
    )

    async def compare():
        baseline = create_chat_model(app_config=AppConfig.model_validate(original), attach_tracing=False)
        adapted = create_chat_model(app_config=app, attach_tracing=False)
        try:
            assert adapted._default_params == baseline._default_params
            assert adapted.stream_usage == baseline.stream_usage
            assert adapted.stream_chunk_timeout == baseline.stream_chunk_timeout
        finally:
            await baseline.root_async_client._client.aclose()
            await adapted.root_async_client._client.aclose()

    asyncio.run(compare())


@pytest.mark.parametrize("recover_qwen_tool_calls", [False, True])
@pytest.mark.parametrize("bypass_proxy", [False, True])
def test_native_models_can_request_through_proxy_on_separate_event_loops(
    monkeypatch, recover_qwen_tool_calls, bypass_proxy
):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from deerflow.models.factory import create_chat_model

    from dcc_harness.evaluation.benchmark_runner import (
        Settings,
        effective_app,
    )

    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({
                "id": "loop-test", "object": "chat.completion", "created": 0,
                "model": "loop-test", "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "ok"}}],
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    # The local server acts as an HTTP proxy. No real API, DNS or key is used.
    for name in ("http_proxy", "https_proxy", "all_proxy", "no_proxy",
                 "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    proxy = "http://127.0.0.1:1" if bypass_proxy else f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("http_proxy", proxy)
    if bypass_proxy:
        monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    base_url = (f"http://127.0.0.1:{server.server_port}/v1" if bypass_proxy
                else f"http://loop-test-{server.server_port}.invalid/v1")
    app = effective_app(Settings(
        config="configs/stackplanner_benchmarks.yaml",
        base_url=base_url,
        model="loop-test", timeout=5.0,
        recover_qwen_tool_calls=recover_qwen_tool_calls,
    ))
    clients = {}
    sync_clients = {}

    async def request():
        model = create_chat_model(app_config=app, attach_tracing=False)
        client = model.root_async_client._client
        clients.setdefault(id(client), (asyncio.get_running_loop(), client))
        sync_client = model.root_client._client
        sync_clients.setdefault(id(sync_client), sync_client)
        assert (await model.ainvoke("transport regression")).content == "ok"
        assert model.invoke("sync transport regression").content == "ok"
        return client

    try:
        with asyncio.Runner() as central, asyncio.Runner() as specialist:
            try:
                central_client = central.run(request())
                specialist_client = specialist.run(request())
                assert central_client is not specialist_client
                assert central.run(request()) is central_client
                assert specialist.run(request()) is specialist_client
                assert len(requests) == 8
                assert all(item["model"] == "loop-test" for item in requests)
            finally:
                for loop, client in clients.values():
                    loop.run_until_complete(client.aclose())
                for client in sync_clients.values():
                    client.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_source_fingerprint_includes_native_soul_and_skill_support_files(tmp_path):
    from scripts.evaluate_sp_benchmarks import directory_digest, source_digest

    soul = (
        tmp_path
        / "StackPlanner2.0/backend/packages/harness/deerflow/sp/central/SOUL.md"
    )
    soul.parent.mkdir(parents=True)
    soul.write_text("version one", encoding="utf-8")
    before = source_digest(tmp_path)
    soul.write_text("version two", encoding="utf-8")
    assert source_digest(tmp_path) != before
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "SKILL.md").write_text("skill instructions", encoding="utf-8")
    support = skills / "helper.sh"
    support.write_text("first implementation", encoding="utf-8")
    before = directory_digest(skills)
    support.write_text("changed implementation", encoding="utf-8")
    assert directory_digest(skills) != before


def test_search_identity_detects_endpoint_and_proxy_changes_without_recording_secrets(
    monkeypatch,
):
    from scripts.evaluate_sp_benchmarks import retrieval_identity

    monkeypatch.setenv("DCC_SEARCH_URL", "http://user:SECRET@search-host")
    before = retrieval_identity()
    assert "SECRET" not in json.dumps(before)
    monkeypatch.setenv("DCC_SEARCH_URL", "http://other-search-host")
    assert before != retrieval_identity()
    before = retrieval_identity()
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:SECRET@proxy-host")
    assert before != retrieval_identity()


def test_third_dataset_runs_through_native_runner_and_generic_metrics(tmp_path, monkeypatch):
    import sys
    import types

    import deerflow.sp.runtime as native

    from dcc_harness.datasets.benchmark_plugins import BenchmarkPlugin
    from dcc_harness.evaluation.benchmark_runner import (
        Settings,
        run_task,
    )

    skill = tmp_path / "skills" / "public" / "third"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: third\ndescription: Handle public third dataset.\n---\nUse only supplied public records.\n", encoding="utf-8")
    module = types.ModuleType("test_third_runner_plugin")
    module.plugin = BenchmarkPlugin(
        name="third", prepare_task=lambda sample: sample.prompt,
        prepare_inputs=lambda sample, thread_id: {"public_document": sample.document},
        skills_path=skill.parents[1], parse_answer=lambda sample, text: text,
        score_task=lambda sample, text, judge=None: {"exact_match": float(text == sample.gold)},
    )
    monkeypatch.setitem(sys.modules, module.__name__, module)
    captured = {}

    class Graph:
        async def astream(self, inputs, **kwargs):
            captured["inputs"] = inputs
            yield "values", finished(sp_last_run_summary="public answer")

    def build(config, *, app_config):
        captured["tools"] = {tool.name for tool in app_config.tools}
        return Graph()

    monkeypatch.setattr(native, "make_sp_agent", build)
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "native"))
    sample = Task(id="third-id", benchmark="third", split="validation", prompt="Public task", document="Public source", gold="public answer", metadata={"secret": "PRIVATE_META"})
    settings = Settings(config="configs/stackplanner_benchmarks.yaml", base_url="http://unused", model="scripted", dataset_plugins=("test_third_runner_plugin:plugin",))
    result = asyncio.run(run_task(sample, settings, output_dir=tmp_path / "attempt"))
    assert result["status"] == "completed" and result["metrics"] == {"exact_match": 1.0}
    assert captured["inputs"]["public_document"] == "Public source"
    assert "PRIVATE_META" not in str(captured["inputs"])
    assert "web_search" not in captured["tools"]
    assert summarize([result], expected_tasks=1)["third"]["exact_match"] == 1.0


def test_each_native_task_rebuilds_dataset_skill_mounts(tmp_path, monkeypatch):
    import deerflow.sp.runtime as native
    from deerflow.sandbox.sandbox_provider import (
        get_sandbox_provider,
        reset_sandbox_provider,
    )

    from dcc_harness.evaluation.benchmark_runner import (
        Settings,
        run_task,
    )

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "native"))
    seen = []
    providers = []

    class Graph:
        def __init__(self, config, app):
            self.config, self.app = config, app

        async def astream(self, inputs, **kwargs):
            provider = get_sandbox_provider()
            providers.append(provider)
            identity = self.config["context"]
            sandbox_id = provider.acquire(identity["thread_id"], user_id=identity["user_id"])
            sandbox = provider.get(sandbox_id)
            name = Path(self.app.skills.path).name
            body = sandbox.read_file(f"/mnt/skills/public/{name}/SKILL.md")
            assert f"name: {name}" in body
            seen.append(name)
            yield "values", finished(sp_last_run_summary='["California law"]' if name == "cuad" else '[{"name":"delivered"}]')

    monkeypatch.setattr(native, "make_sp_agent", lambda config, app_config: Graph(config, app_config))
    settings = Settings(config="configs/stackplanner_benchmarks.yaml", base_url="http://unused", model="scripted")
    reset_sandbox_provider()
    try:
        for index, sample in enumerate([task(), Task(id="law", benchmark="cuad", split="validation", prompt="Question", document="California law", gold=["California law"])]):
            result = asyncio.run(run_task(sample, settings, output_dir=tmp_path / f"attempt-{index}"))
            assert result["status"] == "completed", result
        assert seen == ["widesearch", "cuad"] and providers[0] is not providers[1]
    finally:
        reset_sandbox_provider()


def test_cli_loads_a_third_dataset_plugin_and_records_its_identity(tmp_path, monkeypatch):
    import sys
    import types

    import deerflow.sp.runtime as native

    from dcc_harness.datasets.benchmark_plugins import BenchmarkPlugin
    from scripts import evaluate_sp_benchmarks as cli

    skills = tmp_path / "skills" / "public" / "third"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("---\nname: third\ndescription: Process public third records.\n---\nRead supplied public records.\n", encoding="utf-8")
    extension = types.ModuleType("test_cli_third_plugin")
    extension.plugin = BenchmarkPlugin("third", lambda sample: sample.prompt, lambda sample, thread_id: {}, skills.parents[1], parse_answer=lambda sample, text: text, score_task=lambda sample, text, judge=None: {"accuracy": float(text == sample.gold)})
    monkeypatch.setitem(sys.modules, extension.__name__, extension)
    monkeypatch.setenv("DCC_MODEL_API_KEY", "PRIVATE_API_KEY")
    inputs = tmp_path / "third.jsonl"
    inputs.write_text(json.dumps(Task("third-id", "third", "validation", "Public question", gold="Public answer").to_dict()), encoding="utf-8")

    class Graph:
        async def astream(self, inputs, **kwargs):
            yield "values", finished(sp_last_run_summary="Public answer")

    monkeypatch.setattr(native, "make_sp_agent", lambda config, app_config: Graph())
    args = cli.parser().parse_args(["--tasks", str(inputs), "--run-dir", str(tmp_path / "run"), "--dataset-source", "third-public-dev", "--dataset-plugin", "test_cli_third_plugin:plugin", "--base-url", "http://unused", "--model", "scripted", "--exact-proxy"])
    assert asyncio.run(cli.main_async(args)) == 0
    args.resume = True
    assert asyncio.run(cli.main_async(args)) == 0
    manifest_text = (tmp_path / "run/manifest.json").read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert "PRIVATE_API_KEY" not in manifest_text
    assert set(manifest["dataset_plugins"]) == {"third"}
    assert [skill["name"] for skill in manifest["dataset_plugins"]["third"]["skills"]] == ["third"]
    report = json.loads((tmp_path / "run/report.json").read_text(encoding="utf-8"))
    assert report["third"]["accuracy"] == 1.0 and report["dataset_source"] == "third-public-dev"
