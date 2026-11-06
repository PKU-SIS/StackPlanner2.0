"""Host prefetch exposes actual public source tools within an exact token cap."""

import json

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from deerflow.sp.source_access import collect_source_access, source_access_gaps
from deerflow.sp.source_prefetch import SourcePrefetchSettings, mark_prefetch_model_visibility, prefetch_public_sources


def _counter(messages, tools):
    # Deliberately conservative fake tokenizer for deterministic budget tests.
    return sum(len(str(message.content)) + len(str(getattr(message, "tool_calls", []))) + 20 for message in messages) + len(str(tools))


def _settings(context=12000):
    return SourcePrefetchSettings(tokenizer="PRIVATE_TOKENIZER_PATH", context_tokens=context, response_tokens=1024)


def test_prefetch_is_opt_in_and_requires_explicit_private_budget_configuration():
    assert SourcePrefetchSettings.from_config({}) is None
    assert SourcePrefetchSettings.from_config({"sp_source_prefetch_enabled": "false"}) is None
    with pytest.raises(ValueError):
        SourcePrefetchSettings.from_config({"sp_source_prefetch_enabled": True})
    settings = SourcePrefetchSettings.from_config({"sp_source_prefetch_enabled": True, "sp_source_prefetch_tokenizer": "private", "sp_source_prefetch_context_tokens": 73728, "sp_source_prefetch_response_tokens": 16384})
    assert settings.context_tokens == 73728


def test_token_counter_includes_real_chat_template_tools_and_generation_prefix(monkeypatch):
    from deerflow.sp import source_prefetch

    captured = {}

    class Tokenizer:
        def apply_chat_template(self, messages, **kwargs):
            captured.update(messages=messages, **kwargs)
            return [1, 2, 3]

    @tool("read_file")
    def read_file(path: str) -> str:
        """Read the actual public source."""
        return "source"

    monkeypatch.setattr(source_prefetch, "_cached_tokenizer", lambda _: Tokenizer())
    assert source_prefetch.count_source_prompt_tokens([HumanMessage(content="Audit")], [read_file], _settings(), False) == 3
    assert captured["tools"][0]["function"]["name"] == "read_file"
    assert captured["add_generation_prompt"] is True
    assert captured["enable_thinking"] is False


@pytest.mark.asyncio
async def test_real_native_tool_results_establish_only_public_required_source_reads():
    calls = []

    @tool("read_file")
    def read_file(path: str, description: str, start_line: int, end_line: int) -> str:
        """Read exact native source lines."""
        calls.append((path, start_line, end_line))
        return "first original line\nsecond original line"

    @tool("ragflow_retrieve")
    def retrieve(question: str, document_id: str, page: int, page_size: int) -> str:
        """Read original scoped document chunks."""
        calls.append((document_id, page, page_size))
        return json.dumps(
            {
                "chunks": [{"content": "original indexed evidence"}],
                "source_coverage": {"mode": "document", "document_id": document_id, "page": page, "page_size": page_size, "total": 1, "returned": 1, "chunk_ids": ["original-chunk"], "document_bytes": 25},
            }
        )

    required = {"files": [{"path": "/mnt/user-data/uploads/rules.txt", "total_lines": 2}], "document_ids": ["scoped-document"]}
    state = {"messages": [HumanMessage(content="Audit original sources.")]}
    captured = []
    await prefetch_public_sources(
        state=state, tools=[read_file, retrieve], middlewares=[], config={}, context={}, settings=_settings(), contract={"source_requirements": required}, capture=lambda pair: captured.extend(pair), token_counter=_counter
    )
    recorded = [message.model_dump() for message in captured]
    assert source_access_gaps({"sp_task_contract": {"source_requirements": required}, "sp_source_access": collect_source_access(recorded)})
    mark_prefetch_model_visibility(state["messages"], recorded)
    access = collect_source_access(recorded)
    assert source_access_gaps({"sp_task_contract": {"source_requirements": required}, "sp_source_access": access}) == []
    assert calls == [("/mnt/user-data/uploads/rules.txt", 1, 2), ("scoped-document", 1, 100)]
    assert len(captured) == 4
    assert all(message.additional_kwargs.get("sp_host_source_prefetch") for message in captured)
    assert "PRIVATE_TOKENIZER_PATH" not in str(state)


