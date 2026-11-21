"""Bind successful public raw finals to the exact candidate persisted by native SP."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from deerflow.agents.thread_state import ThreadState
from deerflow.sp.actions import ActionType, SPAction, build_default_action_router
from deerflow.sp.artifacts import SPArtifactAdapter
from deerflow.sp.public_format_repair import PublicArtifactFormatRepairMiddleware
from deerflow.sp.subagents import SPSubagentTask
from deerflow.sp.subagents.dr2_adapter import normalize_dr2_subagent_result

SOURCE_PATH = "/mnt/user-data/uploads/document.txt"
OUTPUT_PATH = "/mnt/user-data/outputs/optional-audit.json"
SOURCE = "Payment is due on 1 January.\nPayment is due on 2 January."
OLD = json.dumps([{"text": '"' + SOURCE + '"', "explanation": "Original draft."}])
FINAL = ' [ {"text":"Payment is due on 1 January.","explanation":"The payment dates differ."}, {"text":"Payment is due on 2 January.","explanation":"The other stated payment date."} ]\n'


def contract():
    return {
        "delivery_mode": "artifact",
        "output_parse_mode": "json",
        "output_file_extension": ".json",
        "output_schema": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "explanation": {"type": "string"}},
                "required": ["text", "explanation"],
                "additionalProperties": False,
            },
        },
        "source_requirements": {"audit_agent": "reporter", "files": [{"path": SOURCE_PATH, "total_lines": 2}], "document_ids": []},
    }


def task():
    return SPSubagentTask(action_id="spact_public_final", subagent_type="reporter", task="Audit and return the complete public JSON array.", description="Public audit", context_refs={"task_contract": contract()})


def messages(final, *, write=OLD):
    return [
        AIMessage(content="", tool_calls=[{"name": "read_file", "id": "source", "args": {"path": SOURCE_PATH}}]),
        ToolMessage(content=SOURCE, name="read_file", tool_call_id="source"),
        AIMessage(content="", tool_calls=[{"name": "write_file", "id": "optional-write", "args": {"path": OUTPUT_PATH, "content": write}}]),
        ToolMessage(content="File written successfully.", name="write_file", tool_call_id="optional-write"),
        AIMessage(content=final),
    ]


def normalized(final, *, raw_messages=None, current_task=None, **kwargs):
    raw = SimpleNamespace(status="completed", result=final, ai_messages=[message.model_dump() for message in (raw_messages or messages(final))], **kwargs)
    return normalize_dr2_subagent_result(raw, task=current_task or task())


def deliver(value, tmp_path):
    class Executor:
        def execute(self, delegated_task):
            assert delegated_task.action_id == task().action_id
            return value

    action = SPAction.create(ActionType.DELEGATE, reason="Public audit", action_id=task().action_id, target_agent="reporter", task=task().task, stage="reporting")
    result = build_default_action_router(delegate_executor=Executor()).execute(
        action,
        state={"sp_task_contract": contract(), "thread_data": {"outputs_path": str(tmp_path)}, "sp_loop_run_id": "run", "sp_non_interactive": True},
        thread_id="thread",
        run_id="run",
    )
    reference = result.state_update["sp_current_artifact_refs"]["report_revision"]
    content, truncated = SPArtifactAdapter().read_text_artifact(reference, state={"thread_data": {"outputs_path": str(tmp_path)}}, thread_id="thread", max_chars=10000)
    assert not truncated
    return result, reference, content


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("old_body", [OLD, '{"unfinished":'])
def test_native_reviewed_raw_final_after_optional_write_is_the_persisted_candidate(monkeypatch, tmp_path, asynchronous, old_body):
    monkeypatch.setenv("SP_CLAUSE_SEMANTIC_REVIEW", "1")
    monkeypatch.setenv("SP_CLAUSE_QUOTE_REVIEW", "1")
    output = tmp_path / "optional-audit.json"

    @tool
    def write_file(path: str, content: str) -> str:
        """Write the actual optional model draft."""
        assert path == OUTPUT_PATH
        output.write_text(content)
        return "File written successfully."

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    model = Model(responses=[messages(FINAL, write=old_body)[2], AIMessage(content=FINAL), AIMessage(content=FINAL)])
    review = PublicArtifactFormatRepairMiddleware(contract(), action_id=task().action_id, auditor_role="reporter", skill_names=["clause"])
    graph = create_agent(model=model, tools=[write_file], middleware=[review], state_schema=ThreadState)
    initial = {"messages": [HumanMessage(content=task().task), *messages(FINAL)[:2]]}
    state = asyncio.run(graph.ainvoke(initial)) if asynchronous else graph.invoke(initial)
    assert state["messages"][-1].content == FINAL
    value = normalized(FINAL, raw_messages=state["messages"])
    result, reference, persisted = deliver(value, tmp_path)

    assert persisted == FINAL
    assert json.loads(persisted) == json.loads(FINAL)
    assert reference["virtual_path"] != OUTPUT_PATH
    assert output.read_text() == old_body
    assert reference["metadata"]["quality_checks"]["public_output_schema"]["passed"]
    assert reference["metadata"]["completion_status"] == "complete"
    assert result.error is None
    assert reference["metadata"]["clause_quote_review"][-1]["checked_items"] == len(json.loads(persisted))
    assert reference["metadata"]["clause_semantic_review"][-1]["status"] == "completed"


def test_actual_schema_valid_raw_final_overrides_a_conflicting_artifact_property():
    value = normalized(FINAL, artifact_content=OLD, artifact_metadata={"created_paths": [OUTPUT_PATH]})
    assert value.artifact_content == FINAL
    assert not value.artifact_metadata.get("created_paths")
    assert value.created_path_evidence["paths"] == [OUTPUT_PATH]


def test_actual_file_receipt_keeps_native_file_delivery(tmp_path):
    receipt = json.dumps({"summary": "The file is complete.", "artifact_content": None, "artifact_metadata": {"completion_status": "complete", "created_paths": [OUTPUT_PATH]}})
    (tmp_path / "optional-audit.json").write_text(OLD)
    value = normalized(receipt)
    assert value.artifact_content is None
    assert value.artifact_metadata["created_paths"] == [OUTPUT_PATH]
    _, reference, persisted = deliver(value, tmp_path)
    assert reference["virtual_path"] == OUTPUT_PATH
    assert persisted == OLD


@pytest.mark.parametrize("damage", ["different_actual_final", "tool_intent", "invalid_schema", "native_stop", "provider_length", "no_schema", "other_role"])
def test_non_public_or_unfinished_final_does_not_replace_a_real_file(damage):
    final = FINAL
    actual = messages(final)
    current_task = task()
    kwargs = {}
    if damage == "different_actual_final":
        actual[-1].content = "The file is complete."
    elif damage == "tool_intent":
        actual[-1].tool_calls = [{"name": "read_file", "id": "pending", "args": {"path": SOURCE_PATH}}]
    elif damage == "invalid_schema":
        final = '[{"text":"Missing explanation"}]'
        actual[-1].content = final
    elif damage == "native_stop":
        kwargs["stop_reason"] = "loop_capped"
    elif damage == "provider_length":
        actual[-1].response_metadata["finish_reason"] = "length"
    elif damage == "no_schema":
        current_task.context_refs["task_contract"].pop("output_schema")
    else:
        current_task.subagent_type = "coder"
    value = normalized(final, raw_messages=actual, current_task=current_task, **kwargs)
    assert value.artifact_metadata["created_paths"] == [OUTPUT_PATH]
    assert value.artifact_content is None
    if damage in {"native_stop", "provider_length"}:
        assert value.completion_status == "partial"


def test_raw_final_priority_does_not_bypass_the_original_source_gate(tmp_path):
    value = normalized(FINAL, raw_messages=messages(FINAL)[2:])
    result, reference, persisted = deliver(value, tmp_path)
    assert persisted == FINAL
    assert reference["metadata"]["completion_status"] == "partial"
    assert any("source" in gap.lower() for gap in reference["metadata"]["evidence_gaps"])
    assert result.error is None


def test_schema_valid_public_object_is_not_unwrapped_as_an_internal_envelope():
    current_task = task()
    current_task.context_refs["task_contract"]["output_schema"] = {
        "type": "object",
        "required": ["summary", "artifact_content"],
        "additionalProperties": False,
        "properties": {"summary": {"type": "string"}, "artifact_content": {"type": "string"}},
    }
    final = ' {"summary":"An actual public field.", "artifact_content":"The other actual public field."}\n'
    value = normalized(final, current_task=current_task)
    assert value.artifact_content == final
    assert not value.artifact_metadata.get("created_paths")
