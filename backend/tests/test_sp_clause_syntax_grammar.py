"""Existing final turns can use grammar without replacing model bytes.

Mock HTTP verifies the actual native serializer, not deployed grammar support.
"""

import asyncio
import json
import os
from copy import deepcopy

import httpx
import pytest
from dcc_harness.data import InputFile, PublicTask
from dcc_harness.preprocessing.output_contracts import public_output_contract
from jsonschema import Draft202012Validator
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from deerflow.agents.thread_state import ThreadState
from deerflow.models.vllm_provider import VllmChatModel
from deerflow.sp.public_format_repair import PublicArtifactFormatRepairMiddleware
from deerflow.sp.source_prefetch import SourcePrefetchSettings, SourcePrefetchVisibilityMiddleware
from deerflow.sp.syntax_grammar import SyntaxGrammarRequestGate, collect_syntax_grammar_diagnostics, syntax_repair_schema

PATH = "/mnt/user-data/uploads/document.txt"
SOURCE = 'The definition says "Information");tion");\nPayment is due on 1 January.'
GOOD = json.dumps([{"text": SOURCE, "explanation": "The definition contains an incomplete fragment."}], ensure_ascii=False)
BAD = GOOD.replace(';tion\\");', ';tion");')
WRAPPED = json.dumps([{"text": '"' + SOURCE + '"', "explanation": "The definition contains an incomplete fragment."}])
PARTIAL = json.dumps({"summary": "The audit remains incomplete.", "artifact_content": None, "artifact_metadata": {"completion_status": "partial", "evidence_gaps": ["A substantive requirement remains unresolved."]}})


def contract():
    # Use the actual public schema, including its nonblank substring pattern.
    return public_output_contract(PublicTask(benchmark="clause", prompt="Audit the document.", files=(InputFile(name="document.txt", text=SOURCE),)))


def reads():
    return [AIMessage(content="", tool_calls=[{"name": "read_file", "id": "original-read", "args": {"path": PATH}}]), ToolMessage(content=SOURCE, name="read_file", tool_call_id="original-read")]


def run_http(monkeypatch, responses, *, asynchronous=False, backend="sglang", damage=None, reviews=False, enabled=True, source_text=None, beyond_eof=False):
    monkeypatch.setenv("SP_CLAUSE_SYNTAX_GRAMMAR", "1" if enabled else "0")
    monkeypatch.setenv("SP_CLAUSE_SEMANTIC_REVIEW", "1" if reviews else "0")
    monkeypatch.setenv("SP_CLAUSE_QUOTE_REVIEW", "1" if reviews else "0")
    monkeypatch.setattr("deerflow.sp.source_prefetch.count_source_prompt_tokens", lambda *a, **k: 20)
    source = reads()
    if source_text is not None:
        source[-1].content = source_text
    if beyond_eof:
        total = len(source[-1].content.splitlines())
        source.extend(
            [
                AIMessage(content="", tool_calls=[{"name": "read_file", "id": "beyond-eof", "args": {"path": PATH, "start_line": total + 1, "end_line": total + 50}}]),
                ToolMessage(content=f"(empty: requested range starts after end of file; total_lines={total})", name="read_file", tool_call_id="beyond-eof"),
            ]
        )
    captured = [m.model_dump() for m in source]
    public = contract() if source_text is None else public_output_contract(PublicTask(benchmark="clause", prompt="Audit the supplied document.", files=(InputFile(name="document.txt", text=source_text),)))
    role, skills = "reporter", ["clause"]
    if damage == "missing":
        source = []
    elif damage == "altered":
        source[-1].content = SOURCE.replace("1 January", "2 January")
    elif damage == "untrusted":
        captured = []
    elif damage == "pending":
        source.append(AIMessage(content="", tool_calls=[{"name": "read_file", "id": "pending", "args": {"path": PATH}}]))
    elif damage == "truncated":
        source[-1].content += "\n[truncated: more source remains]"
    elif damage == "role":
        role = "researcher"
    elif damage == "skill":
        skills = ["other"]
    elif damage == "schema":
        public["output_schema"]["items"]["additionalProperties"] = True
    elif damage == "budget":
        monkeypatch.setattr("deerflow.sp.source_prefetch.count_source_prompt_tokens", lambda *a, **k: 100_000)
    payloads, headers = [], []

    def endpoint(request):
        payloads.append(json.loads(request.content))
        headers.append(dict(request.headers))
        response = responses[len(payloads) - 1]
        if isinstance(response, BaseException):
            raise response
        content, finish = response if isinstance(response, tuple) else (response, "stop")
        return httpx.Response(
            200,
            json={
                "id": f"response-{len(payloads)}",
                "object": "chat.completion",
                "created": 0,
                "model": "Qwen3-test",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50},
            },
        )

    class Capture(AgentMiddleware):
        def after_model(self, state, runtime):
            latest = state["messages"][-1]
            captured.append(latest.model_dump())

        async def aafter_model(self, state, runtime):
            return self.after_model(state, runtime)

    @tool
    def read_file(path: str) -> str:
        """Read original source."""
        assert path == PATH
        return SOURCE if source_text is None else source_text

    @tool
    def write_file(path: str, content: str) -> str:
        """Optional model draft tool."""
        raise AssertionError("This fixture returns public finals directly")

    def graph(**clients):
        model = VllmChatModel(
            model="Qwen3-test",
            api_key="fixture-key",
            base_url="http://fixture.invalid/v1",
            max_retries=0,
            syntax_grammar_backend=backend,
            temperature=0.7,
            top_p=0.9,
            max_tokens=256,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}, "top_k": 23, "min_p": 0.1},
            **clients,
        )
        gate = SyntaxGrammarRequestGate(captured, contract=public, skill_names=skills, auditor_role=role, scope="audit")
        check = PublicArtifactFormatRepairMiddleware(public, action_id="audit", auditor_role=role, skill_names=skills, captured_sources=lambda: captured, syntax_gate=gate)
        visibility = SourcePrefetchVisibilityMiddleware(
            captured,
            contract=public,
            settings=SourcePrefetchSettings(tokenizer="fixture", context_tokens=65536, response_tokens=256),
            auditor_role=role,
            skill_names=skills,
            syntax_scope="audit",
            syntax_gate=gate,
        )
        return create_agent(model=model, tools=[read_file, write_file], middleware=[check, Capture(), visibility], state_schema=ThreadState)

    state = {"messages": [HumanMessage(content="Audit the original document."), *source]}
    transport = httpx.MockTransport(endpoint)
    if asynchronous:

        async def run():
            async with httpx.AsyncClient(transport=transport) as client:
                return await graph(http_async_client=client).ainvoke(state)

        result = asyncio.run(run())
    else:
        with httpx.Client(transport=transport) as client:
            result = graph(http_client=client).invoke(state)
    return result, payloads, headers, captured


