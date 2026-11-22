"""Central sees one CLAUSE transport without rewriting the authoritative task."""

import asyncio
import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from deerflow.models.vllm_provider import VllmChatModel
from deerflow.sp.agent_tools import SPFinishAvailabilityMiddleware, build_sp_control_tools
from deerflow.sp.memory import TaskMemoryStack

PREFIX = "PUBLIC WORKFLOW: preserve every supported finding and original quotation.\n\n"
LEGACY_MARKERS = (
    "The reporter must write /mnt/user-data/outputs/clause_answer-<action_id>.json",
    "immediately return the internal SP specialist envelope",
    "set artifact_content to null and artifact_metadata.created_paths",
    "The required deliverable is a UTF-8 .json file",
)


def bound_state(*, recovery=False):
    original = (Path(__file__).parent / "fixtures/clause_legacy_public_delivery.txt").read_text()
    user = HumanMessage(content=PREFIX + original, id="user-input", name="public-task", additional_kwargs={"public_fixture": "preserve"})
    stack = TaskMemoryStack()
    if recovery:
        stack.append_delegate("Audit source", run_id="run", metadata={"target_agent": "reporter"})
        stack.append_observe("An unfinished audit", actor="reporter", run_id="run", result_ref="prior-draft", metadata={"completion_status": "partial", "evidence_gaps": ["A genuine task requirement remains unresolved."]})
    return {
        "messages": [user],
        "sp_non_interactive": True,
        "sp_loop_run_id": "run",
        "sp_task_memory": stack.to_dict(),
        "sp_recovery_limits": {"reporter": 3, "delegate": 3, "capped_delegate": 2},
        "sp_task_contract": {
            "delivery_mode": "artifact",
            "output_parse_mode": "json",
            "output_schema": {"type": "array", "items": {"type": "object", "properties": {"text": {"type": "string"}, "explanation": {"type": "string"}}, "required": ["text", "explanation"], "additionalProperties": False}},
            "source_requirements": {"audit_agent": "reporter", "skill_names": ["clause"], "files": [{"path": "/mnt/user-data/uploads/document.txt", "total_lines": 1}], "document_ids": []},
            "original_issue": original,
            "original_request": original,
        },
    }


def filtered(state, messages=None):
    messages = state["messages"] if messages is None else messages
    request = ModelRequest(model=FakeMessagesListChatModel(responses=[AIMessage(content="unchanged")]), messages=messages, tools=build_sp_control_tools(), state=state)
    return SPFinishAvailabilityMiddleware().wrap_model_call(request, lambda current: current)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("recovery", [False, True], ids=["initial", "recovery"])
def test_real_central_http_uses_raw_final_transport_without_mutating_task_or_model_response(asynchronous, recovery):
    state = bound_state(recovery=recovery)
    original = state["sp_task_contract"]["original_issue"]
    # Literal source/history/hidden control content must never be treated as
    # the current harness-authored task instruction.
    messages = [
        SystemMessage(content="Central fixture system."),
        *state["messages"],
        AIMessage(content="", tool_calls=[{"id": "source-read", "name": "read_file", "args": {"path": "/mnt/user-data/uploads/document.txt"}}]),
        ToolMessage(content=original, name="read_file", tool_call_id="source-read"),
        HumanMessage(content=PREFIX + original, name="sp_task_memory_context", additional_kwargs={"hide_from_ui": True}),
    ]
    state["messages"] = messages
    before = deepcopy(state)
    payloads, seen = [], []
    response_text = "UNCHANGED CENTRAL RESPONSE BYTES"

    def endpoint(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "central-fixture",
                "object": "chat.completion",
                "created": 0,
                "model": "Qwen3-central-fixture",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": response_text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 5, "total_tokens": 35},
            },
        )

    transport = httpx.MockTransport(endpoint)
    middleware = SPFinishAvailabilityMiddleware()

    def request_for(model):
        return ModelRequest(model=model, messages=messages, tools=build_sp_control_tools(), state=state)

    if asynchronous:

        async def run():
            async with httpx.AsyncClient(transport=transport) as client:
                model = VllmChatModel(model="Qwen3-central-fixture", api_key="fixture-key", base_url="http://fixture.invalid/v1", max_retries=0, http_async_client=client)

                async def handler(current):
                    seen.append(current)
                    return await model.bind_tools(current.tools).ainvoke(current.messages)

                return await middleware.awrap_model_call(request_for(model), handler)

        result = asyncio.run(run())
    else:
        with httpx.Client(transport=transport) as client:
            model = VllmChatModel(model="Qwen3-central-fixture", api_key="fixture-key", base_url="http://fixture.invalid/v1", max_retries=0, http_client=client)

            def handler(current):
                seen.append(current)
                return model.bind_tools(current.tools).invoke(current.messages)

            result = middleware.wrap_model_call(request_for(model), handler)

    assert len(payloads) == 1
    actual = seen[0]
    wire = payloads[0]
    visible = wire["messages"][1]["content"]
    assert visible.startswith(PREFIX)
    assert "Review the complete contract. Preserve every supported issue." in visible
    assert "Read it using file tools; review the entire document" in visible
    assert "complete raw JSON array itself" in visible
    assert "optional draft" in visible and "partial/blocked" in visible
    assert all(marker not in visible for marker in LEGACY_MARKERS)
    assert [item["function"]["name"] for item in wire["tools"]] == ["sp_delegate"]
    note = actual.messages[-1]
    assert note.name == "sp_public_source_audit_required"
    assert f'stage="{"revision" if recovery else "reporting"}"' in note.content
    assert ('input_refs=["prior-draft"]' in note.content) is recovery
    # Prompt rendering must not change source content, memory, identity, status,
    # recovery allowances, tool binding, or the model's actual returned value.
    assert wire["messages"][3]["content"] == original
    assert wire["messages"][4]["content"] == PREFIX + original
    assert actual.messages[1].id == "user-input"
    assert actual.messages[1].name == "public-task"
    assert actual.messages[1].additional_kwargs == {"public_fixture": "preserve"}
    assert state == before
    assert result.content == response_text
    again = middleware._filter_request(actual)
    assert [message.model_dump() for message in again.messages] == [message.model_dump() for message in actual.messages]


