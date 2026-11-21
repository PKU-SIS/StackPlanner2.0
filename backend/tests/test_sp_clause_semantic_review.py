"""One opt-in source-only semantic pass inside the existing Reporter instance."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.sp.public_format_repair import PublicArtifactFormatRepairMiddleware

SOURCE_PATH = "/mnt/user-data/uploads/document.txt"
SOURCE = "Payment is due on 1 January.\nPayment is due on 2 January."
GOOD = json.dumps([{"text": SOURCE, "explanation": "The stated payment dates conflict."}])
WRAPPED = json.dumps([{"text": '"' + SOURCE + '"', "explanation": "The stated payment dates conflict."}])


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
        "source_requirements": {"audit_agent": "reporter", "files": [{"path": SOURCE_PATH, "total_lines": 2}], "document_ids": []},
    }


def reads():
    return [
        AIMessage(content="", tool_calls=[{"name": "read_file", "id": "source-read", "args": {"path": SOURCE_PATH}}]),
        ToolMessage(content=SOURCE, name="read_file", tool_call_id="source-read"),
    ]


def middleware(monkeypatch, *, quote=False, **kwargs):
    monkeypatch.setenv("SP_CLAUSE_SEMANTIC_REVIEW", "1")
    monkeypatch.setenv("SP_CLAUSE_QUOTE_REVIEW", "1" if quote else "0")
    return PublicArtifactFormatRepairMiddleware(contract(), action_id="audit", auditor_role="reporter", skill_names=["clause"], **kwargs)


def assert_public_issue_scope(content):
    note = content.lower()
    for family in ["ambiguity", "inconsistency", "misaligned terminology", "omission", "structural flaws"]:
        assert family + ":" in note
    assert "plausible readings" in note
    assert "document's own" in note
    assert "need not be a contradiction" in note
    assert "no supported issue" in note and "[]" in note
    assert "claimed inconsistency" not in note


def test_semantic_review_is_explicit_and_scoped_to_the_public_clause_reporter(monkeypatch):
    from deerflow.sp.semantic_review import clause_semantic_review_enabled

    monkeypatch.delenv("SP_CLAUSE_SEMANTIC_REVIEW", raising=False)
    assert not clause_semantic_review_enabled(contract(), ["clause"], "reporter")
    monkeypatch.setenv("SP_CLAUSE_SEMANTIC_REVIEW", "1")
    assert clause_semantic_review_enabled(contract(), ["clause"], "reporter")
    assert not clause_semantic_review_enabled(contract(), ["nba"], "reporter")
    assert not clause_semantic_review_enabled(contract(), ["clause"], "researcher")
    for changed in ["schema", "source", "documents", "delivery"]:
        value = deepcopy(contract())
        if changed == "schema":
            value["output_schema"]["items"]["additionalProperties"] = True
        elif changed == "source":
            value["source_requirements"]["files"][0]["path"] = "/other.txt"
        elif changed == "documents":
            value["source_requirements"]["document_ids"] = ["other"]
        else:
            value["delivery_mode"] = "inline"
        assert not clause_semantic_review_enabled(value, ["clause"], "reporter")


@pytest.mark.parametrize("kind", ["raw_final", "artifact_content", "file_receipt"])
def test_first_qualified_candidate_requests_review_without_changing_content(monkeypatch, kind):
    check = middleware(monkeypatch, quote=True)
    messages = reads()
    body = GOOD
    if kind == "artifact_content":
        body = json.dumps({"artifact_content": json.loads(GOOD), "artifact_metadata": {"completion_status": "complete"}})
    if kind == "file_receipt":
        path = "/mnt/user-data/outputs/audit.json"
        messages.extend(
            [
                AIMessage(content="", tool_calls=[{"name": "write_file", "id": "write", "args": {"path": path, "content": GOOD}}]),
                ToolMessage(content="File written successfully.", name="write_file", tool_call_id="write", id="write-result"),
            ]
        )
        body = json.dumps({"artifact_content": None, "artifact_metadata": {"completion_status": "complete", "created_paths": [path]}})
    candidate = AIMessage(content=body, id="first-candidate")
    messages.append(candidate)
    update = check.after_model({"messages": messages}, None)
    marked = update["messages"][0]
    assert update["jump_to"] == "model"
    assert marked.content == body
    assert "sp_clause_quote_review" not in marked.additional_kwargs
    assert marked.additional_kwargs["sp_clause_semantic_review"]["status"] == "requested"
    assert marked.additional_kwargs["sp_clause_semantic_review"]["candidate_kind"] == kind
    assert candidate.additional_kwargs == {}


@pytest.mark.parametrize("damage", ["missing", "truncated", "failed", "unpaired", "conflict"])
def test_unavailable_source_exhausts_without_claiming_review_or_changing_candidate(monkeypatch, damage):
    check = middleware(monkeypatch)
    messages = reads()
    if damage == "missing":
        messages = []
    elif damage == "truncated":
        messages[-1].content += " [truncated: incomplete]"
    elif damage == "failed":
        messages[-1].status = "error"
    elif damage == "unpaired":
        messages.pop(0)
    else:
        messages.extend(
            [
                AIMessage(content="", tool_calls=[{"name": "read_file", "id": "conflict", "args": {"path": SOURCE_PATH}}]),
                ToolMessage(content=SOURCE.replace("1 January", "3 January"), name="read_file", tool_call_id="conflict"),
            ]
        )
    messages.append(AIMessage(content=GOOD))
    update = check.after_model({"messages": messages}, None)
    assert "jump_to" not in update
    assert update["messages"][0].content == GOOD
    info = update["messages"][0].additional_kwargs["sp_clause_semantic_review"]
    assert info["status"] == "exhausted" and info["attempt"] == 0
    assert info["reason"] == ("conflicting_original_reads" if damage == "conflict" else "complete_original_read_unavailable")


@pytest.mark.parametrize("damage", ["partial", "blocked", "gaps", "provider", "length", "tool", "invalid_tool", "safety", "budget"])
def test_semantic_pass_never_reopens_a_failed_stopped_or_tool_candidate(monkeypatch, damage):
    check = middleware(monkeypatch)
    candidate = AIMessage(content=GOOD)
    if damage in {"partial", "blocked", "gaps"}:
        candidate.content = json.dumps({"artifact_content": json.loads(GOOD), "artifact_metadata": {"completion_status": damage if damage != "gaps" else "complete", "evidence_gaps": ["Unfinished"] if damage == "gaps" else []}})
    elif damage == "provider":
        candidate.additional_kwargs["error_type"] = "ProviderError"
    elif damage == "length":
        candidate.response_metadata["finish_reason"] = "length"
    elif damage == "tool":
        candidate.tool_calls = [{"name": "read_file", "id": "next-read", "args": {}}]
    elif damage == "invalid_tool":
        candidate.invalid_tool_calls = [{"name": "read_file", "id": "bad", "args": "bad", "error": "parse"}]
    elif damage == "safety":
        candidate.additional_kwargs["safety_termination"] = {"detected": True}
    else:
        candidate.content += " [TOKEN BUDGET EXCEEDED]"
    assert check.after_model({"messages": [*reads(), candidate]}, None) is None


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("format_repair", [False, True])
def test_real_native_http_orders_one_semantic_pass_then_two_quote_reviews(monkeypatch, asynchronous, format_repair):
    import httpx
    from langchain.agents import create_agent

    from deerflow.agents.thread_state import ThreadState
    from deerflow.models.vllm_provider import VllmChatModel
    from deerflow.sp.semantic_review import collect_semantic_review_diagnostics

    check = middleware(monkeypatch, quote=True)
    responses = [GOOD, WRAPPED, WRAPPED, GOOD]
    if format_repair:
        responses = ["Malformed original", GOOD, "Malformed reviewed draft", WRAPPED, WRAPPED, GOOD]
    payloads = []

    def endpoint(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": f"fixture-{len(payloads)}",
                "object": "chat.completion",
                "created": 0,
                "model": "Qwen3-semantic-fixture",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": responses[len(payloads) - 1]}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50},
            },
        )

    transport = httpx.MockTransport(endpoint)

    def graph(**clients):
        model = VllmChatModel(model="Qwen3-semantic-fixture", api_key="fixture-key", base_url="http://fixture.invalid/v1", max_retries=0, **clients)
        return create_agent(model=model, tools=[], middleware=[check], state_schema=ThreadState)

    state = {"messages": [HumanMessage(content="Audit the whole source and preserve every supported distinct finding."), *reads()]}
    if asynchronous:

        async def run():
            async with httpx.AsyncClient(transport=transport) as client:
                return await graph(http_async_client=client).ainvoke(state)

        result = asyncio.run(run())
    else:
        with httpx.Client(transport=transport) as client:
            result = graph(http_client=client).invoke(state)
    assert len(payloads) == len(responses)
    notes = [payload["messages"][-1] for payload in payloads]
    semantic = [note for note in notes if note.get("name") == "sp_clause_semantic_review_feedback"]
    quotes = [note for note in notes if note.get("name") == "sp_clause_quote_review_feedback"]
    assert len(semantic) == 1 and len(quotes) == 2
    assert semantic[0]["role"] == "user" and "hide_from_ui" not in semantic[0]
    assert "every supported distinct finding" in semantic[0]["content"]
    assert "whole document" in semantic[0]["content"]
    assert_public_issue_scope(semantic[0]["content"])
    assert all(any(message.get("content") == SOURCE for message in payload["messages"]) for payload in payloads)
    assert result["messages"][-1].content == GOOD
    assert [info["status"] for info in collect_semantic_review_diagnostics(result["messages"], finalize=True)] == ["requested", "completed"]
    assert not any(isinstance(message, HumanMessage) and message.name == "sp_clause_semantic_review_feedback" for message in result["messages"])


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("no_issue", [False, True])
def test_semantic_scope_http_preserves_one_pass_and_model_owned_empty_or_nonempty_final(monkeypatch, asynchronous, no_issue):
    import httpx
    from langchain.agents import create_agent

    from deerflow.agents.thread_state import ThreadState
    from deerflow.models.vllm_provider import VllmChatModel
    from deerflow.sp.semantic_review import collect_semantic_review_diagnostics

    source = "Payment is due on 1 January.\nBoth parties agree on that date." if no_issue else SOURCE
    final = "[]" if no_issue else GOOD
    source_messages = reads()
    source_messages[-1].content = source
    payloads = []

    def endpoint(request):
        payloads.append(json.loads(request.content))
        assert len(payloads) <= 2  # The scope clarification cannot add another review.
        return httpx.Response(
            200,
            json={
                "id": f"scope-fixture-{len(payloads)}",
                "object": "chat.completion",
                "created": 0,
                "model": "Qwen3-semantic-scope-fixture",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": final}, "finish_reason": "stop"}],
            },
        )

    transport = httpx.MockTransport(endpoint)

    def graph(**clients):
        model = VllmChatModel(model="Qwen3-semantic-scope-fixture", api_key="fixture-key", base_url="http://fixture.invalid/v1", max_retries=0, **clients)
        return create_agent(model=model, tools=[], middleware=[middleware(monkeypatch)], state_schema=ThreadState)

    state = {"messages": [HumanMessage(content="Audit the complete public document using the bound task."), *source_messages]}
    if asynchronous:

        async def run():
            async with httpx.AsyncClient(transport=transport) as client:
                return await graph(http_async_client=client).ainvoke(state)

        result = asyncio.run(run())
    else:
        with httpx.Client(transport=transport) as client:
            result = graph(http_client=client).invoke(state)
    assert len(payloads) == 2
    note = payloads[1]["messages"][-1]
    assert note["name"] == "sp_clause_semantic_review_feedback"
    assert_public_issue_scope(note["content"])
    assert "every supported distinct finding" in note["content"]
    assert "count limit" in note["content"]
    assert "host supplies no replacement text or labels" in note["content"]
    assert all(any(message.get("content") == source for message in payload["messages"]) for payload in payloads)
    assert result["messages"][-1].content == final
    diagnostics = collect_semantic_review_diagnostics(result["messages"], finalize=True)
    assert [info["status"] for info in diagnostics] == ["requested", "completed"]
    assert [info["candidate_items"] for info in diagnostics] == ([0, 0] if no_issue else [1, 1])


@pytest.mark.parametrize("asynchronous", [False, True])
def test_successful_file_rewrites_share_one_semantic_budget_then_quote_budget(monkeypatch, tmp_path, asynchronous):
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.tools import tool

    from deerflow.agents.thread_state import ThreadState
    from deerflow.sp.semantic_review import collect_semantic_review_diagnostics

    path = "/mnt/user-data/outputs/audit.json"
    writes = []

    @tool
    def write_file(path: str, content: str, append: bool = False) -> str:
        """Execute a real complete-array write in the isolated test directory."""
        assert not append
        (tmp_path / "audit.json").write_text(content)
        writes.append(content)
        return "File written successfully."

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    def call(body, index):
        return AIMessage(content="", tool_calls=[{"name": "write_file", "id": f"write-{index}", "args": {"path": path, "content": body}}])

    receipt = json.dumps({"artifact_content": None, "artifact_metadata": {"completion_status": "complete", "created_paths": [path]}})
    model = Model(responses=[call(GOOD, 1), call(WRAPPED, 2), call(WRAPPED, 3), call(GOOD, 4), AIMessage(content=receipt)])
    graph = create_agent(model=model, tools=[write_file], middleware=[middleware(monkeypatch, quote=True)], state_schema=ThreadState)
    state = {"messages": [HumanMessage(content="Audit and deliver the actual file."), *reads()]}
    result = asyncio.run(graph.ainvoke(state)) if asynchronous else graph.invoke(state)
    assert writes == [GOOD, WRAPPED, WRAPPED, GOOD]
    assert (tmp_path / "audit.json").read_text() == GOOD
    assert result["messages"][-1].content == receipt
    info = collect_semantic_review_diagnostics(result["messages"], finalize=True)
    assert [item["status"] for item in info] == ["requested", "completed"]
    assert all(item["candidate_kind"] == "write_file" for item in info)


def test_unchanged_file_receipt_cannot_claim_a_model_executed_review_rewrite(monkeypatch):
    check = middleware(monkeypatch)
    path = "/mnt/user-data/outputs/audit.json"
    messages = [
        *reads(),
        AIMessage(content="", tool_calls=[{"name": "write_file", "id": "write", "args": {"path": path, "content": GOOD}}]),
        ToolMessage(content="File written successfully.", name="write_file", tool_call_id="write", id="original-write"),
    ]
    messages[-1] = check.before_model({"messages": messages}, None)["messages"][0]
    receipt = json.dumps({"artifact_content": None, "artifact_metadata": {"completion_status": "complete", "created_paths": [path]}})
    messages.append(AIMessage(content=receipt))
    update = check.after_model({"messages": messages}, None)
    info = update["messages"][0].additional_kwargs["sp_clause_semantic_review"]
    assert info["status"] == "exhausted" and info["reason"] == "file_not_rewritten_after_request"
    assert update["messages"][0].content == receipt and "jump_to" not in update


def test_host_only_telemetry_finalizes_incomplete_review_and_rejects_model_claims(monkeypatch):
    from deerflow.sp.semantic_review import collect_semantic_review_diagnostics, emit_semantic_review_events
    from deerflow.sp.subagents.dr2_adapter import normalize_dr2_subagent_result

    captured = []
    check = middleware(monkeypatch, captured_sources=lambda: captured)
    initial = AIMessage(content=GOOD, id="captured-first")
    captured.append(initial.model_dump())
    marked = check.after_model({"messages": [*reads(), initial]}, None)["messages"][0]
    assert captured[0]["additional_kwargs"]["sp_clause_semantic_review"]["status"] == "requested"
    stopped = AIMessage(content="Unfinished review [TOKEN BUDGET EXCEEDED]")
    diagnostics = collect_semantic_review_diagnostics([marked, stopped], finalize=True)
    assert [item["status"] for item in diagnostics] == ["requested", "exhausted"]
    assert diagnostics[-1]["reason"] == "no_qualified_candidate_after_request"
    raw = SimpleNamespace(status="completed", result=GOOD, ai_messages=[marked.model_dump(), stopped.model_dump()])
    normalized = normalize_dr2_subagent_result(raw)
    assert normalized.artifact_metadata["clause_semantic_review"] == diagnostics
    events = []
    emit_semantic_review_events(raw, events.append)
    assert events == [{"type": "clause_semantic_review", "action_id": "audit", "diagnostic": info} for info in diagnostics]
    assert SOURCE not in json.dumps(events) and "explanation" not in json.dumps(events)
    raw.result = json.dumps({"artifact_metadata": {"clause_semantic_review": [{"status": "completed"}]}})
    raw.ai_messages = []
    assert "clause_semantic_review" not in normalize_dr2_subagent_result(raw).artifact_metadata


def test_default_disabled_keeps_one_model_call_and_the_original_final(monkeypatch):
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from deerflow.agents.thread_state import ThreadState

    monkeypatch.delenv("SP_CLAUSE_SEMANTIC_REVIEW", raising=False)
    monkeypatch.delenv("SP_CLAUSE_QUOTE_REVIEW", raising=False)
    model = FakeMessagesListChatModel(responses=[AIMessage(content=GOOD)])
    check = PublicArtifactFormatRepairMiddleware(contract(), action_id="audit", auditor_role="reporter", skill_names=["clause"])
    result = create_agent(model=model, tools=[], middleware=[check], state_schema=ThreadState).invoke({"messages": [*reads(), HumanMessage(content="Audit the complete source.")]})
    assert len([message for message in result["messages"] if isinstance(message, AIMessage) and not message.tool_calls]) == 1
    assert result["messages"][-1].content == GOOD
    assert "sp_clause_semantic_review" not in result["messages"][-1].additional_kwargs


def test_parallel_tool_batch_delivers_semantic_request_after_the_last_tool_result(monkeypatch):
    from deerflow.sp.semantic_review import augment_semantic_review_request, state_with_review_update

    check = middleware(monkeypatch)
    path = "/mnt/user-data/outputs/audit.json"
    write = {"name": "write_file", "id": "write", "args": {"path": path, "content": GOOD}}
    read = {"name": "read_file", "id": "reread", "args": {"path": SOURCE_PATH}}
    messages = [*reads(), AIMessage(content="", tool_calls=[write, read]), ToolMessage(content="File written successfully.", name="write_file", tool_call_id="write"), ToolMessage(content=SOURCE, name="read_file", tool_call_id="reread")]
    state = {"messages": messages}
    update = check.before_model(state, None)
    projected = state_with_review_update(state, update)
    assert [message.content for message in projected["messages"]] == [message.content for message in messages]

    class Request:
        messages = projected["messages"]

        def override(self, **kwargs):
            return SimpleNamespace(**kwargs)

    request = augment_semantic_review_request(Request())
    assert request.messages[-1].name == "sp_clause_semantic_review_feedback"
    assert "write_file again" in request.messages[-1].content


def test_reading_during_file_review_neither_completes_nor_reopens_the_review(monkeypatch):
    check = middleware(monkeypatch, quote=True)
    path = "/mnt/user-data/outputs/audit.json"
    messages = [
        *reads(),
        AIMessage(content="", tool_calls=[{"name": "write_file", "id": "write-first", "args": {"path": path, "content": GOOD}}]),
        ToolMessage(content="File written successfully.", name="write_file", tool_call_id="write-first"),
    ]
    messages[-1] = check.before_model({"messages": messages}, None)["messages"][0]
    messages.extend([AIMessage(content="", tool_calls=[{"name": "read_file", "id": "review-read", "args": {"path": SOURCE_PATH}}]), ToolMessage(content=SOURCE, name="read_file", tool_call_id="review-read")])
    assert check.before_model({"messages": messages}, None) is None
    messages.extend(
        [AIMessage(content="", tool_calls=[{"name": "write_file", "id": "write-reviewed", "args": {"path": path, "content": GOOD}}]), ToolMessage(content="File written successfully.", name="write_file", tool_call_id="write-reviewed")]
    )
    assert check.before_model({"messages": messages}, None)["messages"][0].additional_kwargs["sp_clause_semantic_review"]["status"] == "completed"


@pytest.mark.parametrize("damage", ["failed", "append", "unpaired"])
def test_unbacked_or_partial_file_write_does_not_start_semantic_review(monkeypatch, damage):
    check = middleware(monkeypatch)
    args = {"path": "/mnt/user-data/outputs/audit.json", "content": GOOD, "append": damage == "append"}
    messages = [
        *reads(),
        AIMessage(content="", tool_calls=[{"name": "write_file", "id": "write", "args": args}]),
        ToolMessage(content="File written successfully.", name="write_file", tool_call_id="write", status="error" if damage == "failed" else "success"),
    ]
    if damage == "unpaired":
        messages.pop(-2)
    assert check.before_model({"messages": messages}, None) is None


@pytest.mark.parametrize("field,value", [("status", {}), ("candidate_kind", []), ("attempt", True), ("candidate_items", -1), ("reason", {"untrusted": "text"})])
def test_semantic_telemetry_discards_malformed_annotations(field, value):
    from deerflow.sp.semantic_review import collect_semantic_review_diagnostics

    info = {"status": "exhausted", "scope": "audit", "attempt": 1, "candidate_kind": "raw_final", "candidate_items": 1, "reason": "no_qualified_candidate_after_request"}
    info[field] = value
    assert collect_semantic_review_diagnostics([AIMessage(content=GOOD, additional_kwargs={"sp_clause_semantic_review": info})], finalize=True) == []
