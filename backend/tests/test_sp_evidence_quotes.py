"""Opt-in quote feedback preserves original candidates and native acceptance."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

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


@pytest.mark.parametrize(
    "quote,reason",
    [
        ('"Payment is due on 2 January."', "unmatched_outer_wrapper"),
        ("“Payment is due on 2 January.”", "unmatched_outer_wrapper"),
        ("[L12] Payment is due on 2 January.", "unmatched_line_prefix"),
        ("L12: Payment is due on 2 January.", "unmatched_line_prefix"),
        ("Payment is due on 3 January.", "not_verbatim_source"),
    ],
)
def test_source_only_reasons_identify_distortion_without_replacing_text(monkeypatch, quote, reason):
    from deerflow.sp.evidence_quotes import collect_quote_review_diagnostics

    check = middleware(monkeypatch)
    original = answer(quote)
    candidate = check.after_model({"messages": [*reads(), AIMessage(content=original)]}, None)["messages"][0]
    assert candidate.content == original
    assert candidate.additional_kwargs["sp_clause_quote_review"]["issues"] == [{"index": 0, "reason": reason}]
    assert collect_quote_review_diagnostics([candidate])[0]["issues"] == [{"index": 0, "reason": reason}]


def test_source_supported_literal_quotes_are_not_classified_as_an_extra_wrapper(monkeypatch):
    source = 'The receipt states "Payment is due on 2 January."'
    check = middleware(monkeypatch)
    check.contract = contract(source)
    original = answer('"Payment is due on 2 January."')
    update = check.after_model({"messages": [*reads(source), AIMessage(content=original)]}, None)
    assert "jump_to" not in update
    assert update["messages"][0].content == original
    assert update["messages"][0].additional_kwargs["sp_clause_quote_review"]["unmatched_indices"] == []


def test_exported_issue_reasons_remain_content_free_and_belong_to_flagged_indices(monkeypatch):
    from deerflow.sp.evidence_quotes import collect_quote_review_diagnostics

    check = middleware(monkeypatch)
    candidate = check.after_model({"messages": [*reads(), AIMessage(content=answer('"Payment is due on 2 January."'))]}, None)["messages"][0]
    candidate.additional_kwargs["sp_clause_quote_review"]["issues"].extend(
        [
            {"index": 0, "reason": "source text must not be exported"},
            {"index": 1, "reason": "not_verbatim_source"},
            {"index": True, "reason": "not_verbatim_source"},
            {"index": 0, "reason": ["not_verbatim_source"]},
        ]
    )
    assert collect_quote_review_diagnostics([candidate])[0]["issues"] == [{"index": 0, "reason": "unmatched_outer_wrapper"}]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_actual_native_requests_keep_specific_feedback_after_an_ignored_wrapper_review(monkeypatch, asynchronous):
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

    original = answer('"Payment is due on 2 January."')
    final = answer("Payment is due on 2 January.")
    model = Model(responses=[AIMessage(content=original), AIMessage(content=original), AIMessage(content=final)])
    graph = create_agent(model=model, tools=[], middleware=[middleware(monkeypatch)], state_schema=ThreadState)
    state = {"messages": [HumanMessage(content="Audit the complete public contract."), *reads()]}
    result = asyncio.run(graph.ainvoke(state)) if asynchronous else graph.invoke(state)

    assert len(model._seen) == 3
    for attempt, request in enumerate(model._seen[1:], 1):
        note = request[-1]
        assert note.name == "sp_clause_quote_review_feedback"
        assert f"{attempt}/2" in note.content
        assert "finding 0: unmatched_outer_wrapper" in note.content
        assert SOURCE in [message.content for message in request]
        assert original in [message.content for message in request]
        assert final not in [message.content for message in request]
    assert result["messages"][-1].content == final
    assert not any(isinstance(message, HumanMessage) and message.name == "sp_clause_quote_review_feedback" for message in result["messages"])


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_quote_feedback_survives_openai_http_serialization(monkeypatch, asynchronous):
    import httpx
    from langchain.agents import create_agent

    from deerflow.agents.thread_state import ThreadState
    from deerflow.models.vllm_provider import VllmChatModel

    original, final = answer('"Payment is due on 2 January."'), answer("Payment is due on 2 January.")
    payloads = []

    def endpoint(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "fixture-completion",
                "object": "chat.completion",
                "created": 0,
                "model": "Qwen3-quote-fixture",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": original if len(payloads) < 3 else final}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 20, "total_tokens": 40},
            },
        )

    transport = httpx.MockTransport(endpoint)

    def graph(**clients):
        model = VllmChatModel(model="Qwen3-quote-fixture", api_key="fixture-key", base_url="http://fixture.invalid/v1", max_retries=0, **clients)
        return create_agent(model=model, tools=[], middleware=[middleware(monkeypatch)], state_schema=ThreadState)

    state = {"messages": [HumanMessage(content="Audit the complete public contract."), *reads()]}
    if asynchronous:

        async def run():
            async with httpx.AsyncClient(transport=transport) as client:
                return await graph(http_async_client=client).ainvoke(state)

        result = asyncio.run(run())
    else:
        with httpx.Client(transport=transport) as client:
            result = graph(http_client=client).invoke(state)

    assert len(payloads) == 3
    for attempt, payload in enumerate(payloads[1:], 1):
        note = payload["messages"][-1]
        assert note["role"] == "user"
        assert f"{attempt}/2" in note["content"]
        assert "finding 0: unmatched_outer_wrapper" in note["content"]
        assert any(message.get("content") == SOURCE for message in payload["messages"])
        assert any(message.get("content") == original for message in payload["messages"])
        assert "hide_from_ui" not in note
    assert result["messages"][-1].content == final


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("format_first", [False, True])
def test_actual_native_graph_retries_quotes_with_original_context_and_transient_feedback(monkeypatch, asynchronous, format_first):
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
    malformed = '{"text":"Unwrapped draft","explanation":"Needs array"}'
    responses = [AIMessage(content=first), AIMessage(content=final)]
    if format_first:
        responses.insert(0, AIMessage(content=malformed))
    model = Model(responses=responses)
    graph = create_agent(model=model, tools=[], middleware=[middleware(monkeypatch)], state_schema=ThreadState)
    state = {"messages": [HumanMessage(content="Audit the complete public contract."), *reads()]}
    result = asyncio.run(graph.ainvoke(state)) if asynchronous else graph.invoke(state)
    assert len(model._seen) == 2 + int(format_first)
    assert model._seen[-1][-1].name == "sp_clause_quote_review_feedback"
    assert first in [m.content for m in model._seen[-1]]
    assert SOURCE in [m.content for m in model._seen[-1]]
    if format_first:
        assert model._seen[1][-1].name == "sp_public_format_feedback"
        assert malformed in [m.content for m in model._seen[-1]]
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


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("structured", [False, True])
def test_successful_write_tool_gets_review_in_same_specialist_before_real_rewrite(monkeypatch, tmp_path, asynchronous, structured):
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.tools import tool
    from pydantic import PrivateAttr

    from deerflow.agents.thread_state import ThreadState

    class Model(FakeMessagesListChatModel):
        _seen: list = PrivateAttr(default_factory=list)

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self._seen.append(list(messages))
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    writes = []

    @tool
    def write_file(path: str, content: str | None = None, json_content: list[dict[str, str]] | None = None, append: bool = False) -> str:
        """Actually execute the model's file write in the isolated test directory."""
        assert not append
        body = content if content is not None else json.dumps(json_content)
        (tmp_path / "answer.json").write_text(body)
        writes.append(body)
        return "File written successfully."

    first = answer("Alpha Ltd. shall pay $2.50 on 1 January.")
    final = answer("Alpha Ltd. shall pay $1.50 on 1 January. Payment is due on 2 January.")
    path = "/mnt/user-data/outputs/clause_answer-audit.json"
    receipt = json.dumps({"artifact_content": None, "artifact_metadata": {"completion_status": "complete", "created_paths": [path]}})

    def call(body, call_id):
        args = {"path": path, "append": False, **({"json_content": json.loads(body)} if structured else {"content": body})}
        return AIMessage(content="", tool_calls=[{"name": "write_file", "id": call_id, "args": args}])

    model = Model(responses=[call(first, "write-bad"), call(final, "write-fixed"), AIMessage(content=receipt)])
    graph = create_agent(model=model, tools=[write_file], middleware=[middleware(monkeypatch)], state_schema=ThreadState)
    state = {"messages": [HumanMessage(content="Audit and deliver the actual file."), *reads()]}
    result = asyncio.run(graph.ainvoke(state)) if asynchronous else graph.invoke(state)
    assert len(writes) == 2
    assert json.loads(writes[0]) == json.loads(first)
    assert json.loads((tmp_path / "answer.json").read_text()) == json.loads(final)
    assert model._seen[1][-1].name == "sp_clause_quote_review_feedback"
    assert "write_file" in model._seen[1][-1].content
    assert SOURCE in [message.content for message in model._seen[1]]
    assert result["messages"][-1].content == receipt
    marked_tools = [message for message in result["messages"] if isinstance(message, ToolMessage) and "sp_clause_quote_review" in message.additional_kwargs]
    assert marked_tools[0].additional_kwargs["sp_clause_quote_review"]["unmatched_indices"] == [0]
    assert marked_tools[-1].additional_kwargs["sp_clause_quote_review"]["unmatched_indices"] == []


