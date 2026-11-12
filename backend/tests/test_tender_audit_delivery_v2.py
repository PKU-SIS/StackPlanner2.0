"""Delivery contracts: evidence quality is assessed offline, never repaired."""

import json
from types import SimpleNamespace

import pytest

from app.experiments.tender_audit.cases import AuditCase, validate_result
from app.experiments.tender_audit.observability import AuditTrace
from app.experiments.tender_audit.profile import AuditProfile
from app.experiments.tender_audit.ragflow import RagflowClient, RagflowSettings
from deerflow.sp.actions.handlers.delegate import _has_no_data_report_constraint, _unsupported_no_data_report_claims
from deerflow.sp.agent_tools import SPFinishAvailabilityMiddleware
from deerflow.sp.artifacts.adapter import _serialize_content
from deerflow.sp.runtime import DR2SPExecutorProvider
from deerflow.sp.subagents import SPSubagentTask
from deerflow.subagents.builtins.sp_specialists import REPORTER_CONFIG


@pytest.fixture
def case():
    return AuditCase(1, "示例", "bound", ("规则",), "规则正文保持原样", "检索")


def result(**updates):
    return {"case_id": 1, "pass": False, "needs_review": False, "score": 0.8, "conclusion": "冲突", "reference": "证据说明", "evidence": [{"chunk_id": "unverified-id", "quote": "表格内容的概括", "position": "位置"}], **updates}


def test_delivery_does_not_claim_to_validate_evidence_quality(case):
    value = result()
    validation = validate_result(json.dumps(value), case, [{"id": "other", "content": "不相同的原文"}])
    assert validation["valid"] and validation["schema_valid"]
    assert validation["evidence_evaluated"] is False
    assert validation["evidence_evaluation"] == "offline_only"
    assert "evidence_valid" not in validation
    assert validation["parsed_result"] == value
    assert validate_result(json.dumps(result(evidence=[])), case, [])["valid"]


@pytest.mark.parametrize("updates", [{"score": None}, {"score": "unknown"}, {"score": 95}, {"pass": "false"}, {"note": "extra"}, {"case_id": 2}])
def test_delivery_remains_strict_about_format(case, updates):
    assert not validate_result(json.dumps(result(**updates)), case, [])["valid"]


def test_delivery_never_repairs_invalid_json(case):
    raw = json.dumps(result(), ensure_ascii=False)
    assert not validate_result("```json\n" + raw + "\n```", case, [])["json_valid"]
    assert not validate_result(raw.replace("冲突", r"10\%"), case, [])["json_valid"]
    value = result(evidence=[{"chunk_id": "c", "quote": r"扣除10\%", "position": ""}])
    assert json.loads(_serialize_content(value)) == value


def test_central_receives_the_same_rules_and_output_contract(case):
    text = case.user_input()
    assert case.rule_content in text
    assert "score" in text and "置信度" in text
    assert "0–1" in text
    assert "artifact_content" in text
    assert not _has_no_data_report_constraint(SPSubagentTask("a", "reporter", "合成", "审核", context_refs={"latest_user_input": text}))


@pytest.mark.parametrize(
    "text", ["规则已给定、数据集已绑定，无需询问输出格式或数据来源。", "无需提供额外数据，已有材料足够。", "报价按无效处理，核对技术指标。", "若用户未提供实际数据，应标记未知。", "If actual data were not provided, use unknowns."]
)
def test_native_no_data_guard_does_not_confuse_instructions_with_missing_data(text):
    task = SPSubagentTask("a", "reporter", "生成报告", "测试", context_refs={"latest_user_input": text})
    assert not _has_no_data_report_constraint(task)
    assert not _unsupported_no_data_report_claims('{"reference":"最高限价3387300元","score":0.95}', task=task)


@pytest.mark.parametrize("text", ["目前我还没有给你实际数值，也不允许联网或虚构数据。请生成报告。", "用户未提供实际业务数据，请用待填模板。", "没有实际数据。", "No actual values were provided. Create a report."])
def test_native_no_data_guard_still_rejects_unsupported_claims(text):
    task = SPSubagentTask("a", "reporter", "生成报告", "测试", context_refs={"latest_user_input": text})
    assert _has_no_data_report_constraint(task)
    assert _unsupported_no_data_report_claims("The West Region had the highest churn rate.", task=task)