@pytest.mark.parametrize("asynchronous", [False, True])
def test_real_http_constrains_existing_format_semantic_and_quote_final_turns(monkeypatch, asynchronous):
    result, payloads, headers, captured = run_http(monkeypatch, [BAD, GOOD, WRAPPED, GOOD], asynchronous=asynchronous, reviews=True)
    assert len(payloads) == 4
    constrained = [i for i, body in enumerate(payloads) if "response_format" in body]
    assert constrained == [1, 2, 3]
    grammar = payloads[1]["response_format"]
    assert grammar["type"] == "json_schema"
    assert grammar["json_schema"]["schema"] == syntax_repair_schema(contract())
    assert not payloads[1].get("tools") and "tool_choice" not in payloads[1]
    assert {t["function"]["name"] for t in payloads[0]["tools"]} == {"read_file", "write_file"}
    assert all(not payloads[i].get("tools") for i in constrained)
    for index in constrained:
        note = payloads[index]["messages"][-1]
        assert note["name"] == "sp_clause_syntax_final"
        assert "Source defects are audit content" in note["content"]
        assert "after exactly one JSON decode" in note["content"]
        assert "physical source lines 1..2" in note["content"]
        assert "line 3 is beyond EOF" in note["content"]
        assert "candidate quotation" in note["content"] and "not a source-read failure" in note["content"]
        assert "genuinely unavailable or unresolved" in note["content"]
        assert SOURCE not in note["content"]
    for body in payloads:
        assert body["temperature"] == 0.7 and body["top_p"] == 0.9 and body["max_completion_tokens"] == 256
        assert body["top_k"] == 23 and body["min_p"] == 0.1 and body["chat_template_kwargs"] == {"enable_thinking": False}
        assert any(m.get("content") == SOURCE for m in body["messages"])
    assert any(m.get("name") == "sp_clause_semantic_review_feedback" for m in payloads[2]["messages"])
    assert any(m.get("name") == "sp_clause_quote_review_feedback" for m in payloads[3]["messages"])
    assert all("x-stainless-helper-method" not in h for h in headers)
    assert result["messages"][-1].content == GOOD
    diagnostics = collect_syntax_grammar_diagnostics(captured)
    assert [(d["phase"], d["status"]) for d in diagnostics] == [(phase, status) for phase in ("format", "semantic", "quote") for status in ("applied", "response_received")]
    assert all(d["attempt"] == 1 and d["backend"] == "sglang" for d in diagnostics)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("partial", [False, True])
def test_current_cross_page_source_stays_visible_and_does_not_force_completion(monkeypatch, asynchronous, partial):
    source = "Public agreement.\nPayment must be made to\nPage 12\nExample Company within ten days.\nSigned below."
    candidate = json.dumps([{"text": source, "explanation": "A synthetic public-document finding."}])
    final = PARTIAL if partial else candidate
    result, payloads, _, captured = run_http(monkeypatch, [candidate, final], asynchronous=asynchronous, reviews=True, source_text=source, beyond_eof=True)
    assert len(payloads) == 2  # Reuse the one existing semantic review turn.
    notes = {m.get("name"): m.get("content", "") for m in payloads[1]["messages"] if m.get("role") == "user"}
    for name in ("sp_clause_semantic_review_feedback", "sp_clause_syntax_final"):
        assert "public document actually supplied" in notes[name]
        assert "physical line breaks and page boundaries" in notes[name]
        assert "Do not cut a quotation" in notes[name]
        assert "unread required ranges" in notes[name]
        assert source not in notes[name]  # No host-authored quotation or replacement answer.
    assert all(any(m.get("role") == "tool" and m.get("content") == source for m in body["messages"]) for body in payloads)
    assert not payloads[1].get("tools") and "response_format" in payloads[1]
    assert "physical source lines 1..5" in notes["sp_clause_syntax_final"]
    assert "line 6 is beyond EOF" in notes["sp_clause_syntax_final"]
    assert "candidate quotation" in notes["sp_clause_syntax_final"] and "not a source-read failure" in notes["sp_clause_syntax_final"]
    assert any(m.get("name") == "sp_clause_current_source_visibility" for m in payloads[0]["messages"])
    assert result["messages"][-1].content == final
    assert [(d["phase"], d["status"]) for d in collect_syntax_grammar_diagnostics(captured)] == [("semantic", "applied"), ("semantic", "response_received")]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("partial", [False, True])
