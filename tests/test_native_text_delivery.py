"""Native text completion must coexist with artifact and failure contracts."""

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from dcc_harness.evaluation.benchmarks import DeliveryError, extract_delivery


def test_native_recovery_exhaustion_is_never_an_empty_prediction():
    with pytest.raises(DeliveryError) as exc:
        extract_delivery({"sp_recovery_exhausted": {"target_agent": "reporter", "attempts": 6, "limit": 6}}, thread_id="t", run_id="r", native_returned=True)
    assert exc.value.status == "recovery_exhausted"


def direct_state(**overrides):
    return {
        "messages": [
            HumanMessage(
                content="Classify the supplied scenario and return one label."
            ),
            AIMessage(
                content="CombinationErr",
                id="answer",
                response_metadata={"finish_reason": "stop"},
            ),
        ],
        "sp_loop_run_id": "run-1",
        "sp_loop_iteration": 2,
        "sp_max_loop_iterations": 20,
        "sp_current_stage": "perception",
        "sp_last_handler_result": {
            "action_type": "THINK",
            "action_id": "answer",
            "next_step": "continue",
        },
        "sp_last_run_summary": "DO NOT USE THIS SUMMARY",
        **overrides,
    }


def extract(state, *, returned=True):
    return extract_delivery(
        state, thread_id="thread-1", run_id="run-1", native_returned=returned
    )


def test_collects_normal_native_text_without_finish_or_summary_fallback():
    assert extract(direct_state()) == ("CombinationErr", None)
    with pytest.raises(DeliveryError):
        extract(direct_state(), returned=False)


@pytest.mark.parametrize(
    "damage",
    [
        "human",
        "active",
        "run",
        "handler",
        "artifact_request",
        "selected_artifact",
        "partial_report",
        "budget",
    ],
)
def test_direct_text_preserves_native_execution_and_artifact_gates(damage):
    state = direct_state()
    if damage == "human":
        state["sp_pending_human_interaction"] = {"question": "Missing evidence"}
    elif damage == "active":
        state["sp_active_delegate_id"] = "working"
    elif damage == "run":
        state["sp_loop_run_id"] = "earlier-run"
    elif damage == "handler":
        state["sp_last_handler_result"] = {"next_step": "error_fatal"}
    elif damage == "artifact_request":
        state["messages"][0] = HumanMessage(
            content="Create a downloadable report file with the answer."
        )
    elif damage == "selected_artifact":
        state["sp_last_final_artifact_ref"] = "not-accepted"
    elif damage == "partial_report":
        state["sp_current_artifact_refs"] = {
            "report": {
                "type": "report",
                "is_current": True,
                "metadata": {"completion_status": "partial"},
            }
        }
    else:
        state["sp_loop_iteration"] = 19
    with pytest.raises(DeliveryError):
        extract(state)


@pytest.mark.parametrize(
    "damage",
    [
        "tool",
        "user",
        "tool_call",
        "invalid_call",
        "truncated",
        "filtered",
        "error",
        "incomplete",
        "hidden",
        "stale",
        "raw_tool_syntax",
    ],
)
def test_does_not_promote_partial_noncentral_or_failed_messages(damage):
    state = direct_state()
    message = state["messages"][-1]
    if damage == "tool":
        state["messages"].append(
            ToolMessage(content="Specialist says CombinationErr", tool_call_id="child")
        )
    elif damage == "user":
        state["messages"].append(HumanMessage(content="CombinationErr"))
    elif damage == "tool_call":
        message.tool_calls = [
            {"name": "sp_delegate", "args": {}, "id": "pending", "type": "tool_call"}
        ]
    elif damage == "invalid_call":
        message.invalid_tool_calls = [
            {
                "name": "sp_finish",
                "args": "{",
                "id": "broken",
                "error": "truncated",
                "type": "invalid_tool_call",
            }
        ]
    elif damage in {"truncated", "filtered"}:
        message.response_metadata["finish_reason"] = (
            "length" if damage == "truncated" else "content_filter"
        )
    elif damage == "error":
        message.additional_kwargs["error_type"] = "APIConnectionError"
    elif damage == "incomplete":
        message.additional_kwargs["stackplanner"] = {
            "status": "incomplete",
            "guard": "unverified_artifact_terminal_claim",
        }
    elif damage == "hidden":
        message.additional_kwargs["hide_from_ui"] = True
    elif damage == "raw_tool_syntax":
        message.content = "<｜DSML｜function_calls>sp_finish</｜DSML｜function_calls>"
    else:
        message.id = "stale-message"
    with pytest.raises(DeliveryError):
        extract(state)


def test_visible_text_blocks_do_not_expose_reasoning():
    state = direct_state()
    state["messages"][-1].content = [
        {"type": "reasoning", "reasoning": "PRIVATE REASONING"},
        {"type": "text", "text": "Answer: False."},
    ]
    assert extract(state) == ("Answer: False.", None)


