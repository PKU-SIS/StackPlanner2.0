"""Closing CLAUSE re-reads requires fresh authenticated complete model input."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from dcc_harness.data import InputFile, PublicTask
from dcc_harness.preprocessing.output_contracts import public_output_contract
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from deerflow.sp.source_prefetch import SourcePrefetchSettings, SourcePrefetchVisibilityMiddleware

PATH = "/mnt/user-data/uploads/document.txt"
SOURCE = 'Original "Information");tion");\nThe full continuation remains visible.'


class Request:
    def __init__(self, messages, tools=None, system_message=None):
        self.messages = messages
        self.tools = tools if tools is not None else [SimpleNamespace(name=name) for name in ("read_file", "grep", "write_file", "other_tool")]
        self.system_message = system_message

    def override(self, **changes):
        return Request(changes.get("messages", self.messages), changes.get("tools", self.tools), self.system_message)


def inputs():
    contract = public_output_contract(PublicTask(benchmark="clause", prompt="Audit all supported issues.", files=(InputFile(name="document.txt", text=SOURCE),)))
    messages = [
        HumanMessage(content="Audit original public evidence and preserve every supported distinct finding."),
        AIMessage(content="", tool_calls=[{"id": "original", "name": "read_file", "args": {"path": PATH, "start_line": 1, "end_line": 2}}]),
        ToolMessage(content=SOURCE, name="read_file", tool_call_id="original"),
    ]
    captured = [message.model_dump() for message in messages[1:]]
    settings = SourcePrefetchSettings(tokenizer="PRIVATE_TOKENIZER", context_tokens=8192, response_tokens=1024)
    return contract, messages, captured, settings


def invoke(middleware, request, asynchronous):
    if asynchronous:
        async def handler(current):
            return current
        return asyncio.run(middleware.awrap_model_call(request, handler))
    return middleware.wrap_model_call(request, lambda current: current)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("host_prefetched", [False, True])
def test_source_ready_closes_only_repeated_reads_and_counts_complete_proposed_input(monkeypatch, asynchronous, host_prefetched):
    monkeypatch.setenv("SP_CLAUSE_SOURCE_READY_NO_REREAD", "1")
    contract, messages, captured, settings = inputs()
    if host_prefetched:
        for message in messages[1:]:
            message.additional_kwargs.update(sp_host_source_prefetch=True)
        captured = [message.model_dump() for message in messages[1:]]
    original_contract = deepcopy(contract)
    checked = []
    def count(messages, tools, settings, thinking_enabled=None):
        checked.append((messages, tools, thinking_enabled))
        return 100
    monkeypatch.setattr("deerflow.sp.source_prefetch.count_source_prompt_tokens", count)
    request = Request(messages, system_message=SystemMessage(content="Original system policy and Skill remain authoritative."))
    before = [message.model_dump() for message in request.messages]
    middleware = SourcePrefetchVisibilityMiddleware(captured, contract=contract, settings=settings, auditor_role="reporter", skill_names=["clause"], thinking_enabled=True)
    filtered = invoke(middleware, request, asynchronous)
    assert [tool.name for tool in filtered.tools] == ["write_file", "other_tool"]
    assert [tool.name for tool in request.tools] == ["read_file", "grep", "write_file", "other_tool"]
    assert filtered.messages[:-1] == messages
    assert [message.model_dump() for message in request.messages] == before
    note = filtered.messages[-1]
    assert note.name == "sp_clause_source_ready_no_reread"
    assert note.additional_kwargs["hide_from_ui"] is True
    assert "partial/blocked" in note.content and "does not establish task completion" in note.content
    assert "every supported distinct finding" in note.content
    assert "EOF" not in note.content and not any(character.isdigit() for character in note.content)
    assert "PRIVATE_TOKENIZER" not in note.content
    assert checked == [([request.system_message, *filtered.messages], filtered.tools, True)]
    assert contract == original_contract
    # Proof is per request, so subsequent compaction restores native read tools.
    summarized = Request([HumanMessage(content="A summary claims the entire file was read.")])
    assert invoke(middleware, summarized, asynchronous) is summarized


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("damage", [
    "missing", "preview", "altered_args", "forged_pair", "no_capture", "error", "incomplete", "truncated", "externalized", "conflict",
    "pending", "invalid_call", "orphan_result", "wrong_role", "wrong_skill", "invalid_skill_selection", "different_source", "rag", "inline", "schema",
    "missing_settings", "over_budget", "counter_error",
])
def test_source_ready_requires_exact_current_originals_scope_no_pending_and_verified_budget(monkeypatch, asynchronous, damage):
    monkeypatch.setenv("SP_CLAUSE_SOURCE_READY_NO_REREAD", "1")
    contract, messages, captured, settings = inputs()
    role, skills = "reporter", ["clause"]
    if damage == "missing":
        messages = messages[:1]
    elif damage == "preview":
        messages[-1].content = "A summary of the original source."
    elif damage == "altered_args":
        messages[-2].tool_calls[0]["args"]["end_line"] = 1
    elif damage == "forged_pair":
        messages[-2].tool_calls[0]["id"] = "forged"
        messages[-1].tool_call_id = "forged"
    elif damage == "no_capture":
        captured = []
    elif damage == "error":
        messages[-1].status = "error"
        captured[-1]["status"] = "error"
    elif damage == "incomplete":
        messages[-2].tool_calls[0]["args"]["end_line"] = 1
        messages[-1].content = SOURCE.splitlines()[0]
        captured = [message.model_dump() for message in messages[1:]]
    elif damage in {"truncated", "externalized"}:
        messages[-1].content += "\n[truncated: more source remains]" if damage == "truncated" else "\n[Full read_file output saved to /mnt/user-data/outputs/source.txt]"
        captured[-1]["content"] = messages[-1].content
    elif damage == "conflict":
        messages.extend([
            AIMessage(content="", tool_calls=[{"id": "conflict", "name": "read_file", "args": {"path": PATH, "start_line": 1, "end_line": 2}}]),
            ToolMessage(content=SOURCE.replace("continuation", "changed text"), name="read_file", tool_call_id="conflict"),
        ])
        captured = [message.model_dump() for message in messages[1:]]
    elif damage == "pending":
        messages.append(AIMessage(content="", tool_calls=[{"id": "pending", "name": "other_tool", "args": {}}]))
    elif damage == "invalid_call":
        messages.append(AIMessage(content="", invalid_tool_calls=[{"name": "read_file", "args": "{", "id": "invalid", "error": "invalid json"}]))
    elif damage == "orphan_result":
        messages.append(ToolMessage(content="unpaired", name="other_tool", tool_call_id="orphan"))
    elif damage == "wrong_role":
        role = "researcher"
    elif damage == "wrong_skill":
        skills = ["nba"]
    elif damage == "invalid_skill_selection":
        skills = 1
    elif damage == "different_source":
        contract["source_requirements"]["files"][0]["path"] = "/mnt/user-data/uploads/other.txt"
    elif damage == "rag":
        contract["source_requirements"]["document_ids"] = ["required-rag-document"]
    elif damage == "inline":
        contract["delivery_mode"] = "inline"
    elif damage == "schema":
        contract["output_schema"]["items"]["additionalProperties"] = True
    elif damage == "missing_settings":
        settings = None
    def count(*args):
        if damage == "counter_error":
            raise RuntimeError("Tokenizer unavailable")
        return 100_000 if damage == "over_budget" else 100
    monkeypatch.setattr("deerflow.sp.source_prefetch.count_source_prompt_tokens", count)
    request = Request(messages)
    before = [message.model_dump() for message in messages]
    middleware = SourcePrefetchVisibilityMiddleware(captured, contract=contract, settings=settings, auditor_role=role, skill_names=skills)
    filtered = invoke(middleware, request, asynchronous)
    assert [tool.name for tool in filtered.tools] == ["read_file", "grep", "write_file", "other_tool"]
    assert not any(message.name == "sp_clause_source_ready_no_reread" for message in filtered.messages)
    assert [message.model_dump() for message in request.messages] == before


@pytest.mark.parametrize("value", [None, "0", "true", "yes", "01", "1 "])
def test_source_ready_is_strictly_opt_in_and_default_keeps_existing_transport_note(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("SP_CLAUSE_SOURCE_READY_NO_REREAD", raising=False)
    else:
        monkeypatch.setenv("SP_CLAUSE_SOURCE_READY_NO_REREAD", value)
    contract, messages, captured, settings = inputs()
    monkeypatch.setattr("deerflow.sp.source_prefetch.count_source_prompt_tokens", lambda *args: 100)
    middleware = SourcePrefetchVisibilityMiddleware(captured, contract=contract, settings=settings, auditor_role="reporter", skill_names=["clause"])
    result = invoke(middleware, Request(messages), False)
    assert [tool.name for tool in result.tools] == ["read_file", "grep", "write_file", "other_tool"]
    assert result.messages[-1].name == "sp_clause_current_source_visibility"


@pytest.mark.parametrize("excess", [0, 1])
def test_source_ready_honors_reserved_response_and_overhead_at_exact_context_boundary(monkeypatch, excess):
    monkeypatch.setenv("SP_CLAUSE_SOURCE_READY_NO_REREAD", "1")
    contract, messages, captured, settings = inputs()
    monkeypatch.setattr("deerflow.sp.source_prefetch.count_source_prompt_tokens", lambda *args: settings.context_tokens - settings.response_tokens - 2048 + excess)
    middleware = SourcePrefetchVisibilityMiddleware(captured, contract=contract, settings=settings, auditor_role="reporter", skill_names=["clause"])
    result = invoke(middleware, Request(messages), False)
    assert any(tool.name == "read_file" for tool in result.tools) is bool(excess)
