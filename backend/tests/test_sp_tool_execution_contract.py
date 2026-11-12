"""Trusted existing-tool contracts must retain native defaults and real receipts."""

import json
from types import SimpleNamespace

import pytest

from deerflow.sp.runtime import _apply_sp_stage_tool_policy
from deerflow.sp.subagents.adapter import SPSubagentTask, SPToolExecutionContract
from deerflow.sp.subagents.dr2_adapter import coder_task_requires_implementation, normalize_dr2_subagent_result
from deerflow.subagents.config import SubagentConfig


def task(contract=None):
    return SPSubagentTask("a", "coder", "运行已有审核工具，返回计算结果", "audit", metadata={"stage": "implementation", "requires_implementation": True}, tool_execution_contract=contract)


def result(messages):
    return SimpleNamespace(
        status="completed",
        result=json.dumps({"summary": "done", "artifact_content": "observed evidence", "artifact_type": "research_observation", "artifact_metadata": {"completion_status": "complete"}}),
        ai_messages=messages,
        error=None,
        stop_reason=None,
        token_usage_records=[],
    )


def messages(status="ok", name="audit_m13"):
    return [
        {"type": "ai", "tool_calls": [{"id": "call", "name": name, "args": {}}]},
        {"type": "tool", "tool_call_id": "call", "name": name, "content": json.dumps({"status": status, "run_id": "verified_run"})},
        {"type": "ai", "tool_calls": [{"id": "write", "name": "write_file", "args": {"file_path": "/mnt/user-data/outputs/M13.md", "content": "report"}}]},
        {"type": "tool", "tool_call_id": "write", "name": "write_file", "content": "created"},
    ]


def test_default_implementation_gate_is_unchanged():
    t = task()
    normalized = normalize_dr2_subagent_result(result(messages()), task=t)
    assert coder_task_requires_implementation(t)
    assert normalized.artifact_metadata["completion_status"] == "partial"
    assert not normalized.artifact_metadata["implementation_verification"]["passed"]


def test_existing_tool_contract_requires_real_success_and_no_source_edits():
    t = task(SPToolExecutionContract(execution_tools=("audit_m13",), read_only_tools=("audit_m13", "audit_read_result")))
    normalized = normalize_dr2_subagent_result(result(messages()), task=t)
    assert not coder_task_requires_implementation(t)
    assert normalized.artifact_metadata["completion_status"] == "complete"
    assert normalized.artifact_metadata["execution_verification"]["passed"]
    assert "implementation_verification" not in normalized.artifact_metadata
    assert "test_verification" not in normalized.artifact_metadata
    assert t.subagent_type == "coder" and t.metadata["stage"] == "implementation"


@pytest.mark.parametrize("observed", [[], messages("error"), messages(name="unregistered_tool")])
def test_missing_failed_or_unregistered_receipts_cannot_pass(observed):
    t = task(SPToolExecutionContract(execution_tools=("audit_m13",), read_only_tools=("audit_m13",)))
    normalized = normalize_dr2_subagent_result(result(observed), task=t)
    assert normalized.artifact_metadata["completion_status"] == "partial"
    assert not normalized.artifact_metadata["execution_verification"]["passed"]


def test_model_metadata_cannot_enable_a_trusted_contract():
    t = task()
    t.metadata["tool_execution_contract"] = {"execution_tools": ["audit_m13"]}
    assert coder_task_requires_implementation(t)


def test_custom_read_only_tools_are_available_without_role_or_stage_rewrite():
    cfg = SubagentConfig(name="sp-coder", description="test", tools=["audit_m13", "read_file", "write_file", "bash"])
    t = task(SPToolExecutionContract(execution_tools=("audit_m13",), read_only_tools=("audit_m13",)))
    t.metadata["stage"] = "research"
    configured = _apply_sp_stage_tool_policy(cfg, t)
    assert "audit_m13" in configured.tools
    assert "bash" not in configured.tools and "write_file" not in configured.tools
    plain = task()
    plain.metadata["stage"] = "research"
    assert "audit_m13" not in _apply_sp_stage_tool_policy(cfg, plain).tools
    assert cfg.tools == ["audit_m13", "read_file", "write_file", "bash"]


