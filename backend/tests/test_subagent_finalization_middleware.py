"""Optional stage finalization preserves normal final output instead of tool dumps."""

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from deerflow.agents.middlewares.subagent_finalization_middleware import SubagentFinalizationMiddleware


class Request(SimpleNamespace):
    def override(self, **kwargs):
        return Request(**{**vars(self), **kwargs})


def test_tools_are_removed_only_at_the_explicit_finalization_boundary():
    middleware = SubagentFinalizationMiddleware(after_tool_calls=2)
    request = Request(messages=[SystemMessage(content="Role and Skill"), HumanMessage(content="Research"), ToolMessage(content="S1 evidence", tool_call_id="one", name="web_search")], tools=["search"])
    assert middleware._prepare(request) is request
    request.messages.append(ToolMessage(content="S2 evidence", tool_call_id="two", name="read_file"))
    prepared = middleware._prepare(request)
    assert prepared.tools == []
    assert "Role and Skill" in prepared.messages[0].content
    assert "artifact_content" in prepared.messages[0].content
    assert sum(isinstance(message, SystemMessage) for message in prepared.messages) == 1
    assert prepared.messages[-1].content == "S2 evidence"
    assert middleware.trigger_reason == "tool_calls"


def test_token_warning_reserves_a_final_model_call_before_hard_stop():
    middleware = SubagentFinalizationMiddleware(after_tool_calls=40, token_limit=1000)
    message = AIMessage(content="Useful evidence", usage_metadata={"input_tokens": 650, "output_tokens": 60, "total_tokens": 710})
    request = Request(messages=[HumanMessage(content="Research"), message], tools=["search"])
    prepared = middleware._prepare(request)
    assert prepared.tools == []
    assert middleware.trigger_reason == "token_reserve"
    assert prepared.messages[-1] is message
