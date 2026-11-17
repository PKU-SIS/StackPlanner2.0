"""Regression for a real no-tools Reporter loop; no output/JSON repair."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from test_collusion_audit import scoped_profile as scoped_profile


def request(messages, tools=None):
    def override(**updates):
        value = {"messages": messages, "tools": [] if tools is None else tools, "state": {}, "model_settings": {}}
        value.update(updates)
        return SimpleNamespace(**value, override=override)

    return override()


def setup_projection(tmp_path):
    from app.experiments.collusion_audit.postwrite import PostWriteProtocolProjection

    outputs = tmp_path / "outputs"
    outputs.mkdir()
    body = '<!doctype html><html lang="zh-CN"><body><p>实际证据正文与未知范围</p></body></html>'
    local = outputs / "M07-report-current.html"
    local.write_text(body)
    virtual = "/mnt/user-data/outputs/" + local.name
    events = []
    saved = []
    trace = SimpleNamespace(event=lambda kind, **fields: events.append((kind, fields)), append=lambda name, row: saved.append((name, row)))
    projection = PostWriteProtocolProjection("current", outputs, virtual, "原始证据类别", trace=trace)
    return projection, body, local, virtual, events, saved


def written_messages(virtual):
    return [
        SystemMessage("原生子代理系统约束保持可见"),
        HumanMessage("前一个阶段要求写入并检查报告"),
        AIMessage(content="", tool_calls=[{"id": "write", "name": "write_file", "args": {"path": virtual, "content": "past body"}}]),
        ToolMessage(json.dumps({"created": virtual, "status": "ok"}), name="write_file", tool_call_id="write"),
        HumanMessage("本次文件已创建，仅需返回真实阶段协议", name="audit_report_created_notice"),
    ]


def test_successful_postwrite_request_contains_full_html_and_true_receipt_without_tool_history(tmp_path):
    projection, body, local, virtual, events, saved = setup_projection(tmp_path)
    original = request(written_messages(virtual))
    result = projection.project(original)
    assert all(not isinstance(m, (AIMessage, ToolMessage)) for m in result.messages)
    text = "\n".join(str(m.content) for m in result.messages)
    assert body in text
    assert hashlib.sha256(local.read_bytes()).hexdigest() in text
    assert virtual in text and "原始证据类别" in text and "原生子代理系统约束保持可见" in text
    assert result.tools == [] and result.model_settings["tool_choice"] == "none"
    assert original.messages[2].tool_calls[0]["name"] == "write_file"
    assert local.read_text() == body
    assert events and saved


@pytest.mark.parametrize("condition", ["not_written", "wrong_scope", "write_error", "tools_still_enabled"])
def test_projection_never_claims_unverified_or_wrong_scope_write(tmp_path, condition):
    projection, body, local, virtual, events, saved = setup_projection(tmp_path)
    messages = written_messages(virtual)
    tools = []
    if condition == "not_written":
        messages = messages[:2]
    if condition == "wrong_scope":
        messages = written_messages("/mnt/user-data/outputs/M07-report-other.html")
    if condition == "write_error":
        messages[3] = ToolMessage(json.dumps({"created": virtual, "status": "error"}), name="write_file", tool_call_id="write", status="error")
    if condition == "tools_still_enabled":
        tools = [SimpleNamespace(name="read_file")]
    original = request(messages, tools)
    result = projection.project(original)
    assert result.messages == original.messages
    assert not saved


def test_postwrite_projection_retains_real_pending_limits_and_does_not_fill_result_json(tmp_path):
    projection, body, local, virtual, events, saved = setup_projection(tmp_path)
    body += "\n<p>核验未完成，覆盖不全，不得视为全部排除。</p>"
    local.write_text(body)
    result = projection.project(request(written_messages(virtual)))
    assert body in "\n".join(str(m.content) for m in result.messages)
    assert local.read_text() == body
    assert not hasattr(projection, "repair_json")


def test_projection_cannot_read_a_path_outside_current_outputs(tmp_path):
    from app.experiments.collusion_audit.postwrite import PostWriteProtocolProjection

    with pytest.raises(ValueError):
        PostWriteProtocolProjection("current", tmp_path, "/mnt/user-data/outputs/../foreign.html", "grade")


@pytest.mark.parametrize("enabled", [False, True])
def test_perception_uses_same_explicit_evidence_deadline_only_in_general_quality(scoped_profile, tmp_path, enabled):
    from app.experiments.collusion_audit.profile import CollusionProfile
    from deerflow.sp.subagents import SPSubagentTask
    from deerflow.subagents.config import SubagentConfig

    p = CollusionProfile(
        scoped_profile.rule,
        scoped_profile.project,
        scoped_profile.outputs,
        tmp_path / "cache",
        scoped_profile.trace,
        quality_controls=True,
        rule_level_review=True,
        general_quality=enabled,
        reporter_max_tokens=32768,
        reporter_request_timeout=900,
        evidence_role_capacity=True,
        evidence_max_tokens=16384,
        evidence_request_timeout=960,
    )
    task = SPSubagentTask("current", "perception", "Read current sources", "local image check")
    configured = p.configure_subagent(task, SubagentConfig("sp-perception", "native"))
    assert configured.model_timeout_seconds == (960 if enabled else None)
    assert configured.model_request_deadline_seconds == (960 if enabled else None)
    assert configured.model_max_tokens == (16384 if enabled else None)


def test_original_delegation_requirements_survive_closing_projection(tmp_path):
    projection, body, local, virtual, events, saved = setup_projection(tmp_path)
    frame = {
        "action_id": "current",
        "task": "仅修订已知字段，仍须披露数据局限",
        "expected_output": "原生阶段结果",
        "context_refs": {"report_requirements": {"human_feedback": "不可把未知写成已排除"}, "audit_report_contract": {"path": virtual, "format": "html"}},
    }
    messages = written_messages(virtual)
    messages.insert(1, HumanMessage("<sp-subagent-task>\n" + json.dumps(frame, ensure_ascii=False)))
    result = projection.project(request(messages))
    content = "\n".join(str(m.content) for m in result.messages)
    assert "仅修订已知字段，仍须披露数据局限" in content
    assert "不可把未知写成已排除" in content
    assert result.messages[-1].name == "audit_postwrite_return_contract"


def test_native_json_request_mode_is_only_an_explicit_postwrite_option(tmp_path):
    projection, body, local, virtual, events, saved = setup_projection(tmp_path)
    source = request(written_messages(virtual))
    assert "response_format" not in projection.project(source).model_settings
    projection.json_mode = True
    projected = projection.project(source)
    assert projected.model_settings["response_format"] == {"type": "json_object"}
    assert local.read_text() == body


def test_delivery_returns_even_malformed_model_content_unchanged(tmp_path):
    import asyncio

    from app.experiments.collusion_audit.reporter_delivery import ReporterDeliveryMiddleware
    from app.experiments.tender_audit.observability import AuditTrace

    projection, body, local, virtual, events, saved = setup_projection(tmp_path)
    delivery = ReporterDeliveryMiddleware("current", AuditTrace(tmp_path / "trace"), request_timeout=1)
    delivery.postwrite_projection = projection
    model_result = SimpleNamespace(result=[AIMessage(content="malformed model result")])
    calls = []

    async def handler(value):
        calls.append(value)
        assert body in "\n".join(str(m.content) for m in value.messages)
        return model_result

    returned = asyncio.run(delivery.awrap_model_call(request(written_messages(virtual)), handler))
    assert returned is model_result
    assert returned.result[0].content == "malformed model result"
    assert len(calls) == 1 and local.read_text() == body


def test_dynamic_coverage_metadata_keeps_complete_source_context_as_inert_data(tmp_path):
    from app.experiments.collusion_audit.reporter_delivery import expand_frame

    projection, body, local, virtual, events, saved = setup_projection(tmp_path)
    projection.retain_source_context = True
    messages = written_messages(virtual)
    messages.insert(1, HumanMessage("真实判读候选为[3,4]；不同页数为7；未看候选5仍待核", name="current_code_facts"))
    result = projection.project(request(messages))
    source = next(m for m in result.messages if m.name == "audit_postwrite_source_context")
    unpacked = expand_frame(json.loads(source.content.split("\n", 1)[1]))
    assert unpacked["prior_messages"] == [m.model_dump(mode="json") for m in messages if not isinstance(m, SystemMessage)]
    assert all(not isinstance(m, (AIMessage, ToolMessage)) for m in result.messages)
    assert body in "\n".join(str(m.content) for m in result.messages)
