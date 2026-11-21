"""Optional CLAUSE drafts do not spend the final quotation feedback budget."""

import asyncio
import json
from copy import deepcopy

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import PrivateAttr

from deerflow.agents.thread_state import ThreadState
from deerflow.sp.evidence_quotes import collect_quote_review_diagnostics
from deerflow.sp.public_format_repair import PublicArtifactFormatRepairMiddleware

PATH = "/mnt/user-data/uploads/document.txt"
OUTPUT = "/mnt/user-data/outputs/clause_answer-audit.json"
SOURCE = "Payment is due on 1 January.\nPayment is due on 2 January."
GOOD = json.dumps([{"text": SOURCE, "explanation": "The stated payment dates conflict."}])
BAD = json.dumps([{"text": '"' + SOURCE + '"', "explanation": "The stated payment dates conflict."}])


def contract():
    return {
        "delivery_mode": "artifact",
        "output_parse_mode": "json",
        "output_schema": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "explanation": {"type": "string"}},
                "required": ["text", "explanation"],
                "additionalProperties": False,
            },
        },
        "source_requirements": {"audit_agent": "reporter", "files": [{"path": PATH, "total_lines": 2}], "document_ids": []},
    }


def reads():
    return [AIMessage(content="", tool_calls=[{"name": "read_file", "id": "source", "args": {"path": PATH}}]), ToolMessage(content=SOURCE, name="read_file", tool_call_id="source")]


def draft(value, call_id):
    return AIMessage(content="", tool_calls=[{"name": "write_file", "id": call_id, "args": {"path": OUTPUT, "content": value}}])


def receipt():
    return AIMessage(content=json.dumps({"artifact_content": None, "artifact_metadata": {"completion_status": "complete", "created_paths": [OUTPUT]}}))


def middleware(monkeypatch, *, public=None, skills=None, role="reporter"):
    monkeypatch.setenv("SP_CLAUSE_QUOTE_REVIEW", "1")
    monkeypatch.delenv("SP_CLAUSE_SEMANTIC_REVIEW", raising=False)
    return PublicArtifactFormatRepairMiddleware(public or contract(), action_id="audit", auditor_role=role, skill_names=skills if skills is not None else ["clause"])


def run_chain(check, responses, tmp_path, *, asynchronous, source=True):
    class Model(FakeMessagesListChatModel):
        _seen: list = PrivateAttr(default_factory=list)

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self._seen.append(list(messages))
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    writes = []

    @tool
    def write_file(path: str, content: str) -> str:
        """Execute the model's optional draft write in the isolated test directory."""
        assert path == OUTPUT
        (tmp_path / "draft.json").write_text(content)
        writes.append(content)
        return "File written successfully."

    model = Model(responses=responses)
    graph = create_agent(model=model, tools=[write_file], middleware=[check], state_schema=ThreadState)
    state = {"messages": [HumanMessage(content="Audit the complete source and return the public final array."), *(reads() if source else [])]}
    result = asyncio.run(graph.ainvoke(state)) if asynchronous else graph.invoke(state)
    return result, model, writes


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("flow", ["raw_draft_raw", "two_drafts_raw"])
def test_native_chain_keeps_two_feedback_requests_for_final_candidates(monkeypatch, tmp_path, asynchronous, flow):
    check = middleware(monkeypatch)
    responses = [AIMessage(content=BAD), draft(BAD, "draft")] if flow == "raw_draft_raw" else [draft(BAD, "draft-1"), draft(BAD, "draft-2"), AIMessage(content=BAD)]
    responses.extend([AIMessage(content=BAD), AIMessage(content=GOOD)])
    result, model, writes = run_chain(check, responses, tmp_path, asynchronous=asynchronous)

    assert result["messages"][-1].content == GOOD
    assert writes == [BAD] * (1 if flow == "raw_draft_raw" else 2)
    assert (tmp_path / "draft.json").read_text() == BAD
    diagnostics = collect_quote_review_diagnostics(result["messages"])
    assert [d["candidate_kind"] for d in diagnostics] == ["raw_final"] * 3
    assert [d["attempt"] for d in diagnostics] == [1, 2, 2]
    assert [d["feedback"] for d in diagnostics] == [True, True, False]
    assert diagnostics[-1]["unmatched_indices"] == []
    notes = [request[-1] for request in model._seen if request[-1].name == "sp_clause_quote_review_feedback"]
    assert len(notes) == 2
    # A model can write an optional draft while processing earlier final feedback.
    # Every feedback request is still anchored to an actual assistant final.
    assert all(not message.additional_kwargs.get("sp_clause_quote_review") for message in result["messages"] if isinstance(message, ToolMessage))


