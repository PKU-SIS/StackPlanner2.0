"""Regression contracts for the opt-in tender audit experiment."""

import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from openpyxl import Workbook
from pydantic import ValidationError

from app.experiments.tender_audit.cases import AuditCase, build_cases, validate_result
from app.experiments.tender_audit.observability import AuditTrace
from app.experiments.tender_audit.profile import AuditProfile
from app.experiments.tender_audit.ragflow import RagflowClient, RagflowSettings
from app.experiments.tender_audit.runner import aggregate_metrics, experiment_config, preflight, recover_failed_attempt, select_final_report
from deerflow.sp.subagents import SPSubagentTask
from deerflow.subagents.builtins.sp_specialists import REPORTER_CONFIG, RESEARCHER_CONFIG


@pytest.fixture
def audit_case():
    return AuditCase(1, "示例标书", "dataset-a", ("规则甲",), "完整规则：比较付款时间。", "付款 时间")


def sheet(path, rows):
    book = Workbook()
    for row in rows:
        book.active.append(row)
    book.save(path)


def test_case_builder_uses_rule_table_and_excludes_gt_answers(tmp_path):
    checks, gt = tmp_path / "rules.xlsx", tmp_path / "gt.xlsx"
    sheet(checks, [["审核要点名称", "审核点内容", "关键词"], ["规则甲", "原始规则甲", "词甲"], ["规则乙", "原始规则乙", "词乙"]])
    sheet(gt, [["序号", "出错的标书文件名", "审核要点名称", "标书文件对应的知识库id", "审核点内容", "关键对比内容", "错误来源位置"], [1, "标书", "规则乙；规则甲", "kb-a", "GT-POISON", "SECRET-ANSWER", "HIDDEN-POSITION"]])
    case = build_cases(checks, gt, expected_count=1)[0]
    assert case.rule_names == ("规则乙", "规则甲")
    assert case.rule_content == "原始规则乙\n\n原始规则甲"
    assert not any(value in json.dumps(case.public_dict(), ensure_ascii=False) for value in ["GT-POISON", "SECRET-ANSWER", "HIDDEN-POSITION"])
    with pytest.raises(ValueError, match="20"):
        build_cases(checks, gt)


@pytest.mark.asyncio
async def test_rag_request_is_bound_and_preserves_chunks(tmp_path, audit_case):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"code": 0, "data": {"chunks": [{"id": "c1", "content_with_weight": "原文", "doc_id": "d1", "positions": [[1, 2]], "similarity": 0.9}]}})

    trace = AuditTrace(tmp_path)
    client = RagflowClient(RagflowSettings("https://rag.test/api/v1/retrieval", "secret"), audit_case.dataset_id, trace, transport=httpx.MockTransport(respond))
    result = await client.search("付款时间")
    assert requests[0]["dataset_ids"] == ["dataset-a"]
    assert requests[0]["page_size"] == 15 and requests[0]["top_k"] == 1024
    assert result["chunks"][0]["positions"] == [[1, 2]]
    assert result["chunks"][0]["content"] == "原文"
    tool = client.tool()
    with pytest.raises(ValidationError):
        await tool.ainvoke({"query": "q", "dataset_id": "foreign"})
    assert len(requests) == 1
    assert "secret" not in (tmp_path / "tool_calls.jsonl").read_text()


@pytest.mark.asyncio
async def test_rag_business_error_is_not_empty_success(tmp_path, audit_case):
    client = RagflowClient(RagflowSettings("https://rag.test", "secret"), audit_case.dataset_id, AuditTrace(tmp_path), transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"code": 401, "message": "denied"})))
    with pytest.raises(RuntimeError, match="401"):
        await client.search("q")
    assert client.trace.tool_records[0]["status"] == "failed"