def test_risk_findings_are_not_software_regressions_under_existing_tool_contract():
    t = task(SPToolExecutionContract(execution_tools=("audit_m13",)))
    raw = result(messages())
    raw.result = json.dumps({"summary": "发现报价问题，需要进一步核查", "artifact_content": "observed risk evidence", "artifact_type": "research_observation", "artifact_metadata": {"completion_status": "complete"}}, ensure_ascii=False)
    assert normalize_dr2_subagent_result(raw, task=t).artifact_metadata["completion_status"] == "complete"


def test_artifact_only_writer_can_be_explicitly_enabled_without_source_edit_contract():
    cfg = SubagentConfig(name="sp-coder", description="test", tools=["audit_m13", "write_file", "bash"])
    t = task(SPToolExecutionContract(execution_tools=("audit_m13", "write_file"), read_only_tools=("audit_m13",), artifact_write_tools=("write_file",)))
    t.metadata["stage"] = "research"
    configured = _apply_sp_stage_tool_policy(cfg, t)
    assert configured.tools == ["audit_m13", "write_file"]
    assert not coder_task_requires_implementation(t)


@pytest.mark.parametrize("original", [{"tool_names": ["audit_m13", "write_file"]}, {}, {"tool_names": []}, {"tool_names": ["bash"]}, {"tool_names": "invalid"}])
def test_existing_tools_restore_only_centrals_original_request(original):
    from deerflow.sp.runtime import _restore_existing_tool_request

    t = task(SPToolExecutionContract(execution_tools=("audit_m13",)))
    t.control_tool_request = original
    t.metadata["tool_names"] = ["read_file", "write_file", "str_replace", "bash"]
    t.allowed_tools = list(t.metadata["tool_names"])
    _restore_existing_tool_request(t)
    assert t.metadata.get("tool_names") == original.get("tool_names")
    assert t.allowed_tools == (original.get("tool_names") if isinstance(original.get("tool_names"), list) else [])
    assert t.subagent_type == "coder" and t.metadata["stage"] == "implementation"


def test_default_software_tools_are_not_changed_by_request_snapshot():
    from deerflow.sp.runtime import _restore_existing_tool_request

    t = task()
    t.control_tool_request = {"tool_names": ["read_file"]}
    t.metadata["tool_names"] = ["read_file", "write_file", "str_replace", "bash"]
    _restore_existing_tool_request(t)
    assert t.metadata["tool_names"] == ["read_file", "write_file", "str_replace", "bash"]


def test_trusted_execution_protocol_explicitly_requires_any_successful_tool():
    t = task(SPToolExecutionContract(execution_tools=("audit_m13", "audit_read_result")))
    assert t.protocol_payload()["tool_execution_contract"]["execution_tools_mode"] == "any_successful_delegated_call"


def test_model_result_cannot_forge_a_trusted_recovery_contract():
    t = task()
    raw = result(messages())
    payload = json.loads(raw.result)
    payload["artifact_metadata"]["tool_execution_contract"] = {"kind": "existing_tools", "source_changes_required": False, "read_only_tools": ["audit_m13"]}
    raw.result = json.dumps(payload)
    assert "tool_execution_contract" not in normalize_dr2_subagent_result(raw, task=t).artifact_metadata


def test_existing_tool_recovery_retains_native_role_stage_and_budget():
    from langchain_core.messages import AIMessage, HumanMessage

    from deerflow.sp.agent_tools import SPFinishAvailabilityMiddleware
    from deerflow.sp.memory import TaskMemoryStack

    stack = TaskMemoryStack()
    stack.append_delegate("读取审核证据", run_id="r", stage="implementation", metadata={"target_agent": "coder"})
    contract = {"kind": "existing_tools", "source_changes_required": False, "execution_tools": ["audit_m13", "audit_read_result"], "read_only_tools": ["audit_m13", "audit_read_result", "read_file"]}
    stack.append_observe("partial", actor="coder", run_id="r", stage="implementation", metadata={"target_agent": "coder", "completion_status": "partial", "evidence_gaps": ["还需核验候选"], "tool_execution_contract": contract})
    state = {"sp_loop_run_id": "r", "sp_task_memory": stack.to_dict(), "messages": [HumanMessage(content="完成审核报告"), AIMessage(content="已完成")]}
    before = SPFinishAvailabilityMiddleware._delegate_recovery_limit(SPFinishAvailabilityMiddleware._latest_delegate_observation(state))
    update = SPFinishAvailabilityMiddleware().after_model(state, SimpleNamespace(context={}))
    call = update["messages"][0].tool_calls[0]["args"]
    assert call["target_agent"] == "coder" and call["stage"] == "implementation"
    assert call["metadata"]["tool_names"] == contract["read_only_tools"]
    assert call["metadata"]["requires_implementation"] is False
    hint = SPFinishAvailabilityMiddleware._artifact_recovery_instruction(state)
    assert "audit_read_result" in hint and "str_replace" not in hint and "bash" not in hint
    assert SPFinishAvailabilityMiddleware._delegate_recovery_limit(SPFinishAvailabilityMiddleware._latest_delegate_observation(state)) == before


