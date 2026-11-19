"""Opt-in quote feedback preserves original candidates and native acceptance."""

import asyncio
import json
from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.sp.public_format_repair import PublicArtifactFormatRepairMiddleware

PATH = "/mnt/user-data/uploads/document.txt"
SOURCE = "Alpha Ltd. shall pay $1.50 on 1 January.\n\nAn unrelated provision remains effective.\nPayment is due on 2 January."


def contract(source=SOURCE):
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
        "source_requirements": {"audit_agent": "reporter", "files": [{"path": PATH, "total_lines": len(source.splitlines())}], "document_ids": []},
    }


def reads(source=SOURCE, *, start=None, end=None, call_id="read"):
    args = {"path": PATH}
    if start is not None:
        args["start_line"] = start
    if end is not None:
        args["end_line"] = end
    body = source if start is None and end is None else "\n".join(source.splitlines()[(start or 1) - 1 : end])
    return [AIMessage(content="", tool_calls=[{"name": "read_file", "id": call_id, "args": args}]), ToolMessage(content=body, name="read_file", tool_call_id=call_id)]


def answer(text):
    return json.dumps([{"text": text, "explanation": "The two payment dates disagree."}])


def middleware(monkeypatch, **kwargs):
    monkeypatch.setenv("SP_CLAUSE_QUOTE_REVIEW", "1")
    return PublicArtifactFormatRepairMiddleware(contract(), action_id="audit", auditor_role="reporter", skill_names=["clause"], **kwargs)


@pytest.mark.parametrize(
    "quote",
    [
        "Alpha Ltd. shall pay $1.50 on 1 January.",
        "Alpha Ltd. shall pay $1.50 on 1 January.\n\nPayment is due on 2 January.",
        "Payment is due on 2 January. Alpha Ltd. shall pay $1.50 on 1 January.",
        "Alpha Ltd. shall pay $1.50\non 1 January.",
    ],
)
def test_original_and_noncontiguous_sentences_do_not_trigger_feedback(monkeypatch, quote):
    check = middleware(monkeypatch)
    original = answer(quote)
    state = {"messages": [*reads(), AIMessage(content=original, id="answer")]}
    update = check.after_model(state, None)
    assert "jump_to" not in update
    candidate = update["messages"][0]
    assert candidate.content == original
    assert candidate.additional_kwargs["sp_clause_quote_review"]["status"] == "checked"
    assert candidate.additional_kwargs["sp_clause_quote_review"]["unmatched_indices"] == []


def test_paged_original_including_blank_line_is_reconstructed(monkeypatch):
    check = middleware(monkeypatch)
    state = {"messages": [*reads(start=1, end=2), *reads(start=3, end=4, call_id="later"), AIMessage(content=answer("Payment is due on 2 January."))]}
    update = check.after_model(state, None)
    assert update["messages"][0].additional_kwargs["sp_clause_quote_review"]["status"] == "checked"
    assert "jump_to" not in update


@pytest.mark.parametrize("damage", ["missing", "truncated", "unpaired", "failed", "conflict", "hidden_prefetch"])
def test_incomplete_or_unreliable_source_is_unavailable_not_quote_error(monkeypatch, damage):
    check = middleware(monkeypatch)
    source_messages = reads()
    if damage == "missing":
        source_messages = reads(start=1, end=2)
    elif damage == "truncated":
        source_messages[-1].content += "\n... [truncated: showing first 100 of 200 chars] ..."
    elif damage == "unpaired":
        source_messages.pop(0)
    elif damage == "failed":
        source_messages[-1].status = "error"
    elif damage == "hidden_prefetch":
        source_messages[-1].additional_kwargs = {"sp_host_source_prefetch": True}
    else:
        source_messages.extend(reads(source=SOURCE.replace("1 January", "3 January"), call_id="changed"))
    original = answer("Invented payment deadline.")
    update = check.after_model({"messages": [*source_messages, AIMessage(content=original)]}, None)
    assert "jump_to" not in update
    candidate = update["messages"][0]
    assert candidate.content == original
    assert candidate.additional_kwargs["sp_clause_quote_review"]["status"] == "unavailable"


