"""One scoped model correction preserves history, budgets and tool isolation."""

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.agents.middlewares.invalid_tool_call_repair_middleware import InvalidToolCallRepairMiddleware
from deerflow.config.token_budget_config import TokenBudgetConfig


class Request(SimpleNamespace):
    def override(self, **kwargs):
        return Request(**{**vars(self), **kwargs})


def bad(name="report_submit_outline"):
    return AIMessage(content="", invalid_tool_calls=[{"name": name, "args": '{"tree":{"conclusion":"broken "addition":""}}', "id": "bad-id", "error": "bad JSON"}])


def test_repair_jumps_once_without_mutating_or_executing_original_arguments():
    middleware = InvalidToolCallRepairMiddleware()
    original = bad()
    update = middleware.after_model({"messages": [original]}, None)
    assert update["jump_to"] == "model"
    assert isinstance(update["messages"][0], HumanMessage)
    assert original.invalid_tool_calls[0]["args"].endswith('""}}')
    assert middleware.events[0]["line"] == 1
    request = Request(messages=[original, *update["messages"]], tools=[SimpleNamespace(name=n) for n in ["report_read_material", "report_submit_outline"]])
    prepared = middleware.prepare_request(request)
    assert [item.name for item in prepared.tools] == ["report_submit_outline"]
    converted = next(item for item in prepared.messages if isinstance(item, AIMessage))
    assert converted.invalid_tool_calls == []
    assert original.invalid_tool_calls
    assert "broken" in converted.content
    assert middleware.after_model({"messages": [original, bad()]}, None) is None
    assert middleware.consume_stop_reason() == "tool_argument_repair_failed"


def test_valid_correction_can_execute_once_then_only_final_ack_is_allowed():
    middleware = InvalidToolCallRepairMiddleware()
    original = bad()
    update = middleware.after_model({"messages": [original]}, None)
    middleware.prepare_request(Request(messages=[original, *update["messages"]], tools=[SimpleNamespace(name="report_submit_outline")]))
    corrected = AIMessage(content="", tool_calls=[{"name": "report_submit_outline", "args": {"tree": {}}, "id": "fixed"}])
    assert middleware.after_model({"messages": [corrected]}, None) is None
    final_request = middleware.prepare_request(Request(messages=[original, corrected], tools=[SimpleNamespace(name="report_read_material")]))
    assert final_request.tools == []
    assert middleware.consume_stop_reason() is None
    assert middleware.events[-1]["status"] == "valid_call_generated"


def test_no_repair_of_mixed_batches_or_other_tools():
    for message in [bad("write_file"), bad().model_copy(update={"tool_calls": [{"name": "read_file", "args": {}, "id": "valid"}]})]:
        assert InvalidToolCallRepairMiddleware().after_model({"messages": [message]}, None) is None


def test_provider_raw_fields_are_sanitized_only_in_request_copy():
    original = bad().model_copy(update={"additional_kwargs": {"tool_calls": [{"id": "bad-id"}], "function_call": {"arguments": "bad"}}})
    middleware = InvalidToolCallRepairMiddleware()
    update = middleware.after_model({"messages": [original]}, None)
    prepared = middleware.prepare_request(Request(messages=[original, *update["messages"]], tools=[SimpleNamespace(name="report_submit_outline")]))
    converted = next(item for item in prepared.messages if isinstance(item, AIMessage))
    assert converted.additional_kwargs == {}
    assert original.additional_kwargs["tool_calls"] == [{"id": "bad-id"}]


def test_existing_guard_stop_takes_precedence_over_local_repair_failure():
    middleware = InvalidToolCallRepairMiddleware()
    update = middleware.after_model({"messages": [bad()]}, None)
    middleware.prepare_request(Request(messages=update["messages"], tools=[SimpleNamespace(name="report_submit_outline")]))
    assert middleware.after_model({"messages": [AIMessage(content="[TOKEN BUDGET EXCEEDED]")]}, None) is None
    assert middleware.consume_stop_reason() is None


def test_budget_or_cancellation_does_not_get_a_fresh_retry_budget():
    message = bad().model_copy(update={"usage_metadata": {"input_tokens": 750, "output_tokens": 100, "total_tokens": 850}})
    middleware = InvalidToolCallRepairMiddleware(budget=TokenBudgetConfig(enabled=True, max_tokens=1000), output_limit=128)
    assert middleware.after_model({"messages": [message]}, None) is None
    assert middleware.events[-1]["status"] == "skipped_budget"
    assert InvalidToolCallRepairMiddleware(aborted=lambda: True).after_model({"messages": [bad()]}, None) is None


def test_correction_cannot_open_new_material_reads():
    middleware = InvalidToolCallRepairMiddleware()
    update = middleware.after_model({"messages": [bad()]}, None)
    middleware.prepare_request(Request(messages=update["messages"], tools=[SimpleNamespace(name="report_submit_outline")]))
    unwanted = AIMessage(content="", tool_calls=[{"name": "report_read_material", "args": {"material_id": "m1"}, "id": "read"}])
    stopped = middleware.after_model({"messages": [unwanted]}, None)
    assert stopped["messages"][0].tool_calls == []
    assert middleware.consume_stop_reason() == "tool_argument_repair_failed"


def test_successful_syntax_repair_keeps_normal_schema_feedback_in_the_same_run():
    middleware = InvalidToolCallRepairMiddleware()
    update = middleware.after_model({"messages": [bad()]}, None)
    request = Request(messages=update["messages"], tools=[SimpleNamespace(name="report_submit_outline"), SimpleNamespace(name="report_read_material")])
    middleware.prepare_request(request)
    fixed = AIMessage(content="", tool_calls=[{"name": "report_submit_outline", "args": {"tree": {}}, "id": "fixed"}])
    middleware.after_model({"messages": [fixed]}, None)
    tool_request = SimpleNamespace(tool_call={"name": "report_submit_outline"})
    response = ToolMessage(content='{"error":"invalid_outline_tree","detail":"relations must be objects"}', tool_call_id="fixed")
    assert middleware.wrap_tool_call(tool_request, lambda _: response) is response
    prepared = middleware.prepare_request(request)
    assert [item.name for item in prepared.tools] == ["report_submit_outline"]
    assert "validation errors" in prepared.messages[0].content
    assert middleware.after_model({"messages": [fixed]}, None) is None
    success = ToolMessage(content='{"saved":true,"validation_ok":true}', tool_call_id="fixed")
    middleware.wrap_tool_call(tool_request, lambda _: success)
    assert middleware.prepare_request(request).tools == []
    assert middleware.consume_stop_reason() is None
