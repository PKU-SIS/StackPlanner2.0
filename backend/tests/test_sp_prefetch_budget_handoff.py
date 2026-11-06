"""Optional source prefetch must hand budget pressure back to the native model."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from deerflow.sp.source_access import collect_source_access, source_access_gaps
from deerflow.sp.source_prefetch import _HOST_NOTE, SourcePrefetchSettings, mark_prefetch_model_visibility, prefetch_public_sources


def _counter(messages, tools=()):
    return sum(len(str(message.content)) for message in messages)


def _requirements():
    return {"files": [{"path": "/mnt/user-data/uploads/document.txt", "total_lines": 1}], "document_ids": []}


@pytest.mark.asyncio
async def test_real_native_summary_threshold_reserves_prefetch_result_room_and_still_calls_auditor(tmp_path):
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from pydantic import PrivateAttr

    from deerflow.agents.middlewares.summarization_middleware import DeerFlowSummarizationMiddleware

    calls = []

    @tool("read_file")
    def read_file(path: str, description: str, start_line: int, end_line: int) -> str:
        """Read an original source through the actual native tool."""
        calls.append(path)
        return "Original source content " * 1000

    class Model(FakeMessagesListChatModel):
        _seen: list = PrivateAttr(default_factory=list)

        def bind_tools(self, tools, **kwargs):
            assert [tool.name for tool in tools] == ["read_file"]
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self._seen.append(messages)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    original = HumanMessage(content="Continue the original audit with the real source and retained draft.")
    # The short pre-call placeholder fits the real native summary threshold;
    # the longer real reference does not. Exact tokenizer capacity is ample.
    trigger = len(original.content + _HOST_NOTE) + 1024 + 160
    summary = DeerFlowSummarizationMiddleware(model=Model(responses=[AIMessage(content="Native summary")]), trigger=("tokens", trigger), keep=("messages", 2), token_counter=_counter)
    state = {"messages": [original], "thread_data": {"outputs_path": str(tmp_path)}}
    captured = []
    settings = SourcePrefetchSettings(tokenizer="PRIVATE_TOKENIZER", context_tokens=15000, response_tokens=1024)
    await prefetch_public_sources(
        state=state,
        tools=[read_file],
        middlewares=[summary],
        config={},
        context={},
        settings=settings,
        contract={"source_requirements": _requirements()},
        capture=lambda pair: captured.extend(pair),
        token_counter=_counter,
    )
    assert calls == []
    assert state["messages"] == [original]
    assert captured == []
    assert source_access_gaps({"sp_task_contract": {"source_requirements": _requirements()}, "sp_source_access": collect_source_access(captured)})
    model = Model(responses=[AIMessage(content="The original audit is partial until the remaining source is inspected.")])
    graph = create_agent(model=model, tools=[read_file], middleware=[summary])
    result = await graph.ainvoke(state)
    assert len(model._seen) == 1
    assert model._seen[0][0].content == original.content
    assert result["messages"][-1].content == model.responses[0].content


@pytest.mark.asyncio
async def test_budget_change_during_real_tool_call_never_aborts_or_removes_prior_model_draft(tmp_path):
    from langgraph.prebuilt.tool_node import ToolRuntime

    original = [HumanMessage(content="Continue the audit."), AIMessage(content="Actual prior partial model draft, preserved byte for byte."), HumanMessage(content="Inspect remaining original sources.")]
    changed_context = HumanMessage(content="Actual native tool context update " * 1000)
    calls = []

    @tool("read_file")
    def read_file(runtime: ToolRuntime, path: str, description: str, start_line: int, end_line: int) -> str:
        """Read an original source while the native tool updates execution context."""
        calls.append(path)
        runtime.state["messages"].append(changed_context)
        return "Actual original source result " * 1000

    state = {"messages": list(original), "thread_data": {"outputs_path": str(tmp_path)}}
    captured = []
    settings = SourcePrefetchSettings(tokenizer="PRIVATE_TOKENIZER", context_tokens=8000, response_tokens=1024)
    await prefetch_public_sources(
        state=state,
        tools=[read_file],
        middlewares=[],
        config={},
        context={},
        settings=settings,
        contract={"source_requirements": _requirements()},
        capture=lambda pair: captured.extend(pair),
        token_counter=_counter,
    )
    assert len(calls) == 1
    assert state["messages"][0] is original[0]
    assert state["messages"][1] is original[1]
    assert state["messages"][1].content == "Actual prior partial model draft, preserved byte for byte."
    assert state["messages"][-1] is changed_context
    assert not any(isinstance(message, AIMessage) and message.additional_kwargs.get("sp_host_source_prefetch") for message in state["messages"])
    assert len(captured) == 2
    assert isinstance(captured[0], AIMessage) and isinstance(captured[1], ToolMessage)
    assert captured[1].content.startswith("[truncated:")
    recorded = [message.model_dump() for message in captured]
    mark_prefetch_model_visibility(state["messages"], recorded)
    assert collect_source_access(recorded)["reads"] == {}
    assert any(path.is_file() for path in tmp_path.rglob("*"))