def test_profile_supplies_full_rules_and_json_contract_without_mutating_defaults(tmp_path, audit_case):
    trace = AuditTrace(tmp_path)
    client = RagflowClient(RagflowSettings("https://rag.test", "secret"), audit_case.dataset_id, trace)
    profile = AuditProfile(audit_case, client, trace, tmp_path / "outputs")
    original = REPORTER_CONFIG.system_prompt
    researcher = profile.configure_subagent(SPSubagentTask("a", "researcher", "查", "测试"), RESEARCHER_CONFIG)
    assert researcher.tools == ["local_search_tool"]
    assert researcher.skills == []
    reporter_task = SPSubagentTask("b", "reporter", "写", "测试")
    profile.prepare_task(reporter_task)
    assert reporter_task.context_refs["audit_task_context"] == audit_case.task_context()
    assert "case_id" in reporter_task.context_refs["audit_report_instructions"]
    reporter = profile.configure_subagent(reporter_task, REPORTER_CONFIG)
    assert reporter.skills == [] and reporter.tools == ["read_file"]
    assert "JSON" in reporter.system_prompt
    assert REPORTER_CONFIG.system_prompt == original
    assert REPORTER_CONFIG.skills == ["stackplanner-reporting"]
    assert RESEARCHER_CONFIG.tools is None


@pytest.mark.asyncio
async def test_scoped_file_tools_reject_traversal_and_symlinks(tmp_path, audit_case):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    outside = tmp_path / "gt.txt"
    outside.write_text("hidden ground truth")
    (outputs / "escape").symlink_to(tmp_path, target_is_directory=True)
    trace = AuditTrace(tmp_path / "trace")
    profile = AuditProfile(audit_case, RagflowClient(RagflowSettings("https://rag.test", "key"), "dataset-a", trace), trace, outputs)
    tools = {tool.name: tool for tool in profile.tools()}
    for path in [str(outside), "/mnt/user-data/outputs/../gt.txt", "/mnt/user-data/outputs/escape/gt.txt"]:
        with pytest.raises(ValueError):
            await tools["read_file"].ainvoke({"file_path": path})
    await tools["write_file"].ainvoke({"file_path": "/mnt/user-data/outputs/result.json", "content": "{}"})
    assert (outputs / "result.json").read_text() == "{}"


def test_trace_deduplicates_usage_and_redacts_credentials(tmp_path):
    trace = AuditTrace(tmp_path, secrets=("private-key-value",))
    call_id = uuid4()
    trace.on_chat_model_start({}, [[HumanMessage(content="test private-key-value")]], run_id=call_id, tags=["subagent:sp-researcher"])
    response = LLMResult(generations=[[ChatGeneration(message=AIMessage(content="done", usage_metadata={"input_tokens": 11, "output_tokens": 3, "total_tokens": 14}))]])
    trace.on_llm_end(response, run_id=call_id)
    trace.on_llm_end(response, run_id=call_id)
    assert len(trace.model_records) == 1
    assert trace.metrics(1)["llm_totals"]["total_tokens"] == 14
    assert trace.metrics(1)["agents"]["researcher"]["calls"] == 1
    assert "private-key-value" not in (tmp_path / "model_calls.jsonl").read_text()
    missing = uuid4()
    trace.on_chat_model_start({}, [[]], run_id=missing)
    trace.on_llm_error(RuntimeError("failed"), run_id=missing)
    assert trace.metrics(1)["token_usage_complete"] is False


def test_result_validation_never_repairs_output_and_defers_evidence_quality(audit_case):
    value = {"case_id": 1, "pass": False, "needs_review": False, "score": 0.8, "conclusion": "不一致", "reference": "条款前后冲突", "evidence": [{"chunk_id": "c1", "quote": "付款时间", "position": ""}]}
    raw = json.dumps(value, ensure_ascii=False)
    assert validate_result(raw, audit_case, [{"id": "c1", "content": "约定付款时间为十日。"}])["valid"]
    assert not validate_result("```json\n" + raw + "\n```", audit_case, [])["json_valid"]
    assert not validate_result(raw.replace('"case_id": 1', '"case_id": 2'), audit_case, [])["valid"]
    validation = validate_result(raw, audit_case, [{"id": "c1", "content": "不同的内容"}])
    assert validation["valid"] and validation["evidence_evaluated"] is False


