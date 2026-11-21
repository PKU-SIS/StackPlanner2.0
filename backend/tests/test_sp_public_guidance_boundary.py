"""Exercise initial guidance through native HTTP, task memory and Reporter context."""

import asyncio
import json

import httpx
import pytest
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from deerflow.agents.thread_state import ThreadState
from deerflow.models.vllm_provider import VllmChatModel
from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.handlers.context import MAX_HANDLER_HISTORICAL_INPUT_CHARS, build_handler_context_refs
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.middlewares import TaskMemoryMiddleware
from deerflow.sp.subagents import SPSubagentTask
from deerflow.sp.subagents.dr2_adapter import render_sp_subagent_prompt

GUIDANCE = "PUBLIC-GUIDANCE-SENTINEL: keep exact source quotations."
TASK = "ORIGINAL-TASK-SENTINEL: audit this complete public contract."


def _reporter_payload(state):
    refs = build_handler_context_refs(HandlerContext(state=state, stack=TaskMemoryStack.from_dict(state["sp_task_memory"]), thread_id="fixture-thread", run_id="fixture-run"))
    task = SPSubagentTask(action_id="fixture-report", subagent_type="reporter", task="Deliver the complete audit.", description="Fixture report", context_refs=refs)
    prompt = render_sp_subagent_prompt(task)
    body = prompt.split("<sp-subagent-task>\n", 1)[1].split("\n</sp-subagent-task>", 1)[0]
    return json.loads(body)["context_refs"]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("fresh_user_turn", [None, False, True], ids=["flag-omitted", "flag-false", "flag-true"])
@pytest.mark.parametrize("inline_latest_user", [False, True], ids=["separate-prelude", "latest-user-inline"])
def test_initial_guidance_delivery_matches_native_http_memory_and_reporter(asynchronous, fresh_user_turn, inline_latest_user):
    latest = f"{GUIDANCE}\n\n{TASK}" if inline_latest_user else TASK
    messages = [HumanMessage(content=latest, id="fixture-task")]
    if not inline_latest_user:
        messages.insert(0, HumanMessage(content=GUIDANCE, id="fixture-prelude"))
    context = {"thread_id": "fixture-thread", "run_id": "fixture-run"}
    if fresh_user_turn is not None:
        context["fresh_user_turn_after_terminal"] = fresh_user_turn
    payloads = []

    def endpoint(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "fixture-completion",
                "object": "chat.completion",
                "created": 0,
                "model": "Qwen3-guidance-fixture",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "Fixture completed."}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24},
            },
        )

    transport = httpx.MockTransport(endpoint)

    def graph(**clients):
        model = VllmChatModel(model="Qwen3-guidance-fixture", api_key="fixture-key", base_url="http://fixture.invalid/v1", max_retries=0, **clients)
        return create_agent(model=model, tools=[], middleware=[TaskMemoryMiddleware()], state_schema=ThreadState, context_schema=dict)

    if asynchronous:

        async def run():
            async with httpx.AsyncClient(transport=transport) as client:
                return await graph(http_async_client=client).ainvoke({"messages": messages}, context=context)

        result = asyncio.run(run())
    else:
        with httpx.Client(transport=transport) as client:
            result = graph(http_client=client).invoke({"messages": messages}, context=context)

    assert len(payloads) == 1
    http_messages = payloads[0]["messages"]
    assert any(TASK in message.get("content", "") for message in http_messages)
    assert any(GUIDANCE in message.get("content", "") for message in http_messages) is (inline_latest_user or fresh_user_turn is not True)
    requests = [entry.content for entry in TaskMemoryStack.from_dict(result["sp_task_memory"]).entries if entry.action == "user_request"]
    assert requests == [latest]
    refs = _reporter_payload(result)
    assert refs["original_query"] == latest
    assert refs["latest_user_input"] == latest
    if inline_latest_user:
        assert "thread_first_user_input" not in refs
    else:
        # A separate short prelude still reaches Reporter as bounded history,
        # even though it is absent from the authoritative current task fields.
        assert refs["thread_first_user_input"] == GUIDANCE


@pytest.mark.parametrize("inline_latest_user", [False, True], ids=["bounded-prelude", "complete-latest-user"])
def test_reporter_keeps_full_inline_guidance_while_historical_prelude_is_bounded(inline_latest_user):
    guidance = "Public workflow guidance. " * (MAX_HANDLER_HISTORICAL_INPUT_CHARS // 20 + 2) + "GUIDANCE-TAIL-SENTINEL"
    latest = f"{guidance}\n\n{TASK}" if inline_latest_user else TASK
    messages = [HumanMessage(content=latest)]
    if not inline_latest_user:
        messages.insert(0, HumanMessage(content=guidance))
    state = {"messages": messages, "sp_task_memory": TaskMemoryStack().to_dict()}
    refs = _reporter_payload(state)
    assert refs["original_query"] == latest
    assert refs["latest_user_input"] == latest
    if inline_latest_user:
        assert "GUIDANCE-TAIL-SENTINEL" in refs["original_query"]
    else:
        assert len(refs["thread_first_user_input"]) == MAX_HANDLER_HISTORICAL_INPUT_CHARS
        assert "GUIDANCE-TAIL-SENTINEL" not in refs["thread_first_user_input"]