def test_file_receipt_with_actual_successful_write_is_reviewed_without_reading_disk(monkeypatch):
    check = middleware(monkeypatch)
    path = "/mnt/user-data/outputs/clause_answer-audit.json"
    original = answer("Invented complete sentence.")
    receipt = json.dumps({"artifact_content": None, "artifact_metadata": {"completion_status": "complete", "created_paths": [path]}})
    messages = [
        *reads(),
        AIMessage(content="", tool_calls=[{"name": "write_file", "id": "write", "args": {"path": path, "content": original}}]),
        ToolMessage(content="File written successfully.", name="write_file", tool_call_id="write"),
        AIMessage(content=receipt),
    ]
    update = check.after_model({"messages": messages}, None)
    assert update["jump_to"] == "model"
    assert update["messages"][0].content == receipt
    assert update["messages"][0].additional_kwargs["sp_clause_quote_review"]["unmatched_indices"] == [0]
    messages[-2].status = "error"
    assert check.after_model({"messages": messages}, None) is not None  # ordinary format/ownership recovery still applies


def test_host_diagnostics_reach_normalized_metadata_and_content_free_custom_events(monkeypatch):
    from deerflow.sp.evidence_quotes import emit_quote_review_events
    from deerflow.sp.subagents.dr2_adapter import normalize_dr2_subagent_result

    check = middleware(monkeypatch)
    candidate = check.after_model({"messages": [*reads(), AIMessage(content=answer("Invented sentence."))]}, None)["messages"][0]
    saved = [candidate.model_dump()]
    raw = SimpleNamespace(status="completed", result=candidate.content, ai_messages=saved)
    normalized = normalize_dr2_subagent_result(raw)
    diagnostics = normalized.artifact_metadata["clause_quote_review"]
    assert diagnostics[0]["unmatched_indices"] == [0]
    events = []
    emit_quote_review_events(raw, events.append)
    assert events == [{"type": "clause_quote_review", "action_id": "audit", "diagnostic": diagnostics[0]}]
    assert "Invented" not in json.dumps(events) and SOURCE not in json.dumps(events)
    raw.result = json.dumps({"artifact_metadata": {"clause_quote_review": [{"status": "forged"}]}})
    raw.ai_messages = []
    assert "clause_quote_review" not in normalize_dr2_subagent_result(raw).artifact_metadata