def test_ordinary_http_exposes_only_current_source_transport_facts_with_real_eof(monkeypatch, asynchronous, partial):
    source = "A long physical source line with many wrapped display lines. " * 100 + "\nPage 17\nThe sentence continues across this layout boundary."
    candidate = json.dumps([{"text": source, "explanation": "A synthetic source-only finding."}])
    final = PARTIAL if partial else candidate
    result, payloads, _, _ = run_http(monkeypatch, [final], asynchronous=asynchronous, enabled=False, source_text=source, beyond_eof=True)
    assert len(payloads) == 1 and "response_format" not in payloads[0]
    assert {t["function"]["name"] for t in payloads[0]["tools"]} == {"read_file", "write_file"}
    facts = [m for m in payloads[0]["messages"] if m.get("name") == "sp_clause_current_source_visibility"]
    assert len(facts) == 1
    note = facts[0]["content"]
    assert PATH in note and "physical source lines 1..3" in note and "line 4 is beyond EOF" in note
    assert "visual display lines" in note and "not a source-read failure" in note
    assert "not substantive audit completion" in note and "partial/blocked" in note
    assert source not in note and "synthetic source-only finding" not in note
    assert any(m.get("role") == "tool" and m.get("content") == source for m in payloads[0]["messages"])
    assert any(m.get("role") == "tool" and m.get("content") == "(empty: requested range starts after end of file; total_lines=3)" for m in payloads[0]["messages"])
    assert result["messages"][-1].content == final


@pytest.mark.parametrize(
    "damage", ["missing", "changed", "args", "no_capture", "old_visibility", "truncated", "externalized", "failed", "empty", "partial_range", "eof_only", "conflict", "pending", "budget", "counter_error", "no_settings", "bad_skills"]
)
@pytest.mark.parametrize("asynchronous", [False, True])
def test_current_source_fact_never_uses_uncertain_or_historical_proof(monkeypatch, damage, asynchronous):
    monkeypatch.setenv("SP_CLAUSE_SYNTAX_GRAMMAR", "0")
    source = reads()
    source[-1].additional_kwargs.update(sp_host_source_prefetch=True, sp_host_source_prefetch_visible=True)
    captured = [m.model_dump() for m in source]
    if damage == "missing":
        source = []
    elif damage == "changed":
        source[-1] = source[-1].model_copy(update={"content": SOURCE + " altered"})
    elif damage == "args":
        source[0] = AIMessage(content="", tool_calls=[{"name": "read_file", "id": "original-read", "args": {"path": PATH, "end_line": 1}}])
    elif damage == "no_capture":
        captured = []
    elif damage == "old_visibility":
        source = [HumanMessage(content="An old summary said every source was read.")]
    elif damage in {"truncated", "externalized", "failed", "empty", "partial_range"}:
        if damage == "truncated":
            source[-1].content += " [truncated: source preview]"
        elif damage == "externalized":
            source[-1].content = "[Full read_file output saved to /mnt/user-data/outputs/source.txt]"
        elif damage == "failed":
            source[-1].status = "error"
        elif damage == "empty":
            source[-1].content = "(empty)"
        else:
            source[0] = AIMessage(content="", tool_calls=[{"name": "read_file", "id": "original-read", "args": {"path": PATH, "start_line": 1, "end_line": 1}}])
            source[-1].content = SOURCE.splitlines()[0]
        captured = [m.model_dump() for m in source]
    elif damage == "eof_only":
        source = [
            AIMessage(content="", tool_calls=[{"name": "read_file", "id": "after-eof", "args": {"path": PATH, "start_line": 3, "end_line": 50}}]),
            ToolMessage(content="(empty: requested range starts after end of file; total_lines=2)", name="read_file", tool_call_id="after-eof"),
        ]
        captured = [m.model_dump() for m in source]
    elif damage == "conflict":
        source.extend([AIMessage(content="", tool_calls=[{"name": "read_file", "id": "conflict", "args": {"path": PATH}}]), ToolMessage(content=SOURCE.replace("1 January", "2 January"), name="read_file", tool_call_id="conflict")])
        captured = [m.model_dump() for m in source]
    elif damage == "pending":
        source.append(AIMessage(content="", tool_calls=[{"name": "read_file", "id": "pending", "args": {"path": PATH}}]))
    counted = []

    def counter(messages, *args):
        counted.append(messages)
        if damage == "counter_error":
            raise RuntimeError("Tokenizer is unavailable")
        return 100_000 if damage == "budget" else 20

    monkeypatch.setattr("deerflow.sp.source_prefetch.count_source_prompt_tokens", counter)
    request = ModelRequest(
        model=VllmChatModel(model="fixture", api_key="fixture"),
        messages=[HumanMessage(content="Audit original sources."), *source],
        tools=[{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}],
        state={"sp_source_access": {"reads": {PATH: [[1, 2]]}}},
    )
    before = [m.model_dump() for m in request.messages]
    visibility = SourcePrefetchVisibilityMiddleware(
        captured,
        contract=contract(),
        settings=None if damage == "no_settings" else SourcePrefetchSettings(tokenizer="fixture", context_tokens=65536, response_tokens=256),
        auditor_role="reporter",
        skill_names=1 if damage == "bad_skills" else ["clause"],
    )
    if asynchronous:

        async def handler(current):
            return current

        result = asyncio.run(visibility.awrap_model_call(request, handler))
    else:
        result = visibility.wrap_model_call(request, lambda current: current)
    assert not any(m.name == "sp_clause_current_source_visibility" for m in result.messages)
    assert [m.model_dump() for m in result.messages] == before
    assert result.tools == request.tools
    if damage in {"budget", "counter_error"}:
        assert counted and any(m.name == "sp_clause_current_source_visibility" for m in counted[0])


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("final", [PARTIAL, (GOOD[:-9], "length")])
def test_native_partial_and_provider_length_bytes_exit_without_parse_exception_or_review(monkeypatch, asynchronous, final):
    result, payloads, _, _ = run_http(monkeypatch, [BAD, final], asynchronous=asynchronous, reviews=True)
    assert len(payloads) == 2 and "response_format" in payloads[1]
    expected = final[0] if isinstance(final, tuple) else final
    assert result["messages"][-1].content == expected
    if isinstance(final, tuple):
        assert result["messages"][-1].response_metadata["finish_reason"] == "length"
    else:
        Draft202012Validator(syntax_repair_schema(contract())).validate(json.loads(final))


