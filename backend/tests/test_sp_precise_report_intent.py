"""Opt-in report intent must respect stage data, polarity and native defaults."""

from types import SimpleNamespace

import pytest

from deerflow.sp.agent_tools import SPControlActionMiddleware, _action_payload
from deerflow.sp.extensions import SPExecutionExtensions
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus


@pytest.mark.parametrize(
    "text",
    [
        "Flag a shared-draft signal). Put the table in artifact_content. Report offset_reached and rows_remaining.",
        "Do NOT write a report; return classifications only.",
        "Don't generate the final report. Return stage observations.",
        "No audit report should be generated.",
        "无需生成报告；返回逐行分类。",
        "不要撰写最终报告，只返回统计。",
        "Create a classification table; report offsets and counts.",
        "Compare the shared draft and report offset_reached.",
        "Evaluate whether the product can generate an audit report.",
        "核查软件能否生成分析报告。",
        "Inspect the sentence 'write a report' and classify it.",
    ],
)
def test_precise_mode_rejects_false_report_intents(text):
    payload = _action_payload("sp_delegate", {"target_agent": "coder", "task": text, "stage": "research"}, tool_call_id="intent", precise_report_intent=True)
    assert payload["target_agent"] == "coder"
    assert "routing_reason" not in payload["metadata"]


def test_precise_mode_does_not_match_across_task_and_output_fields():
    payload = _action_payload(
        "sp_delegate", {"target_agent": "coder", "task": "Classify each row and identify a shared-draft signal", "expected_output": "Report offset_reached and counts", "stage": "research"}, tool_call_id="intent", precise_report_intent=True
    )
    assert payload["target_agent"] == "coder"


@pytest.mark.parametrize(
    "text",
    [
        "Generate the complete board report in Markdown format.",
        "Write the final audit report in Markdown.",
        "Draft a concise board report.",
        "生成简明中文审核报告。",
        "请撰写最终审核报告。",
        "将报告改写为中文。",
        "报告需要重新生成。",
        "不要编造事实；撰写审核报告。",
        "Do not invent facts. Write the final report.",
        "The audit report should be rewritten in Chinese.",
    ],
)
def test_precise_mode_retains_positive_report_routing(text):
    payload = _action_payload("sp_delegate", {"target_agent": "coder", "task": text, "stage": "implementation"}, tool_call_id="intent", precise_report_intent=True)
    assert payload["target_agent"] == "reporter"
    assert payload["stage"] == "reporting"
    assert payload["metadata"]["routing_reason"] == "pure_report_synthesis"


def test_disabled_and_model_metadata_cannot_enable_compatibility():
    args = {"target_agent": "coder", "task": "Flag a shared-draft signal). Put the table in artifact_content. Report offset.", "metadata": {"precise_report_intent": True}}
    assert not SPExecutionExtensions().precise_report_intent
    assert _action_payload("sp_delegate", args, tool_call_id="legacy")["target_agent"] == "reporter"


def test_code_priority_local_inspection_and_missing_role_remain_native():
    code = _action_payload("sp_delegate", {"target_agent": "coder", "task": "Generate an HTML report webpage and source code.", "stage": "implementation"}, tool_call_id="c", precise_report_intent=True)
    local = _action_payload("sp_delegate", {"target_agent": "coder", "task": "Read the uploaded correction memo and extract identifiers.", "metadata": {"tool_names": ["read_file"]}}, tool_call_id="p", precise_report_intent=True)
    inferred = _action_payload("sp_delegate", {"task": "Collect evidence", "stage": "research"}, tool_call_id="r", precise_report_intent=True)
    assert code["target_agent"] == "coder"
    assert local["target_agent"] == "perception" and local["metadata"]["tool_names"] == ["read_file"]
    assert inferred["target_agent"] == "researcher"


def test_opt_in_flows_through_native_control_executor(tmp_path):
    tasks = []

    class Executor:
        def execute(self, task):
            tasks.append(task)
            return SPSubagentResult(status=SPSubagentStatus.COMPLETED, result="stage done", artifact_type="research_observation", artifact_content="classified observations", artifact_metadata={"completion_status": "complete"})

    middleware = SPControlActionMiddleware(executor_provider=lambda state, runtime: Executor(), precise_report_intent=True)
    request = SimpleNamespace(
        tool_call={"name": "sp_delegate", "id": "stage", "args": {"target_agent": "coder", "stage": "research", "task": "Flag a shared-draft signal). Put the table in artifact_content. Report offset."}},
        state={"thread_data": {"outputs_path": str(tmp_path)}},
        runtime=SimpleNamespace(context={"thread_id": "t", "run_id": "r"}),
    )
    middleware.wrap_tool_call(request, lambda _: pytest.fail("native SP action must execute normally"))
    assert len(tasks) == 1 and tasks[0].subagent_type == "coder"


@pytest.mark.parametrize("value", ["true", 1, None])
def test_compatibility_flag_requires_trusted_boolean(value):
    with pytest.raises(ValueError):
        SPExecutionExtensions(precise_report_intent=value)
