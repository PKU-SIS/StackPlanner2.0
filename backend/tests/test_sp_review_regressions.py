"""Regressions for tool budgets, task contracts and evidence acceptance."""

import json
from types import SimpleNamespace as NS

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.agents.middlewares.loop_detection_middleware import LoopDetectionMiddleware
from deerflow.agents.middlewares.web_search_budget_middleware import WebSearchBudgetMiddleware
from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.handlers.delegate import DelegateHandler, _report_language_alignment_gaps
from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.runtime import _apply_sp_stage_tool_policy
from deerflow.sp.subagents.adapter import SPSubagentResult, SPSubagentStatus, SPSubagentTask
from deerflow.sp.subagents.dr2_adapter import _coder_reported_behavior_failure, _implementation_verification, _test_execution_verification, normalize_dr2_subagent_result
from deerflow.subagents.config import SubagentConfig


def call(i, query):
    return {"id": str(i), "name": "web_search", "args": {"query": query}, "type": "tool_call"}


def task(**kw):
    fields = dict(action_id="review", subagent_type="coder", task="筛选数据，仅保留金额大于100的记录", description="数据筛选", metadata={"stage": "implementation"})
    fields.update(kw)
    return SPSubagentTask(**fields)


def trace(name, args, content="Exit Code: 0", status="success"):
    return [{"type": "ai", "tool_calls": [{"id": "x", "name": name, "args": args}]}, {"type": "tool", "name": name, "tool_call_id": "x", "content": content, "status": status}]


def test_search_parallel_batch_must_not_exceed_eight():
    messages = [HumanMessage(content="研究")]
    for i in range(7):
        messages += [AIMessage(content="", tool_calls=[call(i, f"query {i}")]), ToolMessage(content="ok", name="web_search", tool_call_id=str(i))]
    batch = [call(i, f"query {i}") for i in range(7, 11)]
    messages.append(AIMessage(content="", tool_calls=batch))
    executed = []
    for tc in batch:
        req = NS(tool_call=tc, state={"messages": messages})
        WebSearchBudgetMiddleware().wrap_tool_call(req, lambda r: executed.append(r.tool_call["id"]) or ToolMessage(content="ok", name="web_search", tool_call_id=r.tool_call["id"]))
    assert 7 + len(executed) <= 8, f"provider calls = {7 + len(executed)}"


def test_twentieth_tool_call_should_be_allowed():
    guard = LoopDetectionMiddleware(tool_total_hard_limit=20, tool_freq_hard_limit=100, hard_limit=100)
    runtime = NS(context={"thread_id": "review", "run_id": "run"})
    for i in range(20):
        state = {"messages": [AIMessage(content="", tool_calls=[{"id": str(i), "name": "read_file", "args": {"path": str(i)}, "type": "tool_call"}])]}
        _, stopped = guard._track_and_check(state, runtime)
        assert not stopped, f"call {i + 1} blocked before execution"


def test_data_processing_should_complete_without_source_patch_or_pytest():
    result = NS(status="completed", result=json.dumps({"summary": "数据筛选完成", "artifact_metadata": {"completion_status": "complete"}}), ai_messages=trace("bash", {"command": 'python -c "print([200])"'}))
    normalized = normalize_dr2_subagent_result(result, task=task())
    assert normalized.completion_status == "complete", normalized.artifact_metadata["evidence_gaps"]


def test_delegate_preserves_explicit_nonimplementation_contract():
    captured = []

    def execute(t):
        captured.append(t)
        return SPSubagentResult(status=SPSubagentStatus.COMPLETED, result="数据筛选完成")

    action = SPAction.create(ActionType.DELEGATE, reason="筛选数据", task="筛选数据", target_agent="coder", stage="implementation", metadata={"requires_implementation": False})
    DelegateHandler(executor=NS(execute=execute)).handle(action, HandlerContext(state={}, stack=TaskMemoryStack(), run_id="review"))
    assert captured[0].metadata["requires_implementation"] is False


