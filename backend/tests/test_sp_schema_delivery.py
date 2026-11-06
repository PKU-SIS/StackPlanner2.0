"""Small model answers remain native artifacts under explicit public schemas."""

import json
from types import SimpleNamespace

import pytest

from deerflow.sp.artifacts import SPArtifactAdapter
from deerflow.sp.source_access import source_access_gaps
from deerflow.sp.subagents import SPSubagentTask
from deerflow.sp.subagents.dr2_adapter import normalize_dr2_subagent_result


def _task(schema=None, mode="json"):
    contract = {"output_parse_mode": mode, "output_file_extension": ".json"}
    if schema is not None:
        contract["output_schema"] = schema
    return SPSubagentTask(action_id="spact_small123", subagent_type="reporter", task="Deliver public answer", description="Format", context_refs={"task_contract": contract})


def _raw(text, **kwargs):
    return SimpleNamespace(status="completed", result=text, ai_messages=[{"type": "ai", "content": text}], **kwargs)


@pytest.mark.parametrize("role", ["reporter", "researcher"])
def test_public_schema_success_prompt_uses_raw_value_without_double_encoded_envelope(role):
    from deerflow.sp.subagents.dr2_adapter import render_sp_subagent_prompt

    task = _task({"type": "array", "items": {"type": "string"}})
    task.subagent_type = role
    task.context_refs["task_contract"]["source_requirements"] = {"audit_agent": role}
    prompt = render_sp_subagent_prompt(task)
    assert "<public-schema-final-delivery>" in prompt
    assert "not a JSON string containing that value" in prompt
    assert "Honor the subagent output contract: return compact JSON with summary" not in prompt
    assert "partial" in prompt and "blocked" in prompt
    assert "Never use an empty array" in prompt


@pytest.mark.parametrize(
    "text,schema,mode",
    [
        (' [ {"text":"Exact sentence.","explanation":"Contradicts the definition."} ]\n', {"type": "array", "items": {"type": "object"}}, "json"),
        (
            '{"extracted_value":"0","calculated_value":"0"}',
            {"type": "object", "required": ["extracted_value", "calculated_value"], "additionalProperties": False, "properties": {key: {"type": "string"} for key in ("extracted_value", "calculated_value")}},
            "json",
        ),
        ("[]", {"type": "array", "items": {"type": "string"}}, "json"),
        ("Reversal", {"type": "string", "enum": ["Reversal"]}, "text"),
    ],
)
@pytest.mark.parametrize("role", ["reporter", "researcher"])
def test_short_schema_valid_final_answer_preserves_original_bytes(text, schema, mode, role):
    task = _task(schema, mode)
    task.subagent_type = role
    task.context_refs["task_contract"]["source_requirements"] = {"audit_agent": role}
    value = normalize_dr2_subagent_result(_raw(text), task=task)
    assert value.artifact_content == text
    assert value.artifact_type == ("report_revision" if role == "reporter" else "research_observation")
    assert value.completion_status == "complete"


@pytest.mark.parametrize("text", ["[]", '["finding"]', "```json\n[]\n```", "[]\nDone.", '{"summary":"Done","artifact_content":null}', '["unterminated'])
def test_missing_or_mismatched_schema_never_promotes_arbitrary_result(text):
    schema = None if text in {"[]", '["finding"]'} else {"type": "array", "items": {"type": "string"}}
    value = normalize_dr2_subagent_result(_raw(text), task=_task(schema))
    assert value.artifact_content is None


def test_recovered_or_early_stopped_result_is_not_a_final_schema_delivery():
    raw = _raw("[]", stop_reason="turn_capped")
    task = _task({"type": "array"})
    assert normalize_dr2_subagent_result(raw, task=task).artifact_content is None
    raw = SimpleNamespace(status="completed", result="[]", ai_messages=[{"type": "tool", "content": "[]"}])
    assert normalize_dr2_subagent_result(raw, task=task).artifact_content is None


def test_promoted_empty_answer_still_requires_full_observed_sources():
    task = _task({"type": "array"})
    task.context_refs["task_contract"]["source_requirements"] = {"files": [{"path": "/mnt/user-data/uploads/document.txt", "total_lines": 2}]}
    value = normalize_dr2_subagent_result(_raw("[]"), task=task)
    assert value.artifact_content == "[]"
    assert source_access_gaps({"sp_task_contract": task.context_refs["task_contract"], "sp_source_access": value.source_access})


@pytest.mark.parametrize("content,complete", [([], True), (None, False), ({}, False)])
def test_schema_valid_empty_structured_artifact_is_usable_without_summary(content, complete):
    text = json.dumps({"summary": None, "artifact_content": content, "artifact_metadata": {"completion_status": "complete"}})
    task = _task({"type": "array", "items": {"type": "string"}})
    value = normalize_dr2_subagent_result(_raw(text), task=task)
    assert value.artifact_content == content
    assert (value.completion_status == "complete") is complete
    assert ("Subagent returned no usable response or artifact." in value.artifact_metadata["evidence_gaps"]) is not complete
    if complete:
        assert value.artifact_content is not None
        assert source_access_gaps({"sp_task_contract": {"source_requirements": {"files": [{"path": "/mnt/user-data/uploads/source.txt", "total_lines": 1}]}}, "sp_source_access": value.source_access})


