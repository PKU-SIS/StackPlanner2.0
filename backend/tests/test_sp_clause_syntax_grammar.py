"""Existing final turns can use grammar without replacing model bytes.

Mock HTTP verifies the actual native serializer, not deployed grammar support.
"""

import asyncio
import json
from copy import deepcopy

import httpx
import pytest
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
    return {
        "delivery_mode": "artifact",
        "output_parse_mode": "json",
        "output_schema": {"type": "array", "items": {"type": "object", "properties": {"text": {"type": "string"}, "explanation": {"type": "string"}}, "required": ["text", "explanation"], "additionalProperties": False}},
        "source_requirements": {"audit_agent": "reporter", "files": [{"path": PATH, "total_lines": 2}], "document_ids": []},
    }


def reads():
    return [AIMessage(content="", tool_calls=[{"name": "read_file", "id": "original-read", "args": {"path": PATH}}]), ToolMessage(content=SOURCE, name="read_file", tool_call_id="original-read")]


def run_http(monkeypatch, responses, *, asynchronous=False, backend="sglang", damage=None, reviews=False, enabled=True):
    monkeypatch.setenv("SP_CLAUSE_SYNTAX_GRAMMAR", "1" if enabled else "0")
    monkeypatch.setenv("SP_CLAUSE_SEMANTIC_REVIEW", "1" if reviews else "0")
    monkeypatch.setenv("SP_CLAUSE_QUOTE_REVIEW", "1" if reviews else "0")
    monkeypatch.setattr("deerflow.sp.source_prefetch.count_source_prompt_tokens", lambda *a, **k: 20)
    source = reads()
    captured = [m.model_dump() for m in source]
    public = contract()
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
        return SOURCE

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
            captured, contract=public, settings=SourcePrefetchSettings(tokenizer="fixture", context_tokens=65536, response_tokens=256),
            auditor_role=role, skill_names=skills, syntax_scope="audit", syntax_gate=gate,
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


def test_union_keeps_original_success_schema_and_requires_real_failure_gaps():
    public = contract()
    original = deepcopy(public)
    schema = syntax_repair_schema(public)
    assert schema["anyOf"][0] == public["output_schema"] and public == original
    validator = Draft202012Validator(schema)
    for value in [json.loads(GOOD), [], json.loads(PARTIAL)]:
        validator.validate(value)
    draft = json.loads(PARTIAL)
    draft["artifact_content"] = "Verified partial work remains available."
    validator.validate(draft)
    for value in [{"artifact_metadata": {"completion_status": "complete", "evidence_gaps": []}}, {"artifact_metadata": {"completion_status": "partial", "evidence_gaps": []}}]:
        assert list(validator.iter_errors(value))


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
