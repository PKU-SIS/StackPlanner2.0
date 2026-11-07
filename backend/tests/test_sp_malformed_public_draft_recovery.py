"""Recover encoding through the model; never promote textual tool intent."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.sp.actions import SPAction, build_default_action_router
from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.handlers.delegate import DelegateHandler
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.public_format_repair import PublicArtifactFormatRepairMiddleware
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus, SPSubagentTask
from deerflow.sp.subagents.dr2_adapter import normalize_dr2_subagent_result

SCHEMA = {"type": "array", "items": {"type": "object", "required": ["text", "explanation"], "properties": {"text": {"type": "string"}, "explanation": {"type": "string"}}, "additionalProperties": False}}
CONTRACT = {"delivery_mode": "artifact", "output_schema": SCHEMA}
BAD_JSON = '[{"text":"An original "quoted" sentence.","explanation":"A supported finding."}]'
GOOD_JSON = json.dumps([{"text": 'An original "quoted" sentence.', "explanation": "A supported finding."}])
TOOL_TEXT = '<tool_call>{"name":"write_file","arguments":{"path":"/mnt/user-data/outputs/answer.json","json_content":' + BAD_JSON + "}}</tool_call>"


def _raw(text):
    return SimpleNamespace(status="completed", result=text, ai_messages=[AIMessage(content=text).model_dump()], artifact_content=None, artifact_type=None, artifact_metadata={})


@pytest.mark.parametrize(
    "text",
    [
        TOOL_TEXT,
        TOOL_TEXT.replace("A supported finding.", "A supported finding. " * 100),
        TOOL_TEXT.replace("tool_call", "TOOL_CALL"),
        '<tool_call>{"name":"write_file","arguments":{"json_content":[]}}</tool_call>',
        json.dumps({"name": "write_file", "arguments": {"json_content": []}, "artifact_content": json.loads(GOOD_JSON), "artifact_metadata": {"completion_status": "partial"}}),
    ],
)
def test_textual_tool_intent_cannot_be_a_public_report_artifact_or_escape_format_feedback(tmp_path, text):
    raw = _raw(text)
    original = deepcopy(raw.ai_messages)
    task = SPSubagentTask(action_id="action", subagent_type="reporter", task="Audit original sources", description="Public audit", context_refs={"task_contract": CONTRACT})
    normalized = normalize_dr2_subagent_result(raw, task=task)
    assert normalized.artifact_content is None
    assert normalized.artifact_metadata["completion_status"] == "partial"
    assert any("unexecuted" in gap.lower() for gap in normalized.artifact_metadata["evidence_gaps"])
    assert raw.result == text and raw.ai_messages == original
    update = PublicArtifactFormatRepairMiddleware(CONTRACT).after_model({"messages": [AIMessage(content=text)]}, None)
    assert update["jump_to"] == "model"
    assert update["messages"][0].content == text
    state = {"thread_data": {"outputs_path": str(tmp_path / "outputs")}, "sp_task_contract": CONTRACT, "messages": [HumanMessage(content="Audit original sources and return the public JSON array.")]}
    action = SPAction.create("DELEGATE", target_agent="reporter", task="Audit original sources", reason="Audit", stage="reporting")
    handled = build_default_action_router(delegate_executor=SimpleNamespace(execute=lambda task: normalized)).execute(action, state=state, thread_id="thread", run_id="run")
    assert not handled.state_update.get("sp_current_artifact_refs")
    assert not list((tmp_path / "outputs").rglob("*.json"))


@pytest.mark.parametrize("text", [BAD_JSON, '[{"text":"A sentence.\\",\\"explanation\\":\\"Merged field text."}]'])
def test_native_rejected_noncap_draft_is_noncanonical_only_in_next_transient_preview(tmp_path, text):
    state = {"thread_data": {"outputs_path": str(tmp_path / "outputs")}, "sp_loop_run_id": "run", "sp_task_contract": CONTRACT, "messages": [HumanMessage(content="Audit originals and return the public JSON array.")]}
    incomplete = SPSubagentResult(status=SPSubagentStatus.COMPLETED, result="An actual malformed draft", artifact_content=text, artifact_metadata={"completion_status": "partial"})
    action = SPAction.create("DELEGATE", target_agent="reporter", task="Audit originals", stage="reporting", reason="Audit")
    first = build_default_action_router(delegate_executor=SimpleNamespace(execute=lambda task: incomplete)).execute(action, state=state, thread_id="thread", run_id="run")
    state.update(first.state_update)
    ref = state["sp_current_artifact_refs"]["report_revision"]
    assert ref["metadata"]["quality_checks"]["public_output_schema"]["passed"] is False
    path = Path(state["thread_data"]["outputs_path"]) / ref["virtual_path"].removeprefix("/mnt/user-data/outputs/")
    original_bytes = path.read_bytes()
    original_refs = deepcopy(state["sp_current_artifact_refs"])
    captured = []

    def execute(task):
        captured.append(task)
        return SPSubagentResult(status=SPSubagentStatus.FAILED, error="No service in unit test")

    retry = SPAction.create(
        "DELEGATE",
        target_agent="reporter",
        task="Fix the original audit format",
        reason="Resolve native format errors",
        stage="revision",
        input_refs=[ref["artifact_id"]],
        metadata={"revision_reason": "Resolve actual native format errors."},
    )
    DelegateHandler(executor=SimpleNamespace(execute=execute)).handle(retry, HandlerContext(state=state, stack=TaskMemoryStack.from_dict(state["sp_task_memory"]), thread_id="thread", run_id="run"))
    task = captured[0]
    item = next(item for item in task.context_refs["artifact_bodies"]["items"] if item["artifact_id"] == ref["artifact_id"])
    assert item["canonical"] is False and item["content_omitted"] is True
    assert item["recovery_kind"] == "public_format"
    assert item["format_errors"]
    assert item["virtual_path"] == ref["virtual_path"]
    assert text not in item["content"]
    assert "single-layer" in task.task and "original sources" in task.task
    assert ref["artifact_id"] in task.task
    assert path.read_bytes() == original_bytes
    assert state["sp_current_artifact_refs"] == original_refs


def test_native_graph_corrects_bad_json_then_textual_tool_without_losing_original_drafts():
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.tools import tool
    from pydantic import PrivateAttr

    from deerflow.agents.thread_state import ThreadState

    @tool
    def read_file(path: str) -> str:
        """Read an original public source."""
        return 'An original "quoted" sentence.'

    @tool
    def write_file(path: str, content: str) -> str:
        """Write a real output artifact."""
        raise AssertionError("A textual tool envelope must not execute a write")

    class Model(FakeMessagesListChatModel):
        _seen: list = PrivateAttr(default_factory=list)
        _bound: list = PrivateAttr(default_factory=list)

        def bind_tools(self, tools, **kwargs):
            self._bound.append([tool.name for tool in tools])
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self._seen.append(list(messages))
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    model = Model(responses=[AIMessage(content=BAD_JSON), AIMessage(content=TOOL_TEXT), AIMessage(content=GOOD_JSON)])
    graph = create_agent(model=model, tools=[read_file, write_file], middleware=[PublicArtifactFormatRepairMiddleware(CONTRACT, action_id="action")], state_schema=ThreadState)
    result = graph.invoke({"messages": [HumanMessage(content='Original public source: An original "quoted" sentence. Audit it and return the required JSON array.')]})
    assert len(model._seen) == 3
    assert result["messages"][-1].content == GOOD_JSON
    assert [message.content for message in result["messages"] if isinstance(message, AIMessage)] == [BAD_JSON, TOOL_TEXT, GOOD_JSON]
    assert model._bound == [["read_file", "write_file"], ["read_file"], ["read_file"]]
    assert "single-layer" in model._seen[2][-1].content
    assert "Rebuild" in model._seen[2][-1].content


def test_valid_public_json_can_quote_tool_syntax_as_source_data():
    text = json.dumps([{"text": '<tool_call>{"name":"write_file"}</tool_call>', "explanation": "This is literal original source text."}])
    task = SPSubagentTask(action_id="action", subagent_type="reporter", task="Quote the public source", description="Audit", context_refs={"task_contract": CONTRACT})
    normalized = normalize_dr2_subagent_result(_raw(text), task=task)
    assert normalized.artifact_content == text
    assert normalized.artifact_metadata["completion_status"] == "complete"
    assert PublicArtifactFormatRepairMiddleware(CONTRACT).after_model({"messages": [AIMessage(content=text)]}, None) is None


@pytest.mark.parametrize("completion_status", ["complete", "partial", "blocked"])
@pytest.mark.parametrize("nested", [False, True])
def test_textual_tool_intent_in_explicit_artifact_cannot_be_persisted(completion_status, nested):
    body = TOOL_TEXT
    if nested:
        body = json.dumps({"summary": "A nested transport", "artifact_content": body, "artifact_type": "report"})
    text = json.dumps(
        {"summary": "The audit is unfinished", "artifact_content": body, "artifact_metadata": {"completion_status": completion_status, "evidence_gaps": [] if completion_status == "complete" else ["Source verification remains unfinished."]}}
    )
    raw = _raw(text)
    original = deepcopy(raw.ai_messages)
    task = SPSubagentTask(action_id="action", subagent_type="reporter", task="Audit original sources", description="Public audit", context_refs={"task_contract": CONTRACT})
    normalized = normalize_dr2_subagent_result(raw, task=task)
    assert normalized.artifact_content is None
    assert normalized.artifact_metadata["completion_status"] == ("partial" if completion_status == "complete" else completion_status)
    assert normalized.artifact_metadata["unexecuted_public_tool_intent"] is True
    assert any("unexecuted" in gap.lower() for gap in normalized.artifact_metadata["evidence_gaps"])
    assert raw.result == text and raw.ai_messages == original
    update = PublicArtifactFormatRepairMiddleware(CONTRACT).after_model({"messages": [AIMessage(content=text)]}, None)
    if completion_status in {"partial", "blocked"}:
        assert update is None  # Honest failure envelopes retain native recovery.
    else:
        assert update["jump_to"] == "model"