@pytest.mark.parametrize(
    ("quote", "source"),
    [
        ("扣除10%作为违约金", "扣除10\\%作为违约金"),
        ("扣除10\\%作为违约金", "扣除10%作为违约金"),
        ("扣除10\\%作为违约金", "扣除10\\%作为违约金"),
        ("扣除10 %作为违约金", "扣除10\\%作为违约金"),
        ("扣除100%作为违约金", "扣除10\\%作为违约金"),
        ("10%", "比例110\\%"),
        ("0%", "比例10.0\\%"),
        ("10%", "比例-10\\%"),
        ("扣除1\\0%作为违约金", "扣除10%作为违约金"),
        ("扣除10%作为违约金", "扣除10\\\\%作为违约金"),
        ("路径\\folder", "路径folder"),
        ("路径%20", "路径\\%20"),
        (" \n ", "任意正文"),
    ],
)
def test_evidence_quality_never_changes_format_acceptance_or_rewrites_output(audit_case, quote, source):
    value = {"case_id": 1, "pass": False, "needs_review": False, "score": 0.8, "conclusion": "不一致", "reference": "比较条款", "evidence": [{"chunk_id": "c1", "quote": quote, "position": ""}]}
    raw = json.dumps(value, ensure_ascii=False)
    validation = validate_result(raw, audit_case, [{"id": "c1", "content": source}])
    assert validation["valid"] is True
    assert validation["evidence_evaluated"] is False
    assert validation["parsed_result"] == value
    assert "evidence_matches" not in validation


def test_invalid_json_is_not_repaired_and_chunk_provenance_is_evaluated_offline(audit_case):
    invalid_json = r'{"case_id":1,"evidence":[{"quote":"扣除10\%"}]}'
    assert not validate_result(invalid_json, audit_case, [{"id": "c1", "content": "扣除10\\%"}])["json_valid"]
    value = {"case_id": 1, "pass": False, "needs_review": False, "score": 0.8, "conclusion": "不一致", "reference": "比较条款", "evidence": [{"chunk_id": "invented", "quote": "扣除10%", "position": ""}]}
    assert validate_result(json.dumps(value, ensure_ascii=False), audit_case, [{"id": "c1", "content": "扣除10\\%"}])["valid"]


def test_final_result_uses_exact_accepted_artifact_not_latest_message_or_other_run():
    correct = {"artifact_id": "report-1", "metadata": {"target_agent": "reporter", "completion_status": "complete"}, "run_id": "r", "version": 1, "is_current": True}
    other = {**correct, "artifact_id": "other", "run_id": "another-run", "version": 9}
    state = {"sp_current_stage": "finished", "sp_last_final_artifact_ref": "report-1", "sp_current_artifact_refs": {"report_revision": correct, "report": other}, "messages": ["A summary is not the result"]}
    assert select_final_report(state, "r") == correct
    state["sp_last_final_artifact_ref"] = "unregistered"
    assert select_final_report(state, "r") is None


def test_batch_metrics_include_failed_attempt_costs_separately(tmp_path):
    for attempt, status, tokens in [(1, "failed", 10), (2, "completed", 20)]:
        directory = tmp_path / "case_01" / f"attempt_{attempt:03d}"
        directory.mkdir(parents=True)
        (directory / "metrics.json").write_text(
            json.dumps({"status": status, "duration_seconds": 2, "token_usage_complete": status == "completed", "llm_totals": {"calls": 1, "input_tokens": tokens, "output_tokens": 0, "total_tokens": tokens}, "tools": {"ragflow_calls": 1}})
        )
    metrics = aggregate_metrics(tmp_path)
    assert metrics["all_attempts"]["total_tokens"] == 30
    assert metrics["successful_attempts"]["total_tokens"] == 20
    assert metrics["all_attempts"]["token_usage_complete"] is False


def test_worker_crash_keeps_completed_costs_and_marks_pending_usage_unknown(tmp_path):
    trace = AuditTrace(tmp_path)
    completed, pending = uuid4(), uuid4()
    trace.on_chat_model_start({}, [[]], run_id=completed)
    trace.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(content="done", usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}))]]), run_id=completed)
    trace.on_chat_model_start({}, [[]], run_id=pending, tags=["subagent:sp-reporter"])
    summary = recover_failed_attempt(tmp_path, case_id=1, returncode=124, duration_seconds=20)
    metrics = json.loads((tmp_path / "metrics.json").read_text())
    assert summary["status"] == "failed" and summary["stop_reason"] == "worker_timeout"
    assert metrics["llm_totals"]["calls"] == 2 and metrics["llm_totals"]["total_tokens"] == 12
    assert metrics["token_usage_complete"] is False
    assert metrics["agents"]["reporter"]["calls"] == 1