def test_summarized_or_clipped_prefetch_is_not_proof_of_model_visible_original():
    from langchain_core.messages import AIMessage, ToolMessage

    call = AIMessage(content="Host source read", tool_calls=[{"name": "read_file", "args": {"path": "/mnt/user-data/uploads/rules.txt"}, "id": "call"}])
    result = ToolMessage(content="first\nsecond", name="read_file", tool_call_id="call", additional_kwargs={"sp_host_source_prefetch": True})
    saved = [call.model_dump(), result.model_dump()]
    mark_prefetch_model_visibility([HumanMessage(content="Summary of the source")], saved)
    assert collect_source_access(saved)["reads"] == {}
    mark_prefetch_model_visibility([call, result.model_copy(update={"content": "short preview"})], saved)
    assert collect_source_access(saved)["reads"] == {}
    mark_prefetch_model_visibility([call, result], saved)
    assert collect_source_access(saved)["reads"] == {"/mnt/user-data/uploads/rules.txt": [[1, 2]]}


@pytest.mark.asyncio
async def test_missing_allowed_tools_and_nonpublic_paths_are_never_broadened():
    state = {"messages": [HumanMessage(content="Audit sources.")]}
    required = {"files": [{"path": "/private/labels.json", "total_lines": 2}], "document_ids": ["doc"]}
    await prefetch_public_sources(state=state, tools=[], middlewares=[], config={}, context={}, settings=_settings(), contract={"source_requirements": required}, token_counter=_counter)
    assert len(state["messages"]) == 1
    assert collect_source_access([m.model_dump() for m in state["messages"]]) == {"reads": {}, "pages": []}


@pytest.mark.asyncio
async def test_oversized_and_truncated_reads_keep_context_bounded_without_full_proof(tmp_path):
    @tool("read_file")
    def read_file(path: str, description: str, start_line: int, end_line: int) -> str:
        """Read a large single source line."""
        return "SOURCE" * 4000

    required = {"files": [{"path": "/mnt/user-data/uploads/document.txt", "total_lines": 1}], "document_ids": []}
    state = {"messages": [HumanMessage(content="Audit the full source.")], "thread_data": {"outputs_path": str(tmp_path)}}
    await prefetch_public_sources(state=state, tools=[read_file], middlewares=[], config={}, context={}, settings=_settings(6500), contract={"source_requirements": required}, token_counter=_counter)
    assert _counter(state["messages"], [read_file]) + 1024 + 2048 <= 6500
    access = collect_source_access([message.model_dump() for message in state["messages"]])
    assert source_access_gaps({"sp_task_contract": {"source_requirements": required}, "sp_source_access": access})
    assert any(path.is_file() for path in tmp_path.rglob("*"))


@pytest.mark.asyncio
async def test_native_tool_interceptor_is_used_and_can_refuse_prefetch():
    from langchain.agents.middleware import AgentMiddleware
    from langchain_core.messages import ToolMessage

    calls = []

    class Refuse(AgentMiddleware):
        async def awrap_tool_call(self, request, handler):
            calls.append(request.tool_call["name"])
            return ToolMessage(content="Error: denied by native policy", status="error", name=request.tool_call["name"], tool_call_id=request.tool_call["id"])

    @tool("read_file")
    def read_file(path: str, description: str, start_line: int, end_line: int) -> str:
        """Read a source file."""
        raise AssertionError("Native refusal must prevent execution")

    required = {"files": [{"path": "/mnt/user-data/uploads/rules.txt", "total_lines": 2}], "document_ids": []}
    state = {"messages": [HumanMessage(content="Audit.")]}
    await prefetch_public_sources(state=state, tools=[read_file], middlewares=[Refuse()], config={}, context={}, settings=_settings(), contract={"source_requirements": required}, token_counter=_counter)
    assert calls == ["read_file"]
    assert source_access_gaps({"sp_task_contract": {"source_requirements": required}, "sp_source_access": collect_source_access([m.model_dump() for m in state["messages"]])})
