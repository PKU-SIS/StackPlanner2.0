"""Experiment-only deadlines and lossless model-visible retrieval contracts."""

import asyncio
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import HumanMessage

from app.experiments.tender_audit.observability import AuditTrace
from app.experiments.tender_audit.ragflow import RagflowClient, RagflowSettings
from deerflow.agents.middlewares.llm_error_handling_middleware import LLMErrorHandlingMiddleware
from deerflow.config.app_config import AppConfig
from deerflow.config.sandbox_config import SandboxConfig


@pytest.mark.asyncio
async def test_visible_retrieval_strips_only_derived_and_empty_fields_and_keeps_full_raw(tmp_path):
    body = "<table><tr><td>分值40</td><td>4+13*3=43</td></tr></table>" + "完整表格" * 1000
    source = {
        "id": "c1",
        "content": "fallback",
        "content_with_weight": body,
        "content_ltks": "derived " * 2000,
        "content_sm_ltks": "index",
        "document_id": "doc",
        "document_keyword": "采购文件.pdf",
        "positions": [[21, 1, 2, 3, 4]],
        "table_id": "table-1",
        "table_part": 0,
        "table_parts": 2,
        "table_full_id": "full",
        "similarity": 0.7,
        "empty": "",
        "unused": None,
        "unknown_provenance": {"section": "技术评分"},
        "flag": False,
    }
    raw = {"code": 0, "data": {"chunks": [source]}}
    trace = AuditTrace(tmp_path)
    rag = RagflowClient(RagflowSettings("https://rag.test", "key"), "bound", trace, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=raw)))
    visible = json.loads(await rag.tool().ainvoke({"query": "评分"}))
    chunk = visible["chunks"][0]
    assert chunk["content"] == body
    assert "content_with_weight" not in chunk and "content_ltks" not in chunk and "content_sm_ltks" not in chunk
    assert "empty" not in chunk and "unused" not in chunk
    for key in ["id", "document_id", "document_keyword", "positions", "table_id", "table_part", "table_parts", "table_full_id", "similarity", "unknown_provenance", "flag"]:
        assert chunk[key] == source[key]
    assert json.loads((tmp_path / "tool_raw_results.jsonl").read_text())["response"] == raw
    assert rag.chunks[0]["content_ltks"] == source["content_ltks"]
    assert json.loads((tmp_path / "tool_visible_results.jsonl").read_text())["content"] == json.dumps(visible, ensure_ascii=False)