@pytest.mark.asyncio
async def test_preflight_failure_keeps_separate_costs(tmp_path, monkeypatch, audit_case):
    async def failed_model(config, thinking, trace):
        call_id = uuid4()
        trace.on_chat_model_start({}, [[]], run_id=call_id, tags=["middleware:preflight"])
        trace.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(content="unexpected", usage_metadata={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}))]]), run_id=call_id)
        raise RuntimeError("preflight invalid response")

    monkeypatch.setattr("app.experiments.tender_audit.runner.model_preflight", failed_model)
    config = SimpleNamespace(models=[SimpleNamespace(model_extra={})])
    with pytest.raises(RuntimeError, match="invalid response"):
        await preflight([audit_case], config, RagflowSettings("https://rag.test", "key"), tmp_path / "preflight", True)
    result = json.loads((tmp_path / "preflight/preflight.json").read_text())
    assert result["status"] == "failed" and result["metrics"]["llm_totals"]["total_tokens"] == 5
    assert aggregate_metrics(tmp_path)["all_attempts"]["calls"] == 0


@pytest.mark.asyncio
async def test_experiment_requests_usage_for_tool_free_vllm_streams_without_changing_global_config(tmp_path, monkeypatch):
    from deerflow.config.app_config import AppConfig
    from deerflow.config.model_config import ModelConfig
    from deerflow.config.sandbox_config import SandboxConfig
    from deerflow.models import create_chat_model

    original = AppConfig(models=[ModelConfig(name="test-qwen", use="deerflow.models.vllm_provider:VllmChatModel", model="qwen-test", api_key="dummy", base_url="https://model.test/v1")], sandbox=SandboxConfig(use="test"))
    monkeypatch.setattr("deerflow.config.app_config.get_app_config", lambda: original)
    config = experiment_config("test-qwen")
    assert "stream_usage" not in original.models[0].model_extra
    assert config.models[0].model_extra["max_retries"] == 0
    assert "max_retries" not in original.models[0].model_extra
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        # Mimic a server that only emits usage when the client requests it.
        events = [
            {"id": "reply", "object": "chat.completion.chunk", "model": "qwen-test", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}]},
        ]
        if (payload.get("stream_options") or {}).get("include_usage"):
            events.append({"id": "reply", "object": "chat.completion.chunk", "model": "qwen-test", "choices": [], "usage": {"prompt_tokens": 6, "completion_tokens": 7, "total_tokens": 13}})
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text="".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n")

    trace = AuditTrace(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = create_chat_model(name="test-qwen", app_config=config, attach_tracing=False, http_async_client=client)
        chunks = [chunk async for chunk in model.astream([HumanMessage(content="OK")], config={"callbacks": [trace], "tags": ["subagent:sp-memory-recaller"]})]
    assert "".join(chunk.content for chunk in chunks) == "OK"
    assert requests[0]["stream_options"]["include_usage"] is True
    assert trace.metrics(1)["token_usage_complete"] is True
    assert trace.metrics(1)["agents"]["memory-recaller"]["total_tokens"] == 13


def test_experiment_model_lookup_and_client_apply_overrides_without_mutating_global_config(monkeypatch):
    from deerflow.config.app_config import AppConfig
    from deerflow.config.model_config import ModelConfig
    from deerflow.config.sandbox_config import SandboxConfig
    from deerflow.models import create_chat_model

    original = AppConfig(
        models=[ModelConfig(name="test-qwen", use="deerflow.models.vllm_provider:VllmChatModel", model="qwen-test", api_key="dummy", base_url="https://model.test/v1", max_retries=1, timeout=600, max_tokens=256)],
        sandbox=SandboxConfig(use="test"),
    )
    monkeypatch.setattr("deerflow.config.app_config.get_app_config", lambda: original)
    config = experiment_config("test-qwen", max_tokens=777)
    assert config.get_model_config("test-qwen") is config.models[0]
    model = create_chat_model(name="test-qwen", app_config=config, attach_tracing=False)
    assert model.max_retries == model.root_async_client.max_retries == 0
    assert model.request_timeout == 120
    assert model.max_tokens == 777
    assert original.get_model_config("test-qwen") is original.models[0]
    assert original.models[0].max_retries == 1
    assert original.models[0].timeout == 600
    assert original.models[0].max_tokens == 256