@pytest.mark.parametrize("asynchronous", [False, True])
def test_real_final_file_receipt_still_requests_quote_correction(monkeypatch, tmp_path, asynchronous):
    check = middleware(monkeypatch)
    result, model, writes = run_chain(check, [draft(BAD, "bad-file"), receipt(), draft(GOOD, "fixed-file"), receipt()], tmp_path, asynchronous=asynchronous)
    assert writes == [BAD, GOOD]
    assert result["messages"][-1].content == receipt().content
    assert (tmp_path / "draft.json").read_text() == GOOD
    diagnostics = collect_quote_review_diagnostics(result["messages"])
    assert [d["candidate_kind"] for d in diagnostics] == ["file_receipt", "file_receipt"]
    assert [d["feedback"] for d in diagnostics] == [True, False]
    assert diagnostics[-1]["unmatched_indices"] == []
    assert sum(request[-1].name == "sp_clause_quote_review_feedback" for request in model._seen) == 1


def test_compatible_final_artifact_content_envelope_keeps_final_review(monkeypatch, tmp_path):
    def envelope(value):
        return AIMessage(content=json.dumps({"artifact_content": json.loads(value), "artifact_metadata": {"completion_status": "complete"}}))

    result, _, _ = run_chain(middleware(monkeypatch), [envelope(BAD), envelope(GOOD)], tmp_path, asynchronous=False)
    assert result["messages"][-1].content == envelope(GOOD).content
    diagnostics = collect_quote_review_diagnostics(result["messages"])
    assert [d["candidate_kind"] for d in diagnostics] == ["artifact_content", "artifact_content"]
    assert [d["feedback"] for d in diagnostics] == [True, False]


def test_unavailable_source_records_only_final_without_feedback_or_answer_change(monkeypatch, tmp_path):
    result, model, writes = run_chain(middleware(monkeypatch), [draft(BAD, "one"), draft(BAD, "two"), AIMessage(content=BAD)], tmp_path, asynchronous=False, source=False)
    assert result["messages"][-1].content == BAD
    assert writes == [BAD, BAD]
    diagnostics = collect_quote_review_diagnostics(result["messages"])
    assert len(diagnostics) == 1
    assert diagnostics[0]["candidate_kind"] == "raw_final"
    assert diagnostics[0]["status"] == "unavailable"
    assert diagnostics[0]["attempt"] == 0
    assert not diagnostics[0]["feedback"]
    assert not any(request[-1].name == "sp_clause_quote_review_feedback" for request in model._seen)


@pytest.mark.parametrize("damage", ["other_role", "other_skill", "other_source", "disabled"])
def test_other_contracts_keep_their_unchanged_unreviewed_draft_and_final(monkeypatch, tmp_path, damage):
    public = deepcopy(contract())
    role, skills = "reporter", ["clause"]
    if damage == "other_role":
        role = "researcher"
    elif damage == "other_skill":
        skills = ["other"]
    elif damage == "other_source":
        public["source_requirements"]["files"][0]["path"] = "/mnt/user-data/uploads/other.txt"
    check = middleware(monkeypatch, public=public, role=role, skills=skills)
    if damage == "disabled":
        monkeypatch.delenv("SP_CLAUSE_QUOTE_REVIEW")
        check = PublicArtifactFormatRepairMiddleware(public, action_id="audit", auditor_role=role, skill_names=skills)
    result, _, writes = run_chain(check, [draft(BAD, "draft"), AIMessage(content=BAD)], tmp_path, asynchronous=False)
    assert writes == [BAD]
    assert result["messages"][-1].content == BAD
    assert collect_quote_review_diagnostics(result["messages"]) == []
