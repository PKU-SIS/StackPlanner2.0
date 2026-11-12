"""Reporter delivery must stream observable source text, never repair it."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import HumanMessage, SystemMessage
from test_collusion_reporter_budget_v23 import real_executor_module as real_executor_module


def test_default_extensions_and_subagent_are_unchanged():
    from deerflow.sp.extensions import SPExecutionExtensions
    from deerflow.subagents.config import SubagentConfig

    assert not SPExecutionExtensions().count_missing_reports
    assert not SubagentConfig("test", "test").model_errors_propagate


def test_exact_reference_roundtrip_does_not_drop_differences():
    from app.experiments.collusion_audit.reporter_delivery import compact_frame, expand_frame

    source = {"rule": "完整判断标准" * 100, "evidence": {"text_a": "甲方原文" * 200, "text_b": "乙方原文" * 200}}
    frame = {"context_refs": {"first": source, "repeat": source, "changed": {**source, "rule": source["rule"] + "例外"}}, "task": "任务"}
    compact = compact_frame(frame)
    assert expand_frame(compact) == frame
    assert len(json.dumps(compact, ensure_ascii=False)) < len(json.dumps(frame, ensure_ascii=False))
    assert "例外" in json.dumps(compact, ensure_ascii=False)
    assert frame["context_refs"]["repeat"] == source


def test_partial_string_preview_is_exact_and_not_json_repair():
    from app.experiments.collusion_audit.reporter_stream import content_prefix

    text = '<!DOCTYPE html>\n<p>中文 & "引文"</p>'
    encoded = json.dumps({"path": "/x", "content": text}, ensure_ascii=True)
    for end in range(len(encoded) + 1):
        assert text.startswith(content_prefix(encoded[:end]))
    assert content_prefix(encoded) == text
    assert content_prefix('{"content":"abc\\u4e') == "abc"


def trace(tmp_path):
    from app.experiments.tender_audit.observability import AuditTrace

    return AuditTrace(tmp_path)


def model(tmp_path, chunks):
    from app.experiments.collusion_audit.reporter_delivery import DELIVERY_CONTEXT
    from app.experiments.collusion_audit.reporter_stream import StreamingReporterModel

    async def respond(request):
        body = json.loads(request.content)
        assert body["stream"] and body["enable_thinking"] is False
        assert body["stream_options"]["include_usage"]
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=chunks)

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    m = StreamingReporterModel(
        model="qwen3.8-27b",
        api_key="test-only",
        base_url="https://example.invalid/v1",
        http_async_client=client,
        max_retries=0,
        streaming=True,
        stream_usage=True,
        extra_body={"enable_thinking": False},
        stream_chunk_timeout=1,
        normal_output_timeout=1,
    )
    return m, client, DELIVERY_CONTEXT, {"trace": trace(tmp_path), "action_id": "current-action"}


def sse(delta, finish=None, usage=None):
    return "data: " + json.dumps({"id": "provider-test", "model": "qwen3.8-27b", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], "usage": usage}) + "\n\n"


def test_real_sdk_stream_tool_arguments_and_preview_survive(tmp_path):
    args = json.dumps({"path": "/x.html", "content": "<!DOCTYPE html><html><body>原文</body></html>"}, ensure_ascii=False)
    chunks = (
        sse({"role": "assistant"})
        + sse({"tool_calls": [{"index": 0, "id": "tc", "type": "function", "function": {"name": "write_file", "arguments": args[:20]}}]})
        + sse({"tool_calls": [{"index": 0, "function": {"arguments": args[20:]}}]})
        + sse({}, "tool_calls")
        + "data: [DONE]\n\n"
    ).encode()
    m, client, context, value = model(tmp_path, chunks)

    async def run():
        token = context.set(value)
        try:
            result = await m.ainvoke([HumanMessage("write")])
            assert result.tool_calls[0]["args"]["content"].startswith("<!DOCTYPE html>")
        finally:
            context.reset(token)
            await client.aclose()

    asyncio.run(run())
    previews = list(tmp_path.rglob("*.html.partial"))
    assert len(previews) == 1 and previews[0].read_text().endswith("</html>")
    assert list(tmp_path.rglob("*.delta.jsonl"))
    assert not (tmp_path / "report.html").exists()


def test_reasoning_only_eof_is_error_not_empty_success(tmp_path):
    from app.experiments.collusion_audit.reporter_stream import IncompleteReporterStream

    m, client, context, value = model(tmp_path, sse({"role": "assistant", "reasoning_content": "private not logged"}).encode())

    async def run():
        token = context.set(value)
        try:
            with pytest.raises(IncompleteReporterStream):
                await m.ainvoke([HumanMessage("write")])
        finally:
            context.reset(token)
            await client.aclose()

    asyncio.run(run())
    data = "".join(p.read_text() for p in tmp_path.rglob("*.jsonl"))
    assert "private not logged" not in data
    assert "thinking_chars" in data


def test_text_without_finish_is_retained_but_rejected(tmp_path):
    from app.experiments.collusion_audit.reporter_stream import IncompleteReporterStream

    m, client, context, value = model(tmp_path, sse({"content": "<!DOCTYPE html><html>未完成"}).encode())

    async def run():
        token = context.set(value)
        try:
            with pytest.raises(IncompleteReporterStream):
                await m.ainvoke([HumanMessage("write")])
        finally:
            context.reset(token)
            await client.aclose()

    asyncio.run(run())
    assert any("未完成" in p.read_text() for p in tmp_path.rglob("*.content.partial.txt"))


def test_request_timeout_propagates_once_and_closes_trace(tmp_path):
    from app.experiments.collusion_audit.reporter_delivery import ReporterDeliveryMiddleware

    t = trace(tmp_path)
    mw = ReporterDeliveryMiddleware("current", t, request_timeout=0.01)
    req = ModelRequest(model=SimpleNamespace(), messages=[HumanMessage("task")], system_message=SystemMessage("native"), state={}, runtime=SimpleNamespace())
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(10)

    with pytest.raises(TimeoutError):
        asyncio.run(mw.awrap_model_call(req, handler))
    assert len(calls) == 1
    assert "reporter.request.ended" in (tmp_path / "events.jsonl").read_text()


def test_missing_report_uses_existing_three_attempt_gate():
    from test_sp_scoped_recovery import Request

    from deerflow.sp.agent_tools import SPFinishAvailabilityMiddleware
    from deerflow.sp.memory import TaskMemoryStack

    stack = TaskMemoryStack()
    for i in range(3):
        stack.append_delegate("write HTML", run_id="run", stage="reporting", metadata={"target_agent": "reporter"})
    stack.append_observe("no report", actor="reporter", run_id="run", stage="reporting", metadata={"completion_status": "partial", "evidence_gaps": ["timed out"]})
    state = {"sp_loop_run_id": "run", "sp_task_memory": stack.to_dict(), "sp_current_artifact_refs": {}, "messages": [HumanMessage("write report")], "sp_last_handler_result": {}}
    native = SPFinishAvailabilityMiddleware()._filter_request(Request(state))
    fixed = SPFinishAvailabilityMiddleware(count_missing_reports=True)._filter_request(Request(state))
    assert "sp_delegate" in {x.name for x in native.tools}
    assert {x.name for x in fixed.tools} == {"sp_ask_human"}


def test_empty_chunks_cannot_extend_normal_output_wait(tmp_path):
    from app.experiments.collusion_audit.reporter_delivery import DELIVERY_CONTEXT
    from app.experiments.collusion_audit.reporter_stream import ReporterOutputIdleTimeout, StreamingReporterModel

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(30):
                await asyncio.sleep(0.003)
                yield sse({"reasoning_content": "not normal output"}).encode()

    async def respond(request):
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Body())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            m = StreamingReporterModel(model="qwen3.8-27b", api_key="test", http_async_client=client, max_retries=0, streaming=True, normal_output_timeout=0.02)
            token = DELIVERY_CONTEXT.set({"trace": trace(tmp_path), "action_id": "idle"})
            try:
                with pytest.raises(ReporterOutputIdleTimeout):
                    await m.ainvoke([HumanMessage("test")])
            finally:
                DELIVERY_CONTEXT.reset(token)

    asyncio.run(run())
    status = json.loads(next(tmp_path.rglob("*.status.json")).read_text())
    assert status["status"] == "failed" and status["normal_chars"] == 0
    assert status["elapsed_seconds"] < 0.5


def test_actual_loaded_skill_and_resources_are_html_only(tmp_path, monkeypatch, real_executor_module):
    from pathlib import Path
    from unittest.mock import AsyncMock

    from app.experiments.collusion_audit.profile import CollusionProfile

    # Use the same clean synthetic fixture builder as existing profile regressions.
    from app.experiments.tender_audit.observability import AuditTrace
    from deerflow.skills.types import Skill, SkillCategory
    from deerflow.sp.subagents import SPSubagentTask
    from deerflow.subagents.config import SubagentConfig

    project = tmp_path / "project"
    project.mkdir()
    (project / "manifest.json").write_text(json.dumps({"project_name": "合成采购", "documents": []}))
    rule = {"合并规则ID": "M01", "规则名称": "同源底稿派生", "通用规则内容": "完整规则原文", "证据等级": "文本同源线索"}
    p = CollusionProfile(
        rule,
        project,
        tmp_path / "outputs",
        tmp_path / "cache",
        AuditTrace(tmp_path / "trace"),
        quality_controls=True,
        rule_level_review=True,
        report_format="html",
        reporter_delivery=True,
        reporter_max_tokens=32768,
        reporter_request_timeout=900,
    )
    task = SPSubagentTask("current-action", "reporter", "生成HTML", "完整交付")
    p.prepare_task(task)
    config = p.configure_subagent(task, SubagentConfig("sp-reporter", "reporter", system_prompt="Write Markdown report.", skills=["stackplanner-reporting"]))
    assert config.model_thinking_enabled is False and config.model_errors_propagate
    assert config.model == "aliyun-qwen-reporter-stream"
    assert config.model_max_tokens == 32768
    assert config.skills == ["collusion-reporting-html"]
    executor = real_executor_module.SubagentExecutor(config=config, tools=[], app_config=None)
    skill_dir = Path(__file__).resolve().parents[2] / "skills/public/collusion-reporting-html"
    skill = Skill("collusion-reporting-html", "HTML evidence", None, skill_dir, skill_dir / "SKILL.md", Path("collusion-reporting-html"), SkillCategory.PUBLIC, enabled=True)
    monkeypatch.setattr(executor, "_load_skills", AsyncMock(return_value=[skill]))

    async def run():
        state, tools, setup = await executor._build_initial_state("current task")
        return state["messages"][0].content

    actual = asyncio.run(run())
    assert "Produce clean HTML" in actual and "Produce clean Markdown" not in actual
    assert "report-<action_id>.md" not in actual
    assert '<skill name="collusion-reporting-html">' in actual
    assert "完整规则" in config.system_prompt


def test_streaming_alias_factory_keeps_http_and_payload_limits(monkeypatch):
    from app.experiments.tender_audit.runner import experiment_config
    from deerflow.models import create_chat_model

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-only")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
    cfg = experiment_config("aliyun-qwen3.8-27b")
    cfg.models.append(
        cfg.models[0].model_copy(
            update={
                "name": "aliyun-qwen-reporter-stream",
                "use": "app.experiments.collusion_audit.reporter_stream:StreamingReporterModel",
                "max_tokens": 32768,
                "timeout": httpx.Timeout(connect=10, read=330, write=60, pool=30),
                "stream_chunk_timeout": 300,
                "normal_output_timeout": 300,
            }
        )
    )
    cfg = type(cfg).model_validate(cfg.model_dump())
    actual = create_chat_model(name="aliyun-qwen-reporter-stream", app_config=cfg, thinking_enabled=False, attach_tracing=False)
    payload = actual._get_request_payload([HumanMessage("test")], stream=True, stream_options={"include_usage": True})
    assert payload["max_completion_tokens"] == 32768
    assert payload["extra_body"] == {"enable_thinking": False}
    assert actual.request_timeout.read == 330 and actual.request_timeout.connect == 10
    assert actual.stream_chunk_timeout == 300 and actual.normal_output_timeout == 300
    assert "normal_output_timeout" not in payload
    assert actual.root_async_client.max_retries == 0
