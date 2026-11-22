"""One existing format turn can use grammar without replacing model bytes.

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
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from deerflow.agents.thread_state import ThreadState
from deerflow.models.vllm_provider import VllmChatModel
from deerflow.sp.public_format_repair import PublicArtifactFormatRepairMiddleware
from deerflow.sp.source_prefetch import SourcePrefetchSettings, SourcePrefetchVisibilityMiddleware
from deerflow.sp.syntax_grammar import collect_syntax_grammar_diagnostics, syntax_repair_schema

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
        check = PublicArtifactFormatRepairMiddleware(public, action_id="audit", auditor_role=role, skill_names=skills, captured_sources=lambda: captured)
        visibility = SourcePrefetchVisibilityMiddleware(captured, contract=public, settings=SourcePrefetchSettings(tokenizer="fixture", context_tokens=65536, response_tokens=256), auditor_role=role, skill_names=skills, syntax_scope="audit")
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
def test_real_http_uses_one_existing_turn_then_restores_semantic_quote_and_tools(monkeypatch, asynchronous):
    result, payloads, headers, captured = run_http(monkeypatch, [BAD, GOOD, WRAPPED, GOOD], asynchronous=asynchronous, reviews=True)
    assert len(payloads) == 4
    constrained = [i for i, body in enumerate(payloads) if "response_format" in body]
    assert constrained == [1]
    grammar = payloads[1]["response_format"]
    assert grammar["type"] == "json_schema"
    assert grammar["json_schema"]["schema"] == syntax_repair_schema(contract())
    assert not payloads[1].get("tools") and "tool_choice" not in payloads[1]
    assert all({t["function"]["name"] for t in payloads[i]["tools"]} == {"read_file", "write_file"} for i in [0, 2, 3])
    for body in payloads:
        assert body["temperature"] == 0.7 and body["top_p"] == 0.9 and body["max_completion_tokens"] == 256
        assert body["top_k"] == 23 and body["min_p"] == 0.1 and body["chat_template_kwargs"] == {"enable_thinking": False}
        assert any(m.get("content") == SOURCE for m in body["messages"])
    assert "sp_clause_semantic_review_feedback" == payloads[2]["messages"][-1]["name"]
    assert "sp_clause_quote_review_feedback" == payloads[3]["messages"][-1]["name"]
    assert all("x-stainless-helper-method" not in h for h in headers)
    assert result["messages"][-1].content == GOOD
    diagnostics = collect_syntax_grammar_diagnostics(captured)
    assert [d["status"] for d in diagnostics] == ["applied", "response_received"]
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


def test_invalid_grammar_response_cannot_reopen_once_per_action_or_add_format_turns(monkeypatch):
    result, payloads, _, captured = run_http(monkeypatch, [BAD, BAD, BAD])
    assert len(payloads) == 3
    assert [i for i, body in enumerate(payloads) if "response_format" in body] == [1]
    assert result["messages"][-1].content == BAD
    assert [d["status"] for d in collect_syntax_grammar_diagnostics(captured)].count("applied") == 1
    assert [d["status"] for d in collect_syntax_grammar_diagnostics(captured)].count("response_received") == 1


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
def test_reusable_service_probe_keeps_native_graph_and_original_output(monkeypatch, mode, final, expected_status):
    import importlib.util
    from pathlib import Path

    probe_file = Path(__file__).resolve().parents[1] / "scripts/smoke_clause_syntax_grammar.py"
    spec = importlib.util.spec_from_file_location("syntax_service_probe", probe_file)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    monkeypatch.setenv("SP_CLAUSE_SYNTAX_GRAMMAR", "1")
    monkeypatch.setattr("deerflow.sp.source_prefetch.count_source_prompt_tokens", lambda *a, **k: 20)
    payloads = []

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
            return await probe.run_probe(base_url="http://fixture.invalid/v1", model_name="fixture", tokenizer="fixture", max_tokens=256, mode=mode, http_async_client=client)

    result = asyncio.run(run())
    assert result["raw_final"] == final and result["completion_status"] == expected_status
    assert "response_format" in payloads[0]
    assert all("response_format" not in body for body in payloads[1:])
    assert [d["status"] for d in result["syntax_grammar"]] == ["applied", "response_received"]
