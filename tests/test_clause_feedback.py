import pytest
from langchain_core.messages import HumanMessage
from dcc_harness.datasets.clause_feedback import (
    _central_control_channel as adapt_request,
)


def test_provider_adapter_is_local_and_not_sent_as_api_parameter():
    from dcc_harness.evaluation.benchmark_runner import (
        BenchmarkChatOpenAI,
        BenchmarkVllmChatModel,
    )

    messages = [HumanMessage(content="task")]
    for cls in [BenchmarkChatOpenAI, BenchmarkVllmChatModel]:
        plain = cls(model="dummy", api_key="EMPTY")._get_request_payload(messages)
        wrapped = cls(
            model="dummy",
            api_key="EMPTY",
            request_adapter_spec="dcc_harness.datasets.clause_feedback:_central_control_channel",
        )._get_request_payload(messages)
        assert wrapped["messages"] == plain["messages"]
        assert "request_adapter_spec" not in wrapped


def test_feedback_is_enabled_only_for_clause_file_plugin():
    from dcc_harness.datasets.benchmark_plugins import resolve_dataset_plugin
    from dcc_harness.preprocessing.clause import make_plugin

    assert (
        resolve_dataset_plugin("clause").model_request_adapter
        == "dcc_harness.datasets.clause_feedback:_central_control_channel"
    )
    assert resolve_dataset_plugin("cuad").model_request_adapter is None
    assert make_plugin().model_request_adapter is None


def test_central_requires_a_control_action_but_does_not_choose_which_one():
    q = {
        "messages": [{"role": "user", "content": "task"}],
        "tools": [
            {"type": "function", "function": {"name": name}}
            for name in ["sp_delegate", "sp_think", "sp_finish"]
        ],
        "tool_choice": "auto",
    }
    actual = adapt_request(q)
    assert actual["tool_choice"] == "required"
    assert actual["messages"] == q["messages"] and actual["tools"] == q["tools"]
    assert q["tool_choice"] == "auto"


@pytest.mark.parametrize(
    "choice",
    [None, "auto", "none", {"type": "function", "function": {"name": "read_file"}}],
)
def test_specialist_still_can_return_a_result_without_a_tool_call(choice):
    q = {
        "messages": [{"role": "user", "content": "task"}],
        "tools": [{"type": "function", "function": {"name": "read_file"}}],
    }
    if choice is not None:
        q["tool_choice"] = choice
    assert adapt_request(q) == q


def test_explicit_native_control_choice_is_preserved():
    for choice in ["none", {"type": "function", "function": {"name": "sp_finish"}}]:
        q = {
            "messages": [],
            "tools": [{"type": "function", "function": {"name": "sp_delegate"}}],
            "tool_choice": choice,
        }
        assert adapt_request(q) == q