def test_nonimplementation_data_work_must_keep_execution_tool():
    cfg = SubagentConfig(name="review", description="review", system_prompt="review", tools=["read_file", "bash", "write_file"])
    t = task(metadata={"stage": "implementation", "requires_implementation": False})
    assert "bash" in _apply_sp_stage_tool_policy(cfg, t).tools


def test_chinese_prose_with_english_data_and_urls_should_pass():
    output = "本次筛选结果如下，所有记录均满足指定条件，详细来源和原始字段保留在表格中。\n"
    output += "|名称|来源|\n|---|---|\n" + "\n".join("|Example Corporation|https://example.com/research/datasets/annual/revenue/2025/records| " for _ in range(8))
    assert not _report_language_alignment_gaps(output, task=task(context_refs={"latest_user_input": "请用中文说明筛选结果，并保留原始名称和来源网址"}))


@pytest.mark.parametrize("command", ["python -m py_compile app.py", 'python -c "assert 2 + 2 == 4"'])
def test_successful_nonframework_checks_should_count(command):
    assert _test_execution_verification(trace("bash", {"command": command}))["passed"]


def test_echoing_pytest_is_not_running_tests():
    assert not _test_execution_verification(trace("bash", {"command": "echo pytest"}))["passed"]


def test_failed_tool_status_must_not_count_as_success():
    assert not _test_execution_verification(trace("bash", {"command": "pytest"}, "Command timed out", status="error"))["passed"]


def test_writing_csv_is_not_source_implementation():
    assert not _implementation_verification(trace("write_file", {"path": "/mnt/user-data/outputs/filtered.csv"}, "File written successfully"))["passed"]


def test_resolved_failure_should_not_poison_final_result():
    messages = trace("bash", {"command": "pytest"}, "test behavior failed\nExit Code: 1")
    messages += trace("bash", {"command": "pytest"}, "1 passed\nExit Code: 0")
    assert not _coder_reported_behavior_failure("所有修复后的测试已通过", messages)


def test_do_not_use_english_does_not_disable_chinese_acceptance():
    assert _report_language_alignment_gaps("This report is entirely written in English. " * 15, task=task(context_refs={"latest_user_input": "请用中文写报告，不要用英文"}))


def test_recovery_must_not_replace_unrelated_research_with_code_patch():
    from deerflow.sp.actions.router import ActionRouter

    stack = TaskMemoryStack()
    stack.append_observe(
        "数据筛选已完成", actor="coder", run_id="review", stage="implementation", metadata={"completion_status": "partial", "evidence_gaps": ["Coder claimed the implementation was complete, but no non-test source file was changed."]}
    )
    action = SPAction.create(ActionType.DELEGATE, reason="补充来源", task="检索这些数据的来源", target_agent="researcher", stage="research")
    ActionRouter._normalize_coder_source_recovery_target(action, {"sp_task_memory": stack.to_dict()}, run_id="review")
    assert action.target_agent == "researcher", action.task


@pytest.mark.parametrize("objective", ["筛选CSV数据，不要修改源码", "Filter the data; do not modify source code", "计算数据结果，无需运行软件测试"])
def test_data_tasks_with_explicit_software_exclusions_remain_data_tasks(objective):
    from deerflow.sp.subagents.dr2_adapter import coder_task_kind

    t = task(task=objective)
    assert coder_task_kind(t) == "data_processing"


def test_data_export_after_computation_does_not_require_running_software_tests():
    messages = trace("bash", {"command": "python -c 'assert 200 > 100; print(200)'"})
    export = trace("write_file", {"path": "/mnt/user-data/outputs/filtered.csv", "content": "value\n200"}, "OK")
    for message in export:
        if message["type"] == "ai":
            message["tool_calls"][0]["id"] = "export"
        else:
            message["tool_call_id"] = "export"
    raw = NS(status="completed", result=json.dumps({"summary": "筛选完成并验证结果", "artifact_metadata": {"completion_status": "complete"}}), ai_messages=[*messages, *export])
    assert normalize_dr2_subagent_result(raw, task=task(metadata={"stage": "implementation", "task_kind": "data_processing", "requires_implementation": False})).completion_status == "complete"