@pytest.mark.asyncio
async def test_repeated_queries_do_not_replace_original_bodies_with_unreadable_references(tmp_path):
    source = {"id": "same", "content": "两次都必须可见的完整原文", "positions": [[1]]}
    rag = RagflowClient(RagflowSettings("https://rag.test", "key"), "bound", AuditTrace(tmp_path), transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": {"chunks": [source]}})))
    for query in ["q1", "q2"]:
        result = json.loads(await rag.tool().ainvoke({"query": query}))
        assert result["chunks"][0]["content"] == source["content"]


def test_request_deadline_is_scoped_and_restores_existing_environment(monkeypatch):
    from app.experiments.tender_audit.runner import request_deadline_environment

    monkeypatch.setenv("DEER_FLOW_LLM_REQUEST_TIMEOUT_SECONDS", "777")
    with request_deadline_environment(150, no_progress_timeout=180):
        assert os.environ["DEER_FLOW_LLM_REQUEST_TIMEOUT_SECONDS"] == "150.0"
    assert os.environ["DEER_FLOW_LLM_REQUEST_TIMEOUT_SECONDS"] == "777"
    monkeypatch.delenv("DEER_FLOW_LLM_REQUEST_TIMEOUT_SECONDS")
    with pytest.raises(RuntimeError), request_deadline_environment(150, no_progress_timeout=180):
        raise RuntimeError("worker failed")
    assert "DEER_FLOW_LLM_REQUEST_TIMEOUT_SECONDS" not in os.environ


@pytest.mark.parametrize("seconds", [-1, 180, 200, float("nan"), float("inf")])
def test_request_deadline_must_allow_outer_watchdog_margin(seconds):
    from app.experiments.tender_audit.runner import request_deadline_environment

    with pytest.raises(ValueError), request_deadline_environment(seconds, no_progress_timeout=180):
        pass


@pytest.mark.asyncio
async def test_native_deadline_cancels_stalled_request_and_preserves_native_retry(tmp_path):
    from app.experiments.tender_audit.runner import request_deadline_environment

    calls, cancelled = [], []
    trace = AuditTrace(tmp_path)

    async def handler(_):
        call_id = uuid4()
        calls.append(call_id)
        trace.on_chat_model_start({}, [[HumanMessage(content="request")]], run_id=call_id, tags=["subagent:sp-researcher"])
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            cancelled.append(call_id)
            trace.on_llm_error(exc, run_id=call_id)
            raise

    with request_deadline_environment(0.02, no_progress_timeout=1):
        middleware = LLMErrorHandlingMiddleware(app_config=AppConfig(sandbox=SandboxConfig(use="test")))
        middleware.retry_base_delay_ms = middleware.retry_cap_delay_ms = 1
        result = await asyncio.wait_for(middleware.awrap_model_call(SimpleNamespace(), handler), timeout=1)
    assert "temporarily unavailable" in str(result.content).lower()
    assert len(calls) == len(cancelled) == 2  # Existing native TimeoutError budget.
    assert not trace.pending and len(trace.model_records) == 2
    assert all(record["status"] == "failed" and record["error_type"] == "CancelledError" for record in trace.model_records)


def test_error_trace_keeps_cause_chain_and_distinguishes_monitor_cleanup(tmp_path):
    trace = AuditTrace(tmp_path, secrets=("private-key",))
    first, pending = uuid4(), uuid4()
    trace.on_chat_model_start({}, [[]], run_id=first)
    error = ConnectionError("Connection error.")
    error.__cause__ = RuntimeError("loop closed private-key")
    trace.on_llm_error(error, run_id=first)
    record = trace.model_records[0]
    assert record["error_type"] == "ConnectionError"
    assert record["error_causes"] == [{"type": "RuntimeError", "message": "loop closed <REDACTED>"}]
    assert record["terminal_source"] == "provider_callback"
    assert record["ended_at"]
    trace.on_chat_model_start({}, [[]], run_id=pending)
    trace.close_pending("No observable progress")
    assert trace.model_records[-1]["terminal_source"] == "run_cleanup"
    assert "private-key" not in (tmp_path / "model_calls.jsonl").read_text()


def test_runtime_probe_verifies_real_client_and_rejects_stale_name_index(monkeypatch):
    from app.experiments.tender_audit.runner import experiment_config, runtime_model_settings
    from deerflow.config.model_config import ModelConfig

    original = AppConfig(
        models=[ModelConfig(name="qwen-test", use="deerflow.models.vllm_provider:VllmChatModel", model="qwen-test", api_key="private-key", base_url="https://model.test/v1", max_retries=1)], sandbox=SandboxConfig(use="test")
    )
    monkeypatch.setattr("deerflow.config.app_config.get_app_config", lambda: original)
    config = experiment_config("qwen-test")
    settings = runtime_model_settings(config, thinking=False)
    assert settings["sdk_max_retries"] == settings["client_max_retries"] == 0
    assert settings["http_timeout_seconds"] == 120
    assert settings["max_tokens"] == 8192
    assert settings["lookup_matches_snapshot"]
    assert "private-key" not in json.dumps(settings)
    config.models[0] = config.models[0].model_copy(update={"max_retries": 1})
    with pytest.raises(ValueError, match="index"):
        runtime_model_settings(config, thinking=False)


def test_reporter_packet_and_readable_cache_use_same_lossless_projection(tmp_path):
    from app.experiments.tender_audit.cases import AuditCase
    from app.experiments.tender_audit.profile import AuditProfile
    from deerflow.sp.subagents import SPSubagentTask

    case = AuditCase(9, "标书", "bound", ("评分",), "原规则", "检索")
    trace = AuditTrace(tmp_path / "trace")
    rag = RagflowClient(RagflowSettings("https://rag.test", "key"), "bound", trace)
    source = {"id": "chunk", "content": "完整表格25与27", "content_ltks": "derived", "positions": [[1]], "table_id": "t", "table_part": 0}
    rag.chunks = [source]
    profile = AuditProfile(case, rag, trace, tmp_path / "outputs")
    task = SPSubagentTask("a", "reporter", "合成", "任务")
    profile.prepare_task(task)
    packet = task.context_refs["audit_retrieved_evidence"]
    visible = packet["items"][0]
    assert visible["content"] == source["content"] and visible["positions"] == [[1]]
    assert visible["table_id"] == "t" and visible["table_part"] == 0
    assert "content_ltks" not in visible
    cached = json.loads(profile._path(packet["source_manifest"][0]["read_path"]).read_text())
    assert cached == visible
    assert rag.chunks[0] == source


@pytest.mark.asyncio
async def test_native_deadline_cancels_real_vllm_tool_bound_nonstream_path(tmp_path, monkeypatch):
    from langchain_core.tools import tool

    from app.experiments.tender_audit.runner import experiment_config, request_deadline_environment
    from deerflow.config.model_config import ModelConfig
    from deerflow.models import create_chat_model

    original = AppConfig(models=[ModelConfig(name="qwen-test", use="deerflow.models.vllm_provider:VllmChatModel", model="qwen-test", api_key="dummy", base_url="https://model.test/v1")], sandbox=SandboxConfig(use="test"))
    monkeypatch.setattr("deerflow.config.app_config.get_app_config", lambda: original)
    config = experiment_config("qwen-test")
    requests, cancellations = [], []
    trace = AuditTrace(tmp_path)

    @tool
    def search(query: str) -> str:
        """Retrieve a local source."""
        return query

    async def transport(request):
        requests.append(json.loads(request.content))
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellations.append(True)
            raise

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        model = create_chat_model(name="qwen-test", app_config=config, attach_tracing=False, http_async_client=client).bind_tools([search])

        async def handler(_):
            async for _chunk in model.astream([HumanMessage(content="search")], config={"callbacks": [trace], "tags": ["subagent:sp-researcher"]}):
                pass
            raise AssertionError("Stalled request unexpectedly completed")

        with request_deadline_environment(0.03, no_progress_timeout=1):
            middleware = LLMErrorHandlingMiddleware(app_config=config)
            middleware.retry_base_delay_ms = middleware.retry_cap_delay_ms = 1
            await asyncio.wait_for(middleware.awrap_model_call(SimpleNamespace(), handler), timeout=1)
    assert len(requests) == len(cancellations) == 2
    assert all(r["stream"] is False for r in requests)
    assert len(trace.model_records) == 2 and not trace.pending
    assert all(r["status"] == "failed" for r in trace.model_records)
