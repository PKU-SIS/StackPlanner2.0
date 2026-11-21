"""Guidance must keep native contracts, permissions and error recovery intact."""

import pytest
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from test_outline_evidence_action_middleware import RecordingModel, Request, packet

from deerflow.agents.middlewares.outline_evidence_action_middleware import (
    REGISTER_INSTRUCTION,
    SUBMIT_INSTRUCTION,
    OutlineEvidenceActionMiddleware,
)


def original_request():
    return Request(messages=[SystemMessage(content="ORIGINAL COMPLETE TREE BUDGET SAFETY CONTRACT"),
                             HumanMessage(content="ORIGINAL FULL TASK")],
                   tools=[{"name": n} for n in ("report_read_material", "report_register_research_request", "report_submit_outline")],
                   model_settings={"reasoning_effort": "low", "max_tokens": 32768})


def read_history(request):
    return [*request.messages, AIMessage(content="", tool_calls=[
        {"name": "report_read_material", "args": {"material_id": "m1"}, "id": "read"}]),
        ToolMessage(content='{"material_id":"m1","source_id":"S1","lines":[1,3]}', name="report_read_material", tool_call_id="read")]


def test_followup_guidance_keeps_the_exact_contract_history_tools_settings_and_guard_warning():
    request = original_request()
    middleware = OutlineEvidenceActionMiddleware(packet=packet(), followup_actions=True)
    history = read_history(request)
    warning = HumanMessage(content="NATIVE TOKEN BUDGET WARNING", additional_kwargs={"hide_from_ui": True})
    next_request = request.override(messages=[*history, warning])
    guided = middleware.prepare_request(next_request)
    assert guided.messages[:-1] == next_request.messages
    assert guided.messages[-1].content == REGISTER_INSTRUCTION
    assert guided.tools is next_request.tools
    assert guided.model_settings is next_request.model_settings
    assert next_request.messages == [*history, warning]
    registration = AIMessage(content="", tool_calls=[{"name": "report_register_research_request", "args": {"questions": []}, "id": "gap"}])
    result = ToolMessage(content='{"registered":[{"gap_id":"g1"}]}', name="report_register_research_request", tool_call_id="gap")
    submit = request.override(messages=[*history, warning, registration, result])
    guided = middleware.prepare_request(submit)
    assert guided.messages[:-1] == submit.messages
    assert guided.messages[-1].content == SUBMIT_INSTRUCTION
    assert guided.tools is request.tools
    assert guided.model_settings is request.model_settings


@pytest.mark.parametrize("bad", [
    ToolMessage(content='{"error":"hash_mismatch"}', status="error", name="report_read_material", tool_call_id="read"),
    ToolMessage(content='{"error":"hash_mismatch"}', name="report_read_material", tool_call_id="read"),
    ToolMessage(content='{"registered":[]}', name="report_register_research_request", tool_call_id="gap"),
    ToolMessage(content='{"error":"invalid_request"}', name="report_register_research_request", tool_call_id="gap"),
    ToolMessage(content='not json', name="report_register_research_request", tool_call_id="gap"),
    ToolMessage(content='{"saved":true,"validation_ok":false}', name="report_submit_outline", tool_call_id="submit"),
    AIMessage(content="", response_metadata={"finish_reason": "length"}),
    AIMessage(content="", invalid_tool_calls=[{"name": "bad", "args": "{", "id": "bad", "error": "invalid"}]),
])
def test_errors_restore_the_exact_native_recovery_request(bad):
    request = original_request()
    request = request.override(messages=[*read_history(request), bad])
    middleware = OutlineEvidenceActionMiddleware(packet=packet(), followup_actions=True)
    assert middleware.prepare_request(request) is request
    assert not middleware.followup_actions


def test_removed_tools_are_never_reintroduced():
    request = original_request()
    request = request.override(messages=read_history(request), tools=[{"name": "read_file"}])
    guided = OutlineEvidenceActionMiddleware(packet=packet(), followup_actions=True).prepare_request(request)
    assert guided.tools is request.tools


def test_consolidation_is_unchanged():
    request = original_request()
    request = request.override(messages=read_history(request))
    middleware = OutlineEvidenceActionMiddleware(packet=packet() | {"tree": {"content": '{"nodes":[]}'}}, followup_actions=True)
    assert middleware.prepare_request(request) is request


@pytest.mark.parametrize("register", [True, False])
def test_native_graph_keeps_direct_submission_and_registration_paths_without_new_decision_calls(register):
    executed = []

    @tool
    def report_read_material(material_id: str) -> str:
        """Read the original evidence."""
        executed.append("read")
        return '{"material_id":"m1","source_id":"S1","lines":[1,3]}'

    @tool
    def report_register_research_request(questions: list[dict]) -> str:
        """Register actual research needs."""
        executed.append("register")
        return '{"registered":[{"gap_id":"g1"}]}'

    @tool
    def report_submit_outline(tree: dict) -> str:
        """Submit a complete tree to the native validator."""
        executed.append("submit")
        return '{"saved":true,"validation_ok":true,"tree_path":"/mnt/user-data/outputs/tree.json"}'

    responses = [AIMessage(content="", tool_calls=[{"name": "report_read_material", "args": {"material_id": "m1"}, "id": "read"}])]
    if register:
        responses.append(AIMessage(content="", tool_calls=[{"name": "report_register_research_request", "args": {"questions": [{"question": "real gap"}]}, "id": "gap"}]))
    responses.extend([
        AIMessage(content="", tool_calls=[{"name": "report_submit_outline", "args": {"tree": {"requirements": ["both"]}}, "id": "submit"}]),
        AIMessage(content='{"artifact_type":"outline","artifact_metadata":{"delivery_mode":"file"}}'),
    ])
    model = RecordingModel(responses=responses)
    original = original_request().messages
    graph = create_agent(model=model, tools=[report_read_material, report_register_research_request, report_submit_outline],
                         middleware=[OutlineEvidenceActionMiddleware(packet=packet(), followup_actions=True)])
    result = graph.invoke({"messages": original})
    assert len(model.seen) == (4 if register else 3)
    assert model.seen[1][-1].content == REGISTER_INSTRUCTION
    for messages in model.seen[1:]:
        assert messages[:2] == original
    assert model.seen[-1][-1].type == 'tool'
    if register:
        assert model.seen[2][-1].content == SUBMIT_INSTRUCTION
    assert executed == (["read", "register", "submit"] if register else ["read", "submit"])
    assert result["messages"][:2] == original


def test_plain_or_capped_answers_do_not_get_new_retry_privileges():
    request = original_request()
    middleware = OutlineEvidenceActionMiddleware(packet=packet(), followup_actions=True)
    middleware.prepare_request(request.override(messages=read_history(request)))
    for response in (AIMessage(content='NO_NEW_RESEARCH_REQUESTS'),
                     AIMessage(content='NO_NEW_RESEARCH_REQUESTS', response_metadata={'finish_reason': 'length'})):
        assert middleware.after_model({'messages': [response]}, None) is None