@pytest.mark.parametrize("damage", ["missing", "altered", "untrusted", "pending", "truncated", "role", "skill", "schema", "budget"])
def test_unproved_source_scope_or_context_uses_original_format_path(monkeypatch, damage):
    result, payloads, _, captured = run_http(monkeypatch, [BAD, GOOD], damage=damage)
    assert len(payloads) == 2 and not any("response_format" in body for body in payloads)
    assert not any("Source defects are audit content" in str(message.get("content")) for body in payloads for message in body["messages"])
    assert not any("HOST CURRENT PUBLIC-SOURCE TRANSPORT FACTS" in str(message.get("content")) for body in payloads for message in body["messages"])
    assert result["messages"][-1].content == GOOD
    assert not any(d["status"] == "applied" for d in collect_syntax_grammar_diagnostics(captured))


@pytest.mark.parametrize("enabled,backend", [(False, "sglang"), (True, None), (True, "unsupported")])
def test_default_and_undeclared_backend_keep_original_requests(monkeypatch, enabled, backend):
    result, payloads, _, _ = run_http(monkeypatch, [BAD, GOOD], enabled=enabled, backend=backend)
    assert result["messages"][-1].content == GOOD
    assert len(payloads) == 2 and not any("response_format" in body for body in payloads)


def test_unavailable_capability_check_falls_back_without_a_model_retry(monkeypatch):
    def unavailable(self):
        raise RuntimeError("capability unavailable")

    monkeypatch.setattr(VllmChatModel, "public_syntax_grammar_capability", unavailable)
    result, payloads, _, _ = run_http(monkeypatch, [BAD, GOOD])
    assert result["messages"][-1].content == GOOD
    assert len(payloads) == 2 and not any("response_format" in body for body in payloads)


def test_each_existing_format_attempt_can_constrain_without_adding_turns(monkeypatch):
    result, payloads, _, captured = run_http(monkeypatch, [BAD, BAD, BAD])
    assert len(payloads) == 3
    assert [i for i, body in enumerate(payloads) if "response_format" in body] == [1, 2]
    assert result["messages"][-1].content == BAD
    assert [d["attempt"] for d in collect_syntax_grammar_diagnostics(captured) if d["status"] == "applied"] == [1, 2]
    assert [d["status"] for d in collect_syntax_grammar_diagnostics(captured)].count("response_received") == 2


def test_second_format_after_damaged_semantic_response_keeps_original_budgets(monkeypatch):
    result, payloads, _, captured = run_http(monkeypatch, [BAD, GOOD, BAD, GOOD], reviews=True)
    assert result["messages"][-1].content == GOOD
    assert len(payloads) == 4
    assert [i for i, body in enumerate(payloads) if "response_format" in body] == [1, 2, 3]
    assert [(d["phase"], d["attempt"]) for d in collect_syntax_grammar_diagnostics(captured) if d["status"] == "applied"] == [("format", 1), ("semantic", 1), ("format", 2)]


def test_both_existing_quote_attempts_are_constrained_without_extra_reviews(monkeypatch):
    result, payloads, _, captured = run_http(monkeypatch, [GOOD, WRAPPED, WRAPPED, GOOD], reviews=True)
    assert len(payloads) == 4 and result["messages"][-1].content == GOOD
    assert [(d["phase"], d["attempt"]) for d in collect_syntax_grammar_diagnostics(captured) if d["status"] == "applied"] == [("semantic", 1), ("quote", 1), ("quote", 2)]


@pytest.mark.parametrize("final", [PARTIAL, (GOOD[:-9], "length")])
def test_review_phase_preserves_partial_or_length_bytes_without_another_turn(monkeypatch, final):
    result, payloads, _, captured = run_http(monkeypatch, [GOOD, final], reviews=True)
    assert len(payloads) == 2 and "response_format" in payloads[1]
    assert result["messages"][-1].content == (final[0] if isinstance(final, tuple) else final)
    assert [d["phase"] for d in collect_syntax_grammar_diagnostics(captured) if d["status"] == "response_received"] == ["semantic"]


def _nominated_request(monkeypatch, phase="semantic", *, nominate=True):
    monkeypatch.setenv("SP_CLAUSE_SYNTAX_GRAMMAR", "1")
    original = reads()
    captured = [m.model_dump() for m in original]
    gate = SyntaxGrammarRequestGate(captured, contract=contract(), skill_names=["clause"], auditor_role="reporter", scope="audit")
    candidate = AIMessage(content=BAD if phase == "format" else GOOD, id="actual-final")
    candidate.additional_kwargs["sp_public_format_feedback"] = {"attempt": 1, "scope": "audit", "syntax_grammar_candidate": True}
    if nominate:
        assert gate.nominate(candidate, phase=phase, attempt=1)
    model = VllmChatModel(model="fixture", api_key="fixture", syntax_grammar_backend="sglang")
    feedback = {"format": "sp_public_format_feedback", "semantic": "sp_clause_semantic_review_feedback", "quote": "sp_clause_quote_review_feedback"}[phase]
    request = ModelRequest(
        model=model,
        messages=[HumanMessage(content="Audit source."), *original, candidate, HumanMessage(content="Existing final feedback.", name=feedback, additional_kwargs={"hide_from_ui": True})],
        tools=[{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}],
    )
    return gate, request, captured


def test_serialized_model_markers_without_host_nomination_cannot_enable_grammar(monkeypatch):
    gate, request, _ = _nominated_request(monkeypatch, "format", nominate=False)
    assert gate.prepare(request) is None