def test_native_diagnostic_names_the_actual_trigger_in_long_json():
    task = SPSubagentTask("a", "reporter", "生成报告", "测试", context_refs={"latest_user_input": "No actual data were provided."})
    output = json.dumps({"score": 0.95, "reference": "很长的前文" * 200 + "最高限价3387300元"}, ensure_ascii=False)
    claims = _unsupported_no_data_report_claims(output, task=task)
    assert len(claims) == 1 and "最高" in claims[0] and len(claims[0]) <= 320


@pytest.mark.parametrize("filename,expected", [("audit.json", "JSON object"), ("report.md", "Markdown")])
def test_native_recovery_respects_declared_artifact_format(filename, expected):
    state = {
        "sp_current_artifact_refs": {"report_revision": {"artifact_id": "report", "type": "report_revision", "is_current": True, "metadata": {"completion_status": "partial", "filename": filename, "evidence_gaps": ["missing evidence"]}}}
    }
    instruction = SPFinishAvailabilityMiddleware._artifact_recovery_instruction(state)
    assert f"complete revised {expected} in artifact_content" in instruction


def test_reporter_packet_deduplicates_before_budgeting_and_retains_readable_sources(tmp_path, case, monkeypatch):
    monkeypatch.setattr("app.experiments.tender_audit.profile.REPORTER_EVIDENCE_MAX_CHARS", 200)
    trace = AuditTrace(tmp_path / "trace")
    rag = RagflowClient(RagflowSettings("https://rag.test", "key"), case.dataset_id, trace)
    large = {"id": "large", "content": "X" * 1000}
    small = {"id": "small", "content": "原始内容"}
    rag.chunks = [large, large, small, small]
    profile = AuditProfile(case, rag, trace, tmp_path / "outputs")
    task = SPSubagentTask("a", "reporter", "生成", "测试")
    profile.prepare_task(task)
    bundle = task.context_refs["audit_retrieved_evidence"]
    assert bundle["total_retrieved_chunks"] == 4
    assert bundle["unique_retrieved_chunks"] == 2
    assert [c["id"] for c in bundle["items"]] == ["small"]
    assert bundle["truncated"]
    assert {c["id"] for c in bundle["source_manifest"]} == {"large", "small"}
    for source in bundle["source_manifest"]:
        cached = json.loads(profile._path(source["read_path"]).read_text())
        assert cached["id"] == source["id"]
    copied = profile.configure_subagent(task, REPORTER_CONFIG)
    assert copied.tools == ["read_file"]
    assert "JSON 对象" in copied.system_prompt
    assert REPORTER_CONFIG.tools != copied.tools


def test_native_reporter_tool_defaults_are_narrowed_before_runtime_enforcement(tmp_path, case, monkeypatch):
    """DelegateHandler adds write_file even when Central never requested it."""
    captured = {}
    trace = AuditTrace(tmp_path / "trace")
    profile = AuditProfile(case, RagflowClient(RagflowSettings("https://rag.test", "key"), "bound", trace), trace, tmp_path / "outputs")

    class Executor:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def execute(self, prompt):
            return SimpleNamespace(
                status="completed",
                result=json.dumps({"summary": "done", "artifact_content": result(), "artifact_type": "report_revision", "artifact_metadata": {"completion_status": "complete"}}),
                error=None,
                stop_reason=None,
                token_usage_records=[],
            )

    monkeypatch.setattr("deerflow.subagents.get_subagent_config", lambda *args, **kwargs: REPORTER_CONFIG)
    monkeypatch.setattr("deerflow.subagents.SubagentExecutor", Executor)
    task = SPSubagentTask("a", "reporter", "合成JSON", "测试", allowed_tools=["read_file", "write_file"], metadata={"tool_names": ["read_file", "write_file"]})
    provider = DR2SPExecutorProvider(app_config=SimpleNamespace(), parent_model="qwen", runnable_config={}, extensions=profile.extensions())
    outcome = provider({}, SimpleNamespace(context={"thread_id": "t", "run_id": "r"}, stream_writer=None)).execute(task)
    assert outcome.is_success
    assert task.allowed_tools == ["read_file"]
    assert captured["config"].tools == ["read_file"]