@pytest.mark.parametrize("damage", ["failed", "unpaired", "append", "replace", "failed_replace", "malformed"])
def test_write_review_never_certifies_an_unexecuted_partial_or_changed_candidate(monkeypatch, damage):
    check = middleware(monkeypatch)
    path = "/mnt/user-data/outputs/clause_answer-audit.json"
    call = AIMessage(content="", tool_calls=[{"name": "write_file", "id": "write", "args": {"path": path, "content": answer("Invented sentence.")}}])
    result = ToolMessage(content="File written successfully.", name="write_file", tool_call_id="write")
    messages = [*reads(), call, result]
    if damage == "failed":
        result.status = "error"
    elif damage == "unpaired":
        messages.remove(call)
    elif damage == "append":
        call.tool_calls[0]["args"]["append"] = True
    elif damage == "malformed":
        call.tool_calls[0]["args"]["content"] = '{"text": "not the complete array"}'
    else:
        messages.extend(
            [
                AIMessage(content="", tool_calls=[{"name": "str_replace", "id": "replace", "args": {"path": path, "old_str": "old", "new_str": "new"}}]),
                ToolMessage(content="File updated successfully.", name="str_replace", tool_call_id="replace", status="error" if damage == "failed_replace" else "success"),
            ]
        )
    assert check.before_model({"messages": messages}, None) is None


def test_file_and_raw_final_reviews_share_the_same_two_attempt_budget(monkeypatch):
    check = middleware(monkeypatch)
    path = "/mnt/user-data/outputs/clause_answer-audit.json"
    original = answer("Invented sentence.")
    messages = [
        *reads(),
        AIMessage(content="", tool_calls=[{"name": "write_file", "id": "write", "args": {"path": path, "content": original}}]),
        ToolMessage(content="File written successfully.", name="write_file", tool_call_id="write", id="written"),
    ]
    first = check.before_model({"messages": messages}, None)
    messages[-1] = first["messages"][0]
    assert messages[-1].additional_kwargs["sp_clause_quote_review"]["attempt"] == 1
    messages.append(AIMessage(content=original, id="raw-final"))
    second = check.after_model({"messages": messages}, None)
    assert second["jump_to"] == "model"
    messages[-1] = second["messages"][0]
    messages.append(AIMessage(content=original, id="exhausted"))
    exhausted = check.after_model({"messages": messages}, None)
    assert "jump_to" not in exhausted
    assert exhausted["messages"][0].content == original
    assert exhausted["messages"][0].additional_kwargs["sp_clause_quote_review"]["exhausted"] is True