@pytest.mark.parametrize("damage", ["id", "content", "new_user", "later_tool", "stop"])
def test_stale_candidate_or_stopped_request_consumes_nomination_without_replay(monkeypatch, damage):
    gate, request, _ = _nominated_request(monkeypatch)
    messages = list(request.messages)
    if damage == "id":
        messages[-2] = messages[-2].model_copy(update={"id": "different-final"})
    elif damage == "content":
        messages[-2] = messages[-2].model_copy(update={"content": WRAPPED})
    elif damage == "new_user":
        messages.append(HumanMessage(content="A new actual instruction."))
    elif damage == "later_tool":
        messages.append(ToolMessage(content="Unrelated later result", name="read_file", tool_call_id="other"))
    else:
        messages[-2] = messages[-2].model_copy(update={"response_metadata": {"finish_reason": "length"}})
    assert gate.prepare(request.override(messages=messages)) is None
    assert gate.prepare(request) is None
    assert not gate.nominate(request.messages[-2], phase="semantic", attempt=1)


@pytest.mark.parametrize("damage", ["source", "args", "pending", "constraint", "framework_constraint", "context"])
def test_each_review_request_rechecks_source_tools_constraints_and_context(monkeypatch, damage):
    gate, request, _ = _nominated_request(monkeypatch, "quote")
    messages = list(request.messages)
    if damage == "source":
        messages[2] = messages[2].model_copy(update={"content": SOURCE + " altered"})
    elif damage == "args":
        messages[1] = AIMessage(content="", tool_calls=[{"name": "read_file", "id": "original-read", "args": {"path": PATH, "end_line": 1}}])
    elif damage == "pending":
        messages.insert(-2, AIMessage(content="", tool_calls=[{"name": "read_file", "id": "pending", "args": {"path": PATH}}]))
    request = request.override(messages=messages, model_settings={"response_format": {"type": "json_object"}} if damage == "constraint" else {}, response_format={"type": "json_object"} if damage == "framework_constraint" else None)
    proposed = gate.prepare(request)
    if damage == "context":
        assert proposed is not None
        assert gate.finish(request, proposed, within_context=False) is request
    else:
        assert proposed is None
    assert gate.prepare(request) is None


def test_nomination_is_once_per_bounded_host_stage_and_next_ordinary_tools_restore(monkeypatch):
    gate, request, captured = _nominated_request(monkeypatch)
    proposed = gate.prepare(request)
    constrained = gate.finish(request, proposed, within_context=True)
    assert constrained.tools == []
    assert not gate.nominate(request.messages[-2], phase="semantic", attempt=1)
    assert not gate.nominate(request.messages[-2], phase="semantic", attempt=2)
    assert not gate.nominate(request.messages[-2], phase="quote", attempt=3)
    assert not gate.nominate(request.messages[-2], phase="invented", attempt=1)
    visibility = SourcePrefetchVisibilityMiddleware(captured, syntax_gate=gate)
    ordinary = request.override(messages=[*request.messages, AIMessage(content="Continue normal work.", id="next-turn")])
    assert visibility.wrap_model_call(ordinary, lambda current: current).tools == request.tools


@pytest.mark.parametrize("kind", ["artifact_content", "file_receipt"])
def test_existing_non_raw_final_review_keeps_tools_and_has_no_nomination(monkeypatch, kind):
    gate, request, captured = _nominated_request(monkeypatch, nominate=False)
    monkeypatch.setenv("SP_CLAUSE_SEMANTIC_REVIEW", "1")
    body = {"summary": "Ready.", "artifact_metadata": {"completion_status": "complete", "evidence_gaps": []}}
    messages = list(request.messages[:-2])
    if kind == "artifact_content":
        body["artifact_content"] = GOOD
    else:
        output = "/mnt/user-data/outputs/findings.json"
        body["artifact_metadata"]["created_paths"] = [output]
        messages += [AIMessage(content="", tool_calls=[{"id": "write", "name": "write_file", "args": {"path": output, "content": GOOD}}]), ToolMessage(content="written", name="write_file", tool_call_id="write")]
    messages.append(AIMessage(content=json.dumps(body), id="delivery"))
    check = PublicArtifactFormatRepairMiddleware(contract(), action_id="audit", auditor_role="reporter", skill_names=["clause"], captured_sources=lambda: captured, syntax_gate=gate)
    update = check.after_model({"messages": messages}, None)
    assert update["jump_to"] == "model"
    messages[-1] = update["messages"][0]
    assert gate.prepare(request.override(messages=messages)) is None


@pytest.mark.parametrize("candidate", ["not JSON", '{"text":"source"}', '[{"text":"source"}]', PARTIAL, (BAD, "length")])
def test_non_array_syntax_schema_mismatch_failure_or_capped_candidate_is_ineligible(monkeypatch, candidate):
    responses = [candidate] if candidate == PARTIAL or isinstance(candidate, tuple) else [candidate, GOOD]
    _, payloads, _, _ = run_http(monkeypatch, responses)
    assert not any("response_format" in body for body in payloads)


def test_union_preserves_public_validation_and_requires_real_failure_gaps():
    public = contract()
    original = deepcopy(public)
    schema = syntax_repair_schema(public)
    expected = deepcopy(public["output_schema"])
    del expected["items"]["properties"]["text"]["pattern"]
    assert schema["anyOf"][0] == expected and public == original
    validator = Draft202012Validator(schema)
    for value in [json.loads(GOOD), [], json.loads(PARTIAL)]:
        validator.validate(value)
    draft = json.loads(PARTIAL)
    draft["artifact_content"] = "Verified partial work remains available."
    validator.validate(draft)
    for value in [{"artifact_metadata": {"completion_status": "complete", "evidence_gaps": []}}, {"artifact_metadata": {"completion_status": "partial", "evidence_gaps": []}}]:
        assert list(validator.iter_errors(value))


