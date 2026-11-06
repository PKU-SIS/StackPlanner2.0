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


@pytest.mark.parametrize("answer", [[{"text": "原文", "explanation": "A contradiction"}], {"extracted_value": "0", "calculated_value": "0"}])
def test_structured_envelope_artifact_persists_as_json_not_python_repr(tmp_path, answer):
    text = json.dumps({"summary": "Done", "artifact_content": answer})
    value = normalize_dr2_subagent_result(_raw(text), task=_task())
    written = SPArtifactAdapter().write_text_artifact(value.artifact_content, artifact_type="report_revision", state={"thread_data": {"outputs_path": str(tmp_path)}}, filename_hint="spact_small123.json")
    from pathlib import Path

    assert json.loads(Path(written.metadata.workspace_path).read_text()) == answer
    assert written.metadata.virtual_path.endswith("/spact_small123.json")
