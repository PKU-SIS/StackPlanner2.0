"""The first evidence action stays in the native loop and preserves later contracts."""
from types import SimpleNamespace

import pytest
from _agent_e2e_helpers import FakeToolCallingModel
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import Field

from deerflow.agents.middlewares.outline_evidence_action_middleware import (
    NO_READS,
    OutlineEvidenceActionMiddleware,
    evidence_packet,
)


class Request(SimpleNamespace):
    def override(self, **kwargs):
        return Request(**{**vars(self), **kwargs})


def packet():
    return {"request":{"text":"Cover both efficiency and maintenance; retain all user requirements."},
            "inputs":[{"input_id":"u2","text":"Also compare developer and manager views."}],
            "materials":[{"material_id":"m1","source_id":"S1","title":"Original source"}],
            "summaries":[{"content":"The original synthesis and locators."}],
            "length":{"body_target_words":3500}}


def test_request_is_scoped_without_mutating_original_contract_or_limits():
    messages=[SystemMessage(content="COMPLETE TREE EVIDENCE AND BUDGET CONTRACT"),HumanMessage(content="ORIGINAL TASK")]
    request=Request(messages=messages,tools=[{"name":"report_read_material"},{"name":"report_submit_outline"}],
                    model_settings={"max_tokens":32768,"reasoning_effort":"low"})
    middleware=OutlineEvidenceActionMiddleware(packet=packet())
    scoped=middleware.prepare_request(request)
    assert scoped.tools==[{"name":"report_read_material"}]
    assert scoped.model_settings is request.model_settings
    assert "maintenance" in scoped.messages[1].content and "manager views" in scoped.messages[1].content
    assert "body_target_words" not in scoped.messages[1].content
    assert request.messages is messages and messages[0].content=="COMPLETE TREE EVIDENCE AND BUDGET CONTRACT"
    response=AIMessage(content="",tool_calls=[{"name":"report_read_material","args":{"material_id":"m1"},"id":"read"}])
    assert middleware.after_model({"messages":[*messages,response]},None) is None
    following=request.override(messages=[*messages,response,ToolMessage(content="source",tool_call_id="read")])
    assert middleware.prepare_request(following) is following


@pytest.mark.parametrize("bad_tree",["bad json", "[]"])
def test_unexpected_tree_falls_back_to_complete_request(bad_tree):
    p=packet()
    p['tree']={'content':bad_tree}
    assert evidence_packet(p)=={}
    request=Request(messages=[HumanMessage(content="complete")],tools=[{"name":"read_file"}])
    assert OutlineEvidenceActionMiddleware(packet=p).prepare_request(request) is request


def test_existing_read_history_is_not_repeated():
    request=Request(messages=[AIMessage(content="already selected")],tools=[{"name":"read_file"}])
    assert OutlineEvidenceActionMiddleware(packet=packet()).prepare_request(request) is request


@pytest.mark.parametrize("finish",["length","max_tokens"])
def test_capped_response_does_not_grant_an_extra_model_turn(finish):
    middleware=OutlineEvidenceActionMiddleware(packet=packet())
    middleware.prepare_request(Request(messages=[HumanMessage(content="task")],tools=[{"name":"read_file"}]))
    assert middleware.after_model({'messages':[AIMessage(content=NO_READS,response_metadata={'finish_reason':finish})]},None) is None


class RecordingModel(FakeToolCallingModel):
    seen: list=Field(default_factory=list)

    def _generate(self,messages,**kwargs):
        self.seen.append(messages)
        return super()._generate(messages,**kwargs)


@pytest.mark.parametrize("needs_read",[True,False])
def test_real_agent_graph_restores_full_contract_and_executes_only_requested_reads(needs_read):
    executed=[]

    @tool
    def report_read_material(material_id: str) -> str:
        """Read original saved evidence."""
        executed.append(material_id)
        return "S1 original lines 1-3"

    @tool
    def report_submit_outline(tree: dict) -> str:
        """Submit the complete tree under the native schema."""
        executed.append('submit')
        return '{"saved":true,"validation_ok":true}'

    first=AIMessage(content="",tool_calls=[{"name":"report_read_material","args":{"material_id":"m1"},"id":"read"}]) if needs_read else AIMessage(content=NO_READS)
    model=RecordingModel(responses=[first,AIMessage(content="",tool_calls=[{"name":"report_submit_outline","args":{"tree":{"requirements":["both"]}},"id":"submit"}]),AIMessage(content='{"artifact_content":null,"artifact_type":"outline"}')])
    original=[SystemMessage(content="COMPLETE TREE EVIDENCE AND BUDGET CONTRACT"),HumanMessage(content="ORIGINAL FULL TASK")]
    graph=create_agent(model=model,tools=[report_read_material,report_submit_outline],
                       middleware=[OutlineEvidenceActionMiddleware(packet=packet())])
    result=graph.invoke({'messages':original})
    assert len(model.seen)==3
    assert 'selecting the next necessary saved-evidence action' in model.seen[0][0].content
    for messages in model.seen[1:]:
        assert messages[0].content==original[0].content and messages[1].content==original[1].content
    assert executed==(['m1','submit'] if needs_read else ['submit'])
    assert result['messages'][0].content==original[0].content
    assert result['messages'][-1].content=='{"artifact_content":null,"artifact_type":"outline"}'