@pytest.mark.parametrize(
    "error_type,status",
    [
        ("SPCentralEmptyResponse", "empty_response"),
        ("SPCentralInvalidToolCall", "protocol_error"),
        ("SPCentralToolProtocolError", "protocol_error"),
        ("APIConnectionError", "infrastructure_error"),
        ("UnknownError", "infrastructure_error"),
        ({"unexpected": "shape"}, "infrastructure_error"),
    ],
)
def test_current_native_text_error_preserves_exact_failure_classification(
    error_type, status
):
    from dcc_harness.evaluation.benchmark_runner import failure_status

    state = direct_state()
    state["messages"][-1].additional_kwargs["error_type"] = error_type

    with pytest.raises(DeliveryError) as error:
        extract(state)

    assert error.value.status == status
    assert failure_status(state) == status


@pytest.mark.parametrize(
    "error_name,status",
    [
        ("SPCentralEmptyResponse", "empty_response"),
        ("SPCentralInvalidToolCall", "protocol_error"),
        ("SPCentralToolProtocolError", "protocol_error"),
    ],
)
def test_native_middleware_policy_fallback_is_not_an_infrastructure_error(
    error_name, status
):
    from types import SimpleNamespace

    from deerflow.agents.middlewares.llm_error_handling_middleware import (
        LLMErrorHandlingMiddleware,
    )
    from deerflow.config.app_config import AppConfig
    from deerflow.config.sandbox_config import SandboxConfig
    from deerflow.sp import central_resilience

    from dcc_harness.evaluation.benchmark_runner import failure_status

    middleware = LLMErrorHandlingMiddleware(
        app_config=AppConfig(sandbox=SandboxConfig(use="test"))
    )
    error_class = getattr(central_resilience, error_name)

    async def invalid_model_output(_request):
        raise error_class("Invalid generated control response")

    message = asyncio.run(
        middleware.awrap_model_call(SimpleNamespace(), invalid_model_output)
    )

    assert message.additional_kwargs["error_type"] == error_name
    assert message.additional_kwargs["deerflow_error_fallback"] is True
    state = direct_state(messages=[message])
    with pytest.raises(DeliveryError) as error:
        extract(state)
    assert error.value.status == "unfinished"
    assert failure_status(state) == status


def test_timeout_after_a_visible_message_is_not_promoted(tmp_path, monkeypatch):
    import deerflow.sp.runtime as native

    from dcc_harness.data import Task
    from dcc_harness.evaluation.benchmark_runner import Settings, run_task

    class Graph:
        def __init__(self, run_id):
            self.run_id = run_id

        async def astream(self, inputs, **kwargs):
            yield "values", direct_state(sp_loop_run_id=self.run_id)
            raise TimeoutError("Graph did not return")

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "native"))
    monkeypatch.setattr(
        native,
        "make_sp_agent",
        lambda config, **kwargs: Graph(config["context"]["run_id"]),
    )
    task = Task(
        id="timeout",
        benchmark="cuad",
        split="test",
        prompt="Return a JSON array.",
        document="California law applies.",
    )
    settings = Settings(
        config="configs/stackplanner_benchmarks.yaml",
        base_url="http://127.0.0.1:1/v1",
        model="scripted",
    )
    result = asyncio.run(run_task(task, settings, output_dir=tmp_path / "attempt"))
    assert result["status"] == "infrastructure_error"
    assert result["native_returned"] is False
    assert "delivery" not in result


@pytest.mark.sp_runtime
def test_real_native_graph_direct_answer_is_collected(tmp_path, monkeypatch):
    import deerflow.agents.lead_agent.agent as lead
    from langchain_core.language_models.fake_chat_models import (
        FakeMessagesListChatModel,
    )

    from dcc_harness.data import Task
    from dcc_harness.evaluation.benchmark_runner import Settings, run_task

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            assert not {"sp_ask_human", "ask_clarification"} & {
                tool.name for tool in tools
            }
            return self

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "native"))
    model = Model(
        responses=[
            AIMessage(
                content='["California law"]',
                response_metadata={"finish_reason": "stop"},
            )
        ]
    )
    monkeypatch.setattr(lead, "create_chat_model", lambda **kwargs: model)
    task = Task(
        id="direct",
        benchmark="cuad",
        split="test",
        prompt="Return the governing law as a JSON array.",
        document="California law applies.",
    )
    settings = Settings(
        config="configs/stackplanner_benchmarks.yaml",
        base_url="http://127.0.0.1:1/v1",
        model="scripted",
    )
    result = asyncio.run(run_task(task, settings, output_dir=tmp_path / "attempt"))
    assert result["status"] == "completed", result
    assert result["delivery"] == '["California law"]'
    assert result["delivery_source"] == "native_text"
    assert result["native_returned"] is True
    import json

    state = json.loads((tmp_path / "attempt/state.json").read_text())
    assert state["sp_non_interactive"] is True
    assert not state.get("sp_pending_human_interaction")


@pytest.mark.parametrize(
    "text",
    [
        '<tool_call>{"name":"sp_finish","arguments":{"run_summary":"False"}}</tool_call>',
        '<tool_call>{"name":"sp_finish"}',
        '{"name":"sp_finish","arguments":{"result":"False"}}',
        '{"name":"sp_delegate","arguments":{"task":"read"}}',
        "False. <|im_end|>",
        "<|im_start|>assistant\nFalse",
    ],
)
def test_unrecovered_qwen_protocol_is_not_a_native_text_delivery(text):
    state = direct_state()
    state["messages"][-1].content = text
    with pytest.raises(DeliveryError) as error:
        extract(state)
    assert error.value.status == "protocol_error"
