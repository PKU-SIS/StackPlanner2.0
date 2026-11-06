"""Public output shapes reject failed prose without selecting answer content."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.sp.agent_tools import SPFinishAvailabilityMiddleware


def _contract(schema, mode="json"):
    return {"output_parse_mode": mode, "output_schema": schema}


@pytest.mark.parametrize("text", ["Audit failed.", '[{"error":"unread evidence"}]', '[""]'])
def test_invalid_public_array_starts_native_bounded_recovery(text):
    state = {
        "sp_task_contract": _contract({"type": "array", "items": {"type": "string", "minLength": 1}}),
        "sp_loop_run_id": "run-1",
        "messages": [HumanMessage(content="Audit sources. Return JSON strings."), AIMessage(content=text, id="answer")],
    }
    update = SPFinishAvailabilityMiddleware().after_model(state, None)
    call = update["messages"][0].tool_calls[0]
    assert call["name"] == "sp_delegate"
    assert call["args"]["target_agent"] == "reporter"
    assert "evidence" in call["args"]["task"]
    assert update["messages"][0].additional_kwargs["stackplanner"]["status"] == "recovery_required"


@pytest.mark.parametrize("text", ["[]", '["Exact observed finding"]'])
def test_format_valid_array_is_not_rewritten(text):
    state = {"sp_task_contract": _contract({"type": "array", "items": {"type": "string", "minLength": 1}}), "messages": [AIMessage(content=text, id="answer")]}
    assert SPFinishAvailabilityMiddleware().after_model(state, None) is None


def test_contract_validation_is_strict_and_preserves_legitimate_zero():
    from deerflow.sp.output_contract import output_contract_gaps

    schema = {"type": "object", "required": ["extracted_value", "calculated_value"], "additionalProperties": False, "properties": {key: {"type": "string", "minLength": 1} for key in ("extracted_value", "calculated_value")}}
    state = {"sp_task_contract": _contract(schema)}
    assert output_contract_gaps(state, '{"extracted_value":"0","calculated_value":"0"}') == []
    for text in ['{"extracted_value":"0"}', '{"extracted_value":0,"calculated_value":0}', "```json\n{}\n```", '{"extracted_value":"0","extracted_value":"1","calculated_value":"0"}']:
        assert output_contract_gaps(state, text)


def test_text_enum_requires_exact_raw_public_label():
    from deerflow.sp.output_contract import output_contract_gaps

    state = {"sp_task_contract": _contract({"type": "string", "enum": ["Reversal", "Inappropriateness", "CombinationErr"]}, "text")}
    assert output_contract_gaps(state, "Reversal") == []
    assert output_contract_gaps(state, '"Reversal"')
    assert output_contract_gaps(state, "The answer is Reversal.")


def test_public_schema_audit_preserves_original_quotes_without_prose_report_heuristics():
    import json
    from types import SimpleNamespace

    from deerflow.sp.actions.handlers.base import HandlerContext
    from deerflow.sp.actions.handlers.delegate import _apply_report_acceptance_checks
    from deerflow.sp.memory import TaskMemoryStack
    from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus, SPSubagentTask

    contract = _contract({"type": "array", "items": {"type": "object", "required": ["text", "explanation"]}})
    contract["source_requirements"] = {"audit_agent": "reporter", "files": []}
    answer = [{"text": "The company records the highest revenue, below the 5% threshold.", "explanation": "The source contradicts the supplied definitions."}]
    result = SPSubagentResult(status=SPSubagentStatus.COMPLETED, artifact_content=json.dumps(answer), artifact_metadata={"completion_status": "complete"})
    task = SPSubagentTask(
        action_id="spact_schema123",
        subagent_type="reporter",
        task="Audit the original document",
        description="Audit",
        context_refs={"latest_user_input": "请保留完整英文原句，只输出指定JSON。", "report_requirements": {"required_literal_tokens": ["THE_REAL_ID"]}},
    )
    _apply_report_acceptance_checks(result, task=task, context=HandlerContext(state={"sp_task_contract": contract}, stack=TaskMemoryStack()), artifact_adapter=SimpleNamespace())
    assert result.artifact_metadata["completion_status"] == "complete"
    checks = result.artifact_metadata["quality_checks"]
    assert checks["public_output_schema"]["passed"]
    assert not {"required_literal_tokens", "threshold_consistency", "no_data_grounding", "language_alignment"}.intersection(checks)


def test_report_schema_uses_actual_file_not_duplicate_inline_preview():
    from types import SimpleNamespace

    from deerflow.sp.actions.handlers.base import HandlerContext
    from deerflow.sp.actions.handlers.delegate import _apply_report_acceptance_checks
    from deerflow.sp.memory import TaskMemoryStack
    from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus, SPSubagentTask

    state = {"sp_task_contract": _contract({"type": "array", "items": {"type": "string"}})}
    task = SPSubagentTask(action_id="spact_call_contract123", subagent_type="reporter", task="Return JSON array", description="Format")
    for file_content, passes in [("[]", True), ("Failed to read evidence", False)]:
        result = SPSubagentResult(
            status=SPSubagentStatus.COMPLETED, result="Report produced", artifact_content="[]", artifact_metadata={"created_paths": ["/mnt/user-data/outputs/report-spact_call_contract123.json"], "completion_status": "complete"}
        )
        _apply_report_acceptance_checks(result, task=task, context=HandlerContext(state=state, stack=TaskMemoryStack()), artifact_adapter=SimpleNamespace(read_text_artifact=lambda *args, **kwargs: (file_content, False)))
        assert result.artifact_metadata["quality_checks"]["public_output_schema"]["passed"] is passes
        assert (result.artifact_metadata["completion_status"] == "complete") is passes


@pytest.mark.parametrize("inline", ["[]", None])
def test_unreadable_model_declared_path_does_not_hide_real_inline_schema_delivery(inline):
    from types import SimpleNamespace

    from deerflow.sp.actions.handlers.base import HandlerContext
    from deerflow.sp.actions.handlers.delegate import _apply_report_acceptance_checks
    from deerflow.sp.memory import TaskMemoryStack
    from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus, SPSubagentTask

    state = {"sp_task_contract": _contract({"type": "array", "items": {"type": "string"}})}
    task = SPSubagentTask(action_id="spact_call_current123", subagent_type="reporter", task="Deliver JSON array", description="Format")
    phantom = "/mnt/user-data/outputs/clause_answer-spact_call_current123.json"
    result = SPSubagentResult(status=SPSubagentStatus.COMPLETED, result="Done", artifact_content=inline, artifact_metadata={"created_paths": [phantom], "completion_status": "complete"})
    _apply_report_acceptance_checks(result, task=task, context=HandlerContext(state=state, stack=TaskMemoryStack()), artifact_adapter=SimpleNamespace(read_text_artifact=lambda *args, **kwargs: None))
    assert result.artifact_metadata["created_paths"] == []
    checks = result.artifact_metadata["quality_checks"]
    assert checks["created_path_ownership"]["discarded_paths"] == [phantom]
    assert checks["public_output_schema"]["passed"] is (inline is not None)
    assert (result.artifact_metadata["completion_status"] == "complete") is (inline is not None)


def test_finish_handler_rejects_bad_schema_without_accepting_finish():
    from deerflow.sp.actions import SPAction
    from deerflow.sp.actions.handlers.base import HandlerContext
    from deerflow.sp.actions.handlers.finish import FinishHandler
    from deerflow.sp.memory import TaskMemoryStack

    state = {"sp_task_contract": _contract({"type": "array", "items": {"type": "string"}})}
    result = FinishHandler().handle(SPAction.create("FINISH", reason="Answer", task="Audit failed", metadata={"allow_without_artifact": True}), HandlerContext(state=state, stack=TaskMemoryStack()))
    assert result.next_step == "error_recoverable"
    assert result.run_events[0]["payload"]["reason"] == "public_output_contract"


def test_schema_propagates_through_bounded_public_task_context():
    from deerflow.sp.actions.handlers.context import _bounded_task_contract

    contract = _contract({"type": "array", "items": {"type": "string"}})
    contract["delivery_mode"] = "inline"
    assert _bounded_task_contract(contract)["output_schema"] == contract["output_schema"]
    assert _bounded_task_contract(contract)["delivery_mode"] == "inline"


@pytest.mark.parametrize("role", ["reporter", "researcher"])
def test_failed_audit_cannot_end_as_format_valid_empty_array(role):
    from deerflow.sp.memory import TaskMemoryStack

    stack = TaskMemoryStack()
    stack.append_delegate("Audit", run_id="run-1", metadata={"target_agent": role})
    stack.append_observe("Audit incomplete", actor=role, run_id="run-1", metadata={"completion_status": "partial", "evidence_gaps": ["Required source remains unread"]})
    state = {
        "sp_task_contract": _contract({"type": "array", "items": {"type": "string"}}),
        "sp_task_memory": stack.to_dict(),
        "sp_loop_run_id": "run-1",
        "sp_non_interactive": True,
        "messages": [HumanMessage(content="Audit sources. Return JSON array."), AIMessage(content="[]", id="answer")],
    }
    update = SPFinishAvailabilityMiddleware().after_model(state, None)
    call = update["messages"][0].tool_calls[0]
    assert call["name"] == "sp_delegate"
    assert call["args"]["target_agent"] == role
    assert "Required source remains unread" in call["args"]["task"]