def test_rerunning_one_check_does_not_hide_a_different_failed_check():
    messages = trace("bash", {"command": "pytest first.py"}, "test behavior failed\nExit Code: 1")
    messages += trace("bash", {"command": "pytest second.py"}, "1 passed\nExit Code: 0")
    assert _coder_reported_behavior_failure("仍有一个失败", messages)


@pytest.mark.parametrize("path", ["/workspace/README.md", "/workspace/data.json", "/mnt/user-data/outputs/result.csv"])
def test_non_source_writes_do_not_satisfy_implementation(path):
    assert not _implementation_verification(trace("write_file", {"path": path}, "OK"))["passed"]


def test_noop_source_replacement_does_not_satisfy_implementation():
    assert not _implementation_verification(trace("str_replace", {"path": "app.py", "old_string": "x = 1", "new_string": "x = 1"}, "OK"))["passed"]


@pytest.mark.parametrize("budget", [{"max_searches": 0}, {"max_tool_calls": True}, {"unrecognized": 1}])
def test_invalid_delegation_budgets_are_rejected(budget):
    with pytest.raises(ValueError):
        task(budgets=budget).validate_protocol()


def test_data_recovery_prompt_does_not_require_source_changes():
    from deerflow.sp.agent_tools import SPFinishAvailabilityMiddleware

    stack = TaskMemoryStack()
    stack.append_observe(
        "数据筛选有缺失",
        actor="coder",
        run_id="review",
        stage="implementation",
        metadata={"target_agent": "coder", "task_kind": "data_processing", "requires_implementation": False, "completion_status": "partial", "evidence_gaps": ["缺少一列"]},
    )
    instruction = SPFinishAvailabilityMiddleware._artifact_recovery_instruction({"sp_loop_run_id": "review", "sp_task_memory": stack.to_dict()})
    assert "source implementation recovery" not in instruction
    assert "data processing" in instruction


@pytest.mark.parametrize("old,new,passed", [("x=1", "x=2", True), ("x=1", "x=1", False)])
def test_actual_str_replace_schema_records_only_real_changes(old, new, passed):
    assert _implementation_verification(trace("str_replace", {"path": "app.py", "old_str": old, "new_str": new}, "OK"))["passed"] is passed


def test_writing_identical_previously_read_source_is_not_implementation():
    messages = trace("read_file", {"path": "app.py"}, "x=1\n")
    write = trace("write_file", {"path": "app.py", "content": "x=1\n"}, "OK")
    for message in write:
        if message["type"] == "ai":
            message["tool_calls"][0]["id"] = "write"
        else:
            message["tool_call_id"] = "write"
    assert not _implementation_verification([*messages, *write])["passed"]


def test_stale_source_metadata_does_not_override_explicit_data_contract():
    from deerflow.sp.agent_tools import SPFinishAvailabilityMiddleware

    metadata = {
        "target_agent": "coder",
        "task_kind": "data_processing",
        "requires_implementation": False,
        "completion_status": "partial",
        "evidence_gaps": ["缺少数据列"],
        "implementation_verification": {"passed": True},
        "delegate_stage": "implementation",
    }
    stack = TaskMemoryStack()
    stack.append_observe("缺少一列", actor="coder", run_id="review", stage="implementation", metadata=metadata)
    instruction = SPFinishAvailabilityMiddleware._artifact_recovery_instruction({"sp_loop_run_id": "review", "sp_task_memory": stack.to_dict()})
    assert "read-only verification recovery" not in instruction
    assert not SPFinishAvailabilityMiddleware._requires_independent_code_verification({"metadata": metadata})