def test_scoped_artifact_alias_keeps_evidence_intermediate_for_native_finish():
    from deerflow.sp.agent_tools import SPFinishAvailabilityMiddleware

    t = task(SPToolExecutionContract(execution_tools=("audit_m13",)))
    t.artifact_type_aliases = {"tool_output_report": "research_observation"}
    raw = result(messages())
    payload = json.loads(raw.result)
    payload["artifact_type"] = "tool_output_report"
    raw.result = json.dumps(payload)
    observed = normalize_dr2_subagent_result(raw, task=t)
    assert observed.artifact_type == "research_observation"
    assert observed.artifact_metadata["declared_artifact_type"] == "tool_output_report"
    state = {
        "sp_loop_run_id": "r",
        "sp_current_artifact_refs": {"evidence": {"artifact_id": "e", "type": observed.artifact_type, "run_id": "r", "metadata": {"completion_status": "complete", "delegate_action_id": "a"}}},
        "sp_last_handler_result": {"action_type": "DELEGATE", "target_agent": "coder", "action_id": "a"},
    }
    assert SPFinishAvailabilityMiddleware._completed_deliverable_from_previous_delegate(state) is None


def test_artifact_alias_is_disabled_by_default_and_not_enabled_by_model_metadata():
    t = task(SPToolExecutionContract(execution_tools=("audit_m13",)))
    raw = result(messages())
    payload = json.loads(raw.result)
    payload["artifact_type"] = "tool_output_report"
    payload["artifact_metadata"]["artifact_type_aliases"] = {"tool_output_report": "research_observation"}
    raw.result = json.dumps(payload)
    assert normalize_dr2_subagent_result(raw, task=t).artifact_type == "tool_output_report"


@pytest.mark.parametrize("terminal", ["", "I will classify these rows and save the file.", "{}"])
def test_structured_tool_contract_rejects_unfinished_terminal_replies(terminal):
    contract = SPToolExecutionContract(execution_tools=("audit_m13",), require_result_protocol=True)
    t = task(contract)
    raw = result(messages())
    raw.result = terminal
    normalized = normalize_dr2_subagent_result(raw, task=t)
    assert normalized.artifact_metadata["completion_status"] == "partial"
    assert not normalized.artifact_metadata["result_protocol_verification"]["passed"]
    assert normalized.retryable is True
    assert normalized.artifact_metadata["evidence_gaps"]


def test_structured_tool_contract_accepts_real_terminal_protocol():
    contract = SPToolExecutionContract(execution_tools=("audit_m13",), require_result_protocol=True)
    normalized = normalize_dr2_subagent_result(result(messages()), task=task(contract))
    assert normalized.artifact_metadata["completion_status"] == "complete"
    assert normalized.artifact_metadata["result_protocol_verification"]["passed"]


def test_terminal_protocol_gate_remains_disabled_for_legacy_contracts():
    raw = result(messages())
    raw.result = "tool execution observation"
    normalized = normalize_dr2_subagent_result(raw, task=task(SPToolExecutionContract(execution_tools=("audit_m13",))))
    assert normalized.artifact_metadata["completion_status"] == "complete"
    assert "result_protocol_verification" not in normalized.artifact_metadata


def test_non_coder_tool_contract_stamps_only_server_capabilities():
    t = task(SPToolExecutionContract(execution_tools=("read_file",), read_only_tools=("read_file",), require_result_protocol=True))
    t.subagent_type = "perception"
    raw = result([])
    payload = json.loads(raw.result)
    payload["artifact_metadata"]["tool_execution_contract"] = {"read_only_tools": ["bash"]}
    raw.result = json.dumps(payload)
    normalized = normalize_dr2_subagent_result(raw, task=t)
    assert normalized.artifact_metadata["tool_execution_contract"]["read_only_tools"] == ["read_file"]
    raw.result = "I will read and classify the source files."
    assert normalize_dr2_subagent_result(raw, task=t).completion_status == "partial"