@pytest.mark.parametrize(
    "text,valid",
    [
        ("", False),
        (" \n\t\r\v\f", False),
        ("\u00a0\u2003", False),
        ("1", True),
        ("§", True),
        ("1a", True),
        (SOURCE, True),
        ("A complete sentence. " * 128, True),
        ("Line one.\nLine two.", True),
        ('The "Scope" includes C:\\source.', True),
        ("中文条款。\n另一条款。", True),
        ("\n Source passage. \t", True),
        (r"\n\t", True),
    ],
)
@pytest.mark.parametrize("ensure_ascii", [False, True])
def test_provider_syntax_projection_keeps_host_nonblank_validation(text, valid, ensure_ascii):
    public = contract()
    before = deepcopy(public)
    schema = syntax_repair_schema(public)
    value = json.loads(json.dumps([{"text": text, "explanation": "Supported issue."}], ensure_ascii=ensure_ascii))
    assert Draft202012Validator(public["output_schema"]).is_valid(value) is valid
    assert Draft202012Validator(schema).is_valid(value)
    assert public == before
    schema["anyOf"][0]["items"]["properties"]["explanation"]["description"] = "Provider copy only."
    assert public == before


@pytest.mark.parametrize("pattern", [None, r"\S+", r"^\S$", r"[A-Z]+", r"[\s\S]*\S[\s\S]*"])
def test_unknown_or_absent_text_patterns_remain_unchanged(pattern):
    public = contract()
    text = public["output_schema"]["items"]["properties"]["text"]
    if pattern is None:
        text.pop("pattern")
    else:
        text["pattern"] = pattern
    public["output_schema"]["items"]["properties"]["explanation"]["pattern"] = r"\S"
    before = deepcopy(public)
    assert syntax_repair_schema(public)["anyOf"][0] == public["output_schema"]
    assert public == before