@pytest.mark.parametrize(
    "damage",
    [
        "unbound",
        "different_skill",
        "invalid_skill",
        "different_schema",
        "different_source",
        "multiple_sources",
        "rag",
        "inline",
        "different_auditor",
        "custom_wording",
        "unbound_input",
        "block_content",
        "hidden_input",
        "different_latest_user",
    ],
)
def test_other_contracts_custom_or_unbound_inputs_preserve_original_message(damage):
    state = bound_state()
    contract = state["sp_task_contract"]
    if damage == "unbound":
        contract.clear()
    elif damage == "different_skill":
        contract["source_requirements"]["skill_names"] = ["nba"]
    elif damage == "invalid_skill":
        contract["source_requirements"]["skill_names"] = 1
    elif damage == "different_schema":
        contract["output_schema"]["items"]["additionalProperties"] = True
    elif damage == "different_source":
        contract["source_requirements"]["files"][0]["path"] = "/mnt/user-data/uploads/other.txt"
    elif damage == "multiple_sources":
        contract["source_requirements"]["files"].append({"path": "/mnt/user-data/uploads/other.txt", "total_lines": 1})
    elif damage == "rag":
        contract["source_requirements"]["document_ids"] = ["required-external-document"]
    elif damage == "inline":
        contract["delivery_mode"] = "inline"
    elif damage == "different_auditor":
        contract["source_requirements"]["audit_agent"] = "researcher"
    elif damage == "custom_wording":
        contract["original_issue"] = contract["original_issue"].replace("is required by native artifact ownership checks", "is requested by the caller")
        contract["original_request"] = contract["original_issue"]
        state["messages"][0].content = PREFIX + contract["original_issue"]
    elif damage == "unbound_input":
        state["messages"][0].content += "\nCaller-owned exact custom trailing instructions."
    elif damage == "block_content":
        state["messages"][0].content = [{"type": "text", "text": state["messages"][0].content}]
    elif damage == "hidden_input":
        state["messages"][0].additional_kwargs["hide_from_ui"] = True
    elif damage == "different_latest_user":
        state["messages"].append(HumanMessage(content="Caller-owned follow-up: preserve the prior quoted task exactly."))
    before = deepcopy(state)
    actual = filtered(state)
    assert actual.messages[: len(state["messages"])] == state["messages"]
    assert state == before


@pytest.mark.parametrize("anchor", ["original_issue", "original_request"])
def test_bound_original_anchor_without_guidance_and_request_message_fallback(anchor):
    state = bound_state()
    original = state["sp_task_contract"][anchor]
    state["sp_task_contract"] = {key: value for key, value in state["sp_task_contract"].items() if key not in {"original_issue", "original_request"} or key == anchor}
    messages = [HumanMessage(content=original, id="unprefixed-task")]
    del state["messages"]
    before = deepcopy(state)
    actual = filtered(state, messages)
    assert "complete raw JSON array itself" in actual.messages[0].content
    assert actual.messages[0].id == "unprefixed-task"
    assert messages[0].content == original
    assert state == before


def test_only_bound_task_suffix_changes_even_when_prefix_and_other_roles_contain_the_same_literal():
    state = bound_state()
    original = state["sp_task_contract"]["original_issue"]
    prefix = f"Caller-owned literal example:\n{original}\n\nCURRENT TASK:\n"
    state["messages"] = [SystemMessage(content=original), AIMessage(content=original), HumanMessage(content=prefix + original, id="current-input")]
    before = deepcopy(state)
    actual = filtered(state)
    assert actual.messages[:2] == state["messages"][:2]
    assert actual.messages[2].content.startswith(prefix)
    suffix = actual.messages[2].content[len(prefix) :]
    assert "complete raw JSON array itself" in suffix
    assert all(marker not in suffix for marker in LEGACY_MARKERS)
    assert state == before


def test_policy_checkpoint_early_return_still_uses_the_same_detached_delivery_view():
    state = bound_state()
    state["sp_last_handler_result"] = {"policy_checkpoint": "same_target_requires_intermediate_control_action"}
    before = deepcopy(state)
    actual = filtered(state)
    assert "complete raw JSON array itself" in actual.messages[0].content
    assert all(marker not in actual.messages[0].content for marker in LEGACY_MARKERS)
    assert "sp_delegate" not in {tool.name for tool in actual.tools}
    assert state == before


def test_public_skill_explanation_supports_each_family_without_requiring_a_conflicting_pair():
    skill = (Path(__file__).parents[2] / "configs/stackplanner_skills/clause/public/clause/SKILL.md").read_text()
    assert "explain the incompatible part elsewhere in the same document" not in skill
    assert "same conflict" not in skill
    assert "document-grounded defect and why it matters" in skill
    for family in ("ambiguity", "inconsistency", "terminology", "omission", "structural"):
        assert family in skill
    assert "Only inconsistency requires comparing incompatible statements" in skill
    assert "entire uploaded contract" in skill or "entire document" in skill
    assert "verbatim" in skill and "`text`" in skill and "`explanation`" in skill
