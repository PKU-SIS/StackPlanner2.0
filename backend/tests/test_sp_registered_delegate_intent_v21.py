"""Host-only registered tool identity preserves the Central's explicit role."""

from types import SimpleNamespace

import pytest

from deerflow.sp.agent_tools import SPControlActionMiddleware, _action_payload
from deerflow.sp.extensions import SPExecutionExtensions
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus


def args(tools):
    return {"target_agent": "coder", "stage": "research", "task": "读取项目文件，用document_lookup检索原句并核对来源。", "metadata": {"tool_names": tools}}


def test_default_and_pure_file_inspection_keep_native_routing():
    assert _action_payload("sp_delegate", args(["read_file", "document_lookup"]), tool_call_id="legacy")["target_agent"] == "perception"
    assert _action_payload("sp_delegate", args(["read_file"]), tool_call_id="pure", registered_tool_names=("read_file", "document_lookup"))["target_agent"] == "perception"


def test_registered_capability_request_keeps_explicit_coder_and_its_tools():
    payload = _action_payload("sp_delegate", args(["read_file", "document_lookup"]), tool_call_id="real", registered_tool_names=("read_file", "document_lookup"))
    assert payload["target_agent"] == "coder"
    assert payload["stage"] != "perception"
    assert payload["metadata"]["tool_names"] == ["read_file", "document_lookup"]
    assert "routed_from_agent" not in payload["metadata"]


def test_model_metadata_unknown_tools_and_explicit_perception_do_not_bypass_policy():
    request = args(["read_file", "document_lookup"])
    request["metadata"]["registered_tool_names"] = ["document_lookup"]
    assert _action_payload("sp_delegate", request, tool_call_id="forged")["target_agent"] == "perception"
    assert _action_payload("sp_delegate", args(["read_file", "invented_tool"]), tool_call_id="unknown", registered_tool_names=("document_lookup",))["target_agent"] == "perception"
    request["target_agent"] = "perception"
    payload = _action_payload("sp_delegate", request, tool_call_id="explicit", registered_tool_names=("document_lookup",))
    assert payload["target_agent"] == "perception"
    assert payload["metadata"]["tool_names"] == ["read_file"]


def test_positive_report_routing_remains_native():
    request = args(["read_file", "document_lookup", "write_file"])
    request["task"] = "请撰写最终审核报告。"
    result = _action_payload("sp_delegate", request, tool_call_id="report", precise_report_intent=True, registered_tool_names=("document_lookup", "write_file"))
    assert result["target_agent"] == "reporter" and result["stage"] == "reporting"


def test_native_control_passes_registered_identity_without_choosing_another_role(tmp_path):
    captured = []

    class Executor:
        def execute(self, task):
            captured.append(task)
            return SPSubagentResult(status=SPSubagentStatus.COMPLETED, result="已核对来源", artifact_type="research_observation", artifact_content="真实阶段观察", artifact_metadata={"completion_status": "complete"})

    middleware = SPControlActionMiddleware(executor_provider=lambda state, runtime: Executor(), registered_tool_names=("document_lookup",))
    request = SimpleNamespace(
        tool_call={"name": "sp_delegate", "id": "actual", "args": args(["read_file", "document_lookup"])}, state={"thread_data": {"outputs_path": str(tmp_path)}}, runtime=SimpleNamespace(context={"thread_id": "thread", "run_id": "run"})
    )
    middleware.wrap_tool_call(request, lambda _: pytest.fail("Native SP must handle this control action"))
    assert len(captured) == 1 and captured[0].subagent_type == "coder"


@pytest.mark.parametrize("value", [1, "true", None])
def test_registered_intent_flag_is_trusted_boolean(value):
    with pytest.raises(ValueError):
        SPExecutionExtensions(preserve_registered_tool_intent=value)


def test_registered_intent_is_default_off():
    assert SPExecutionExtensions().preserve_registered_tool_intent is False


@pytest.mark.parametrize("trusted", [False, True])
def test_factory_uses_only_trusted_actual_registry_not_api_context(monkeypatch, trusted):
    from deerflow.sp.runtime import make_sp_agent

    captured = {}

    class App:
        models = [SimpleNamespace(name="test-model")]
        title = SimpleNamespace(enabled=False, max_chars=60)

        @staticmethod
        def get_model_config(name):
            return SimpleNamespace(supports_thinking=False)

    class Graph:
        metadata = {}

    def lead(config, **kwargs):
        captured.update(kwargs)
        return Graph()

    monkeypatch.setattr("deerflow.agents.lead_agent.agent._make_lead_agent", lead)
    extension = SPExecutionExtensions(tools=(SimpleNamespace(name="document_lookup"),), preserve_registered_tool_intent=trusted)
    make_sp_agent({"configurable": {"model_name": "test-model", "preserve_registered_tool_intent": True}, "context": {"registered_tool_names": ["forged_tool"]}}, app_config=App(), extensions=extension)
    control = next(m for m in captured["extra_middlewares"] if isinstance(m, SPControlActionMiddleware))
    assert control._registered_tool_names == (("document_lookup",) if trusted else ())