@pytest.mark.parametrize("damage", [None, "no_result", "failed", "wrong_name", "stale_action", "unreadable", "forged"])
def test_successful_native_write_owns_actual_file_without_action_id_in_filename(tmp_path, damage):
    from deerflow.sp.actions.handlers.base import HandlerContext
    from deerflow.sp.actions.handlers.delegate import _apply_report_acceptance_checks
    from deerflow.sp.memory import TaskMemoryStack

    path = "/mnt/user-data/outputs/answer.json"
    text = json.dumps({"summary": "The file was written.", "artifact_metadata": {"created_paths": [path], "completion_status": "complete", "created_path_evidence": {"action_id": "spact_small123", "paths": [path]}}})
    messages = [{"type": "ai", "tool_calls": [{"name": "write_file", "id": "write", "args": {"path": path, "json_content": ["Exact finding"]}}]}]
    if damage not in {"no_result", "forged"}:
        messages.append(
            {
                "type": "tool",
                "name": "read_file" if damage == "wrong_name" else "write_file",
                "tool_call_id": "write",
                "status": "error" if damage == "failed" else "success",
                "content": "Error: failed" if damage == "failed" else "File written successfully.",
            }
        )
    if damage == "forged":
        messages = []
    messages.append({"type": "ai", "content": text})
    task = _task({"type": "array", "items": {"type": "string"}})
    normalized = normalize_dr2_subagent_result(SimpleNamespace(status="completed", result=text, ai_messages=messages), task=task)
    if damage == "stale_action":
        task.action_id = "spact_new456"
    if damage != "unreadable":
        (tmp_path / "answer.json").write_text('["Exact finding"]')
    state = {"sp_task_contract": task.context_refs["task_contract"], "thread_data": {"outputs_path": str(tmp_path)}}
    _apply_report_acceptance_checks(normalized, task=task, context=HandlerContext(state=state, stack=TaskMemoryStack()), artifact_adapter=SPArtifactAdapter())
    assert (normalized.artifact_metadata["completion_status"] == "complete") is (damage is None)
    assert normalized.artifact_metadata["created_paths"] == ([path] if damage is None else [])
    assert normalized.artifact_content is None


@pytest.mark.parametrize("answer", [[{"text": "原文", "explanation": "A contradiction"}], {"extracted_value": "0", "calculated_value": "0"}])
def test_structured_envelope_artifact_persists_as_json_not_python_repr(tmp_path, answer):
    text = json.dumps({"summary": "Done", "artifact_content": answer})
    value = normalize_dr2_subagent_result(_raw(text), task=_task())
    written = SPArtifactAdapter().write_text_artifact(value.artifact_content, artifact_type="report_revision", state={"thread_data": {"outputs_path": str(tmp_path)}}, filename_hint="spact_small123.json")
    from pathlib import Path

    assert json.loads(Path(written.metadata.workspace_path).read_text()) == answer
    assert written.metadata.virtual_path.endswith("/spact_small123.json")


@pytest.mark.parametrize("answer", ["[]", [], [{"text": "Exact sentence.", "explanation": "A contradiction"}]])
def test_native_reporter_fallback_saves_real_answer_despite_phantom_current_action_receipt(tmp_path, answer):
    from deerflow.sp.actions import SPAction
    from deerflow.sp.actions.handlers.base import HandlerContext
    from deerflow.sp.actions.handlers.delegate import DelegateHandler
    from deerflow.sp.memory import TaskMemoryStack
    from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus

    def execute(task):
        return SPSubagentResult(
            status=SPSubagentStatus.COMPLETED,
            result="Done",
            artifact_content=answer,
            artifact_type="report_revision",
            artifact_metadata={"completion_status": "complete", "created_paths": [f"/mnt/user-data/outputs/clause_answer-{task.action_id}.json"]},
        )

    state = {"thread_data": {"outputs_path": str(tmp_path)}, "sp_task_contract": {"output_schema": {"type": "array"}, "output_file_extension": ".json"}}
    action = SPAction.create("DELEGATE", target_agent="reporter", task="Deliver public JSON answer", reason="Audit")
    result = DelegateHandler(executor=SimpleNamespace(execute=execute)).handle(action, HandlerContext(state=state, stack=TaskMemoryStack(), thread_id="thread", run_id="run"))
    refs = result.state_update["sp_current_artifact_refs"]
    ref = refs["report_revision"]
    assert ref["virtual_path"].endswith(f"/{action.action_id}.json")
    assert ref["metadata"]["completion_status"] == "complete"
    paths = list(tmp_path.rglob("*.json"))
    assert len(paths) == 1
    assert json.loads(paths[0].read_text()) == (json.loads(answer) if isinstance(answer, str) else answer)