def test_optional_deployed_qwen_xgrammar_allows_long_text_continuation():
    """CPU only; opt in with a local tokenizer and the service's XGrammar install."""
    tokenizer_path = os.environ.get("SP_TEST_CLAUSE_GRAMMAR_TOKENIZER")
    if not tokenizer_path:
        pytest.skip("Set SP_TEST_CLAUSE_GRAMMAR_TOKENIZER for the deployed CPU compiler regression")
    xgrammar = pytest.importorskip("xgrammar")
    from transformers import AutoConfig, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, local_files_only=True)
    model_config = AutoConfig.from_pretrained(tokenizer_path, local_files_only=True)
    assert model_config.model_type == "qwen3_moe"
    stops = [tokenizer.eos_token_id]
    info = xgrammar.TokenizerInfo.from_huggingface(tokenizer, vocab_size=model_config.vocab_size, stop_token_ids=stops)
    compiler = xgrammar.GrammarCompiler(info, max_threads=2)
    public = contract()
    schema = syntax_repair_schema(public)
    original = deepcopy(schema)
    original["anyOf"][0] = deepcopy(public["output_schema"])
    old_context = compiler.compile_json_schema(original, any_whitespace=True)
    context = compiler.compile_json_schema(schema, any_whitespace=True)

    def accepts_wire(compiled, wire):
        matcher = xgrammar.GrammarMatcher(compiled)
        return matcher.accept_string(wire) and matcher.accept_token(stops[0])

    def accepts_complete(compiled, value, ensure_ascii=False):
        return accepts_wire(compiled, json.dumps(value, ensure_ascii=ensure_ascii))

    for text in ["1a", "The Agreement begins on the Effective Date.", SOURCE, "中文条款。\n另一条款。"]:
        value = [{"text": text, "explanation": "Supported issue."}]
        for ensure_ascii in (False, True):
            assert not accepts_complete(old_context, value, ensure_ascii)
            assert accepts_complete(context, value, ensure_ascii)
        Draft202012Validator(public["output_schema"]).validate(value)
    for invalid in ['[{"text":"The "Scope" applies.","explanation":"Issue."}]', '[{"text":"Line one.\nLine two.","explanation":"Issue."}]']:
        with pytest.raises(json.JSONDecodeError):
            json.loads(invalid)
        assert not accepts_wire(context, invalid)
    for prefix in ['[{"text":"1', '[{"text":"The Agreement']:
        old = xgrammar.GrammarMatcher(old_context)
        current = xgrammar.GrammarMatcher(context)
        assert current.accept_string(prefix)
        current_mask = xgrammar.allocate_token_bitmask(1, model_config.vocab_size)
        current.fill_next_token_bitmask(current_mask)
        continuation = tokenizer.encode(" source", add_special_tokens=False)[0]
        assert (int(current_mask[0, continuation // 32]) >> (continuation % 32)) & 1
        if old.accept_string(prefix):
            old_mask = xgrammar.allocate_token_bitmask(1, model_config.vocab_size)
            old.fill_next_token_bitmask(old_mask)
            assert not (int(old_mask[0, continuation // 32]) >> (continuation % 32)) & 1
    # The syntax projection deliberately allows empty/blank JSON strings. Only
    # the unchanged host schema decides whether the decoded artifact is valid.
    for text in ("", " ", "\n\t", "\u00a0\u2003"):
        whitespace = [{"text": text, "explanation": "Supported issue."}]
        assert accepts_complete(context, whitespace)
        assert not Draft202012Validator(public["output_schema"]).is_valid(whitespace)


def test_diagnostics_drop_untrusted_fields():
    events = collect_syntax_grammar_diagnostics([{"additional_kwargs": {"sp_clause_syntax_grammar": {"status": "applied", "scope": "audit", "attempt": 1, "backend": "sglang", "source": "must not escape", "candidate": BAD}}}])
    assert events == [{"status": "applied", "scope": "audit", "attempt": 1, "backend": "sglang"}]


def test_phase_diagnostics_are_whitelisted_and_legacy_records_keep_their_shape():
    legacy = {"status": "response_received", "scope": "audit", "attempt": 1, "backend": "sglang"}
    records = [legacy, {**legacy, "phase": "semantic"}, {**legacy, "phase": "quote", "attempt": 2}, {**legacy, "phase": "model-invented"}, {**legacy, "phase": "semantic", "attempt": 2}]
    messages = [{"additional_kwargs": {"sp_clause_syntax_grammar": row}} for row in records]
    assert collect_syntax_grammar_diagnostics(messages) == records[:3]


def test_provider_response_phase_comes_from_host_request_not_response_metadata():
    from langchain_core.outputs import ChatGeneration, ChatResult

    model = VllmChatModel(model="fixture", api_key="fixture", syntax_grammar_backend="sglang")
    message = AIMessage(content=GOOD, additional_kwargs={"sp_clause_syntax_grammar": {"phase": "forged-output"}})
    result = ChatResult(generations=[ChatGeneration(message=message)])
    received = model._syntax_response_diagnostic(result, {"_sp_clause_syntax_grammar": {"scope": "audit", "phase": "quote", "attempt": 2, "backend": "sglang"}})
    assert collect_syntax_grammar_diagnostics([received.generations[0].message]) == [{"status": "response_received", "scope": "audit", "attempt": 2, "backend": "sglang", "phase": "quote"}]


@pytest.mark.parametrize("damage", ["external_ref", "invalid_schema"])
def test_unsupported_schema_is_not_misclassified_as_only_json_syntax(monkeypatch, damage):
    from deerflow.sp.syntax_grammar import syntax_candidate_enabled

    monkeypatch.setenv("SP_CLAUSE_SYNTAX_GRAMMAR", "1")
    public = contract()
    if damage == "external_ref":
        public["output_schema"]["$ref"] = "https://example.invalid/schema"
    else:
        public["output_schema"]["maxItems"] = -1
    assert not syntax_candidate_enabled(public, ["clause"], "reporter", AIMessage(content=BAD))


@pytest.mark.parametrize("asynchronous", [False, True])
def test_cancelled_raw_request_is_not_replaced_or_retried(monkeypatch, asynchronous):
    # LangGraph wraps sync node cancellation and this LangChain version has a
    # pre-existing async gather bookkeeping bug for child CancelledError. Both
    # paths must behave exactly like the unchanged unconstrained control.
    errors = []
    for enabled in (False, True):
        with pytest.raises(BaseException) as caught:
            run_http(monkeypatch, [BAD, asyncio.CancelledError("original cancellation")], asynchronous=asynchronous, enabled=enabled)
        errors.append(caught.value)
    assert type(errors[0]) is type(errors[1]) and str(errors[0]) == str(errors[1])


def test_raw_create_provider_itself_propagates_original_async_cancellation():
    cancellation = asyncio.CancelledError("raw create cancelled")

    def endpoint(request):
        raise cancellation

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
            model = VllmChatModel(model="fixture", base_url="http://fixture.invalid/v1", api_key="fixture", http_async_client=client, syntax_grammar_backend="sglang", max_retries=0)
            with pytest.raises(asyncio.CancelledError) as caught:
                await model._agenerate([HumanMessage(content="source")], stream=False, _sp_clause_syntax_grammar={"schema": {"type": "array"}, "scope": "audit", "attempt": 1, "backend": "sglang"})
            assert caught.value is cancellation

    asyncio.run(run())


@pytest.mark.parametrize("mode,final,expected_status", [("repair", GOOD, "complete"), ("partial", PARTIAL, "partial"), ("length", GOOD[:-9], "partial")])
@pytest.mark.parametrize("enable_thinking", [None, False, True])
def test_reusable_service_probe_keeps_native_graph_and_original_output(monkeypatch, mode, final, expected_status, enable_thinking):
    import importlib.util
    from pathlib import Path

    probe_file = Path(__file__).resolve().parents[1] / "scripts/smoke_clause_syntax_grammar.py"
    spec = importlib.util.spec_from_file_location("syntax_service_probe", probe_file)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    monkeypatch.setenv("SP_CLAUSE_SYNTAX_GRAMMAR", "1")
    monkeypatch.setattr("deerflow.sp.source_prefetch.count_source_prompt_tokens", lambda *a, **k: 20)
    payloads = []
    observed_responses = []

    def endpoint(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": f"probe-{len(payloads)}",
                "object": "chat.completion",
                "created": 0,
                "model": "fixture",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": final}, "finish_reason": "length" if mode == "length" else "stop"}],
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
            settings = {} if enable_thinking is None else {"enable_thinking": enable_thinking}
            return await probe.run_probe(base_url="http://fixture.invalid/v1", model_name="fixture", tokenizer="fixture", max_tokens=256, mode=mode, http_async_client=client, response_observer=observed_responses.append, **settings)

    result = asyncio.run(run())
    assert result["raw_final"] == final and result["completion_status"] == expected_status
    assert len(result["model_responses"]) == result["service_model_responses"]
    assert result["model_responses"][0]["content"] == final
    assert result["model_responses"][0]["finish_reason"] == ("length" if mode == "length" else "stop")
    assert result["model_responses"][0]["syntax_grammar"][0]["status"] == "response_received"
    assert observed_responses == result["model_responses"]
    assert result["enable_thinking"] is bool(enable_thinking)
    assert all(body["chat_template_kwargs"]["enable_thinking"] is bool(enable_thinking) for body in payloads)
    assert "response_format" in payloads[0]
    assert all("response_format" in body for body in payloads)
    phases = ["format", "semantic"] if mode == "repair" else ["format"]
    assert [(d["phase"], d["status"]) for d in result["syntax_grammar"]] == [(phase, status) for phase in phases for status in ("applied", "response_received")]


@pytest.mark.parametrize("enable_thinking", [False, True])
def test_probe_cli_passes_and_records_explicit_thinking_setting(monkeypatch, tmp_path, enable_thinking):
    import importlib.util
    import sys
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("syntax_service_probe_cli", Path(__file__).resolve().parents[1] / "scripts/smoke_clause_syntax_grammar.py")
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    seen = []

    async def fake_probe(**kwargs):
        seen.append(kwargs["enable_thinking"])
        return {"raw_final": "[]", "model_responses": [], "stop_reason": None, "enable_thinking": kwargs["enable_thinking"]}

    monkeypatch.setattr(probe, "run_probe", fake_probe)
    args = ["probe", "--base-url", "http://fixture.invalid/v1", "--model", "fixture", "--tokenizer", "fixture", "--syntax-grammar-backend", "sglang", "--output-dir", str(tmp_path)]
    monkeypatch.setattr(sys, "argv", args + (["--enable-thinking"] if enable_thinking else []))
    probe.main()
    assert seen == [enable_thinking]
    assert json.loads((tmp_path / "report.json").read_text())["enable_thinking"] is enable_thinking


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("outside_error", [False, True])
def test_fresh_composed_source_ranges_remain_proven_with_outside_eof_result(monkeypatch, asynchronous, outside_error):
    monkeypatch.setenv("SP_CLAUSE_SYNTAX_GRAMMAR", "0")
    monkeypatch.setattr("deerflow.sp.source_prefetch.count_source_prompt_tokens", lambda *a, **k: 20)
    messages = [HumanMessage(content="Audit the supplied public source.")]
    for number, body in enumerate(SOURCE.splitlines(), 1):
        call_id = f"range-{number}"
        messages.extend(
            [
                AIMessage(content="", tool_calls=[{"name": "read_file", "id": call_id, "args": {"path": PATH, "start_line": number, "end_line": number}}]),
                ToolMessage(content=body, name="read_file", tool_call_id=call_id, additional_kwargs={"sp_host_source_prefetch": True, "sp_host_source_prefetch_visible": False}),
            ]
        )
    messages.extend(
        [
            AIMessage(content="", tool_calls=[{"name": "read_file", "id": "outside", "args": {"path": PATH, "start_line": 3, "end_line": 100}}]),
            ToolMessage(
                content="Error: outside-range test failure" if outside_error else "(empty: requested range starts after end of file; total_lines=2)", name="read_file", tool_call_id="outside", status="error" if outside_error else "success"
            ),
        ]
    )
    captured = [m.model_dump() for m in messages]
    request = ModelRequest(model=VllmChatModel(model="fixture", api_key="fixture"), messages=messages, tools=[{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}], model_settings={"max_tokens": 256})
    before = [m.model_dump() for m in messages]
    visibility = SourcePrefetchVisibilityMiddleware(captured, contract=contract(), settings=SourcePrefetchSettings(tokenizer="fixture", context_tokens=65536, response_tokens=256), auditor_role="reporter", skill_names=["clause"])
    if asynchronous:

        async def handler(current):
            return current

        result = asyncio.run(visibility.awrap_model_call(request, handler))
    else:
        result = visibility.wrap_model_call(request, lambda current: current)
    assert result.messages[-1].name == "sp_clause_current_source_visibility"
    assert "physical source lines 1..2" in result.messages[-1].content
    assert [m.model_dump() for m in result.messages[:-1]] == before
    assert result.tools == request.tools and result.model_settings == request.model_settings


@pytest.mark.parametrize("asynchronous", [False, True])
def test_source_ready_mode_is_independent_of_grammar_and_preserves_other_native_tools(monkeypatch, asynchronous):
    monkeypatch.setenv("SP_CLAUSE_SOURCE_READY_NO_REREAD", "1")
    state, payloads, _, _ = run_http(monkeypatch, [GOOD], asynchronous=asynchronous, enabled=False)
    assert state["messages"][-1].content == GOOD
    assert [item["function"]["name"] for item in payloads[0]["tools"]] == ["write_file"]
    assert any(message.get("name") == "sp_clause_source_ready_no_reread" for message in payloads[0]["messages"])
    assert not any(message.get("name") == "sp_clause_current_source_visibility" for message in payloads[0]["messages"])
    assert "response_format" not in payloads[0]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_source_ready_grammar_final_uses_same_fact_without_numeric_eof_note(monkeypatch, asynchronous):
    monkeypatch.setenv("SP_CLAUSE_SOURCE_READY_NO_REREAD", "1")
    state, payloads, _, _ = run_http(monkeypatch, [BAD, GOOD], asynchronous=asynchronous)
    assert state["messages"][-1].content == GOOD
    assert [index for index, request in enumerate(payloads) if "response_format" in request] == [1]
    final_notes = [message["content"] for message in payloads[1]["messages"] if message.get("name") == "sp_clause_syntax_final"]
    assert len(final_notes) == 1
    assert "[HOST CLAUSE SOURCE-READY MODE]" in final_notes[0]
    assert "beyond EOF" not in final_notes[0] and "physical source lines" not in final_notes[0]
    assert "partial" in final_notes[0] and "blocked" in final_notes[0]
    assert "complete original public source" in final_notes[0]
    assert not payloads[1].get("tools")


@pytest.mark.parametrize("final", ["[]", PARTIAL])
def test_source_ready_preserves_native_completed_empty_or_real_partial_output(monkeypatch, final):
    monkeypatch.setenv("SP_CLAUSE_SOURCE_READY_NO_REREAD", "1")
    state, payloads, _, _ = run_http(monkeypatch, [final], enabled=False)
    assert state["messages"][-1].content == final
    assert len(payloads) == 1
    assert [item["function"]["name"] for item in payloads[0]["tools"]] == ["write_file"]