@pytest.mark.parametrize("damage", ["disabled", "skill", "role", "inline", "schema", "rag", "path"])
def test_review_is_explicit_and_does_not_apply_to_other_contracts(monkeypatch, damage):
    monkeypatch.setenv("SP_CLAUSE_QUOTE_REVIEW", "1")
    public = deepcopy(contract())
    skills, role = ["clause"], "reporter"
    if damage == "disabled":
        monkeypatch.delenv("SP_CLAUSE_QUOTE_REVIEW")
    elif damage == "skill":
        skills = ["dcc-audit"]
    elif damage == "role":
        role = "researcher"
    elif damage == "inline":
        public["delivery_mode"] = "inline"
    elif damage == "schema":
        public["output_schema"] = {"type": "array", "items": {"type": "object"}}
    elif damage == "rag":
        public["source_requirements"]["document_ids"] = ["public-document"]
    else:
        public["source_requirements"]["files"][0]["path"] = "/mnt/user-data/uploads/other.txt"
    check = PublicArtifactFormatRepairMiddleware(public, auditor_role=role, skill_names=skills)
    assert check.after_model({"messages": [*reads(), AIMessage(content=answer("Invented sentence."))]}, None) is None


def test_feedback_is_bounded_and_exhaustion_preserves_all_original_findings(monkeypatch):
    check = middleware(monkeypatch)
    original = answer("Alpha Ltd. shall pay $2.50 on 1 January.")
    messages = [*reads(), AIMessage(content=original, id="first")]
    for attempt in (1, 2):
        update = check.after_model({"messages": messages}, None)
        assert update["jump_to"] == "model"
        candidate = update["messages"][0]
        assert candidate.content == original
        assert candidate.additional_kwargs["sp_clause_quote_review"]["attempt"] == attempt
        messages[-1] = candidate
        messages.append(AIMessage(content=original, id=f"retry-{attempt}"))
    update = check.after_model({"messages": messages}, None)
    assert "jump_to" not in update
    candidate = update["messages"][0]
    assert candidate.content == original
    assert "stackplanner" not in candidate.additional_kwargs
    assert candidate.additional_kwargs["sp_clause_quote_review"]["exhausted"] is True
    # The diagnostic annotation is idempotent and cannot itself cause a loop.
    messages[-1] = candidate
    assert check.after_model({"messages": messages}, None) is None


@pytest.mark.parametrize("asynchronous", [False, True])
def test_actual_native_graph_retries_quotes_with_original_context_and_transient_feedback(monkeypatch, asynchronous):
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from pydantic import PrivateAttr

    from deerflow.agents.thread_state import ThreadState

    class Model(FakeMessagesListChatModel):
        _seen: list = PrivateAttr(default_factory=list)

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self._seen.append(list(messages))
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    first = answer("Alpha Ltd. shall pay $2.50 on 1 January.")
    final = answer("Alpha Ltd. shall pay $1.50 on 1 January. Payment is due on 2 January.")
    model = Model(responses=[AIMessage(content=first), AIMessage(content=final)])
    graph = create_agent(model=model, tools=[], middleware=[middleware(monkeypatch)], state_schema=ThreadState)
    state = {"messages": [HumanMessage(content="Audit the complete public contract."), *reads()]}
    result = asyncio.run(graph.ainvoke(state)) if asynchronous else graph.invoke(state)
    assert len(model._seen) == 2
    assert model._seen[1][-1].name == "sp_clause_quote_review_feedback"
    assert first in [m.content for m in model._seen[1]]
    assert SOURCE in [m.content for m in model._seen[1]]
    assert result["messages"][-1].content == final
    assert not any(isinstance(m, HumanMessage) and m.name == "sp_clause_quote_review_feedback" for m in result["messages"])
    assert result["messages"][-1].additional_kwargs["sp_clause_quote_review"]["unmatched_indices"] == []


def test_quote_review_does_not_jump_past_native_guard(monkeypatch):
    check = middleware(monkeypatch)
    stopped = AIMessage(content=answer("Invented sentence."), additional_kwargs={"stackplanner": {"status": "blocked"}})
    assert check.after_model({"messages": [*reads(), stopped]}, None) is None


def test_visible_native_prefetch_and_saved_candidate_diagnostics(monkeypatch):
    # Executor captures an assistant turn before after_model annotates its ID.
    # Keep diagnostics in that saved record as well as in graph state.
    original = answer("Alpha Ltd. shall pay $2.50 on 1 January.")
    candidate = AIMessage(content=original, id="candidate")
    saved = [message.model_dump() for message in [*reads(), candidate]]
    saved[1]["additional_kwargs"] = {"sp_host_source_prefetch": True, "sp_host_source_prefetch_visible": True}
    check = middleware(monkeypatch, captured_sources=lambda: saved)
    update = check.after_model({"messages": [candidate]}, None)
    assert update["jump_to"] == "model"
    assert saved[-1]["content"] == original
    assert saved[-1]["additional_kwargs"]["sp_clause_quote_review"] == update["messages"][0].additional_kwargs["sp_clause_quote_review"]
