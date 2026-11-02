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


def test_markdown_research_finalization_never_reintroduces_json_contract():
    middleware = SubagentFinalizationMiddleware(after_tool_calls=1, output_format="markdown")
    prepared = middleware._prepare(Request(messages=[SystemMessage(content="Research role"), ToolMessage(content="Evidence [S1]", tool_call_id="s")], tools=["search"]))
    assert prepared.tools == []
    assert "complete Markdown" in prepared.messages[0].content
    assert "normal final JSON envelope" not in prepared.messages[0].content


def test_outline_closes_collection_but_keeps_delivery_tools_before_token_cap():
    middleware = SubagentFinalizationMiddleware(after_tool_calls=30, token_limit=1000, token_fraction=0.5, keep_tools=["read_file", "write_file", "report_register_research_request"])
    message = AIMessage(content="Evidence collected", usage_metadata={"input_tokens": 510, "output_tokens": 10, "total_tokens": 520})
    request = Request(messages=[SystemMessage(content="Outline"), message], tools=[SimpleNamespace(name=name) for name in ["report_read_material", "read_file", "write_file", "report_register_research_request"]])
    prepared = middleware._prepare(request)
    assert [item.name for item in prepared.tools] == ["read_file", "write_file", "report_register_research_request"]
    assert "complete tree" in prepared.messages[0].content
    assert middleware.trigger_reason == "token_reserve"


def test_structured_consolidation_finalization_does_not_suggest_partial_files():
    middleware = SubagentFinalizationMiddleware(after_tool_calls=1, keep_tools=["report_submit_outline", "read_file"])
    request = Request(messages=[ToolMessage(content="Evidence", tool_call_id="r")], tools=[SimpleNamespace(name=n) for n in ["report_read_material", "report_submit_outline", "read_file", "write_file"]])
    prepared = middleware._prepare(request)
    assert [t.name for t in prepared.tools] == ["report_submit_outline", "read_file"]
    assert "write_file is unavailable" in prepared.messages[0].content
    assert "Alternatively" not in prepared.messages[0].content
