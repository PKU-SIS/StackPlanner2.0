"""Tests for the SP -> DR2 SubagentExecutor adapter."""

import json
from dataclasses import dataclass

from deerflow.sp.subagents import (
    A2A_DELEGATE_MESSAGE,
    A2A_PROTOCOL_VERSION,
    DR2SubagentExecutorAdapter,
    SPSubagentStatus,
    SPSubagentTask,
    render_sp_subagent_prompt,
)
from deerflow.sp.subagents.dr2_adapter import coder_task_requires_implementation
from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.handlers.context import build_handler_context_refs
from deerflow.sp.memory import TaskMemoryStack


@dataclass
class FakeDR2Result:
    status: str
    result: str | None = None
    error: str | None = None
    stop_reason: str | None = None
    task_id: str | None = None
    token_usage_records: list[dict] | None = None
    ai_messages: list[dict] | None = None


class FakeDR2Executor:
    def __init__(self, result: FakeDR2Result):
        self.result = result
        self.prompts: list[str] = []

    def execute(self, task: str) -> FakeDR2Result:
        self.prompts.append(task)
        return self.result


class SequenceDR2Executor:
    def __init__(self, results: list[FakeDR2Result]):
        self.results = list(results)
        self.prompts: list[str] = []

    def execute(self, task: str) -> FakeDR2Result:
        self.prompts.append(task)
        return self.results.pop(0)


def _sp_task() -> SPSubagentTask:
    return SPSubagentTask(
        action_id="act-1",
        subagent_type="researcher",
        task="Research migration risks",
        description="Need evidence",
        input_refs=["artifact://outline"],
        expected_output="short risk summary",
        context_refs={"task_memory": {"entries": [{"action": "think", "content": "plan"}]}},
        thread_id="thread-1",
        run_id="run-1",
        metadata={"stage": "research"},
    )


def test_coder_verification_task_requires_implementation_by_default():
    task = SPSubagentTask(
        action_id="act-1",
        subagent_type="coder",
        task="Investigate the current behavior and verify the fix.",
        description="Check the implementation.",
        expected_output="Verification findings.",
        metadata={"stage": "verification"},
    )

    assert coder_task_requires_implementation(task) is True


def test_render_sp_subagent_prompt_carries_structured_context():
    prompt = render_sp_subagent_prompt(_sp_task())

    assert "<sp-subagent-task>" in prompt
    assert '"action_id": "act-1"' in prompt
    assert '"subagent_type": "researcher"' in prompt
    assert '"artifact://outline"' in prompt
    assert '"task_memory"' in prompt


def test_render_sp_subagent_prompt_preserves_authoritative_task_contract():
    task = _sp_task()
    task.context_refs["task_contract"] = {
        "authoritative": True,
        "immutable": True,
        "original_issue": "Remove the automatic transform.",
        "fail_to_pass": ["tests/test_core.py::test_exact_behavior"],
    }

    prompt = render_sp_subagent_prompt(task)

    assert '"task_contract"' in prompt
    assert "authoritative and immutable" in prompt


def test_delegate_context_carries_task_contract_without_arbitrary_metadata():
    refs = build_handler_context_refs(
        HandlerContext(
            state={
                "messages": [],
                "sp_task_contract": {
                    "authoritative": True,
                    "immutable": True,
                    "original_issue": "Fix the exact boundary behavior.",
                    "fail_to_pass": ["tests/test_core.py::test_boundary"],
                    "secret": "must not cross the delegation boundary",
                }
            },
            stack=TaskMemoryStack(),
            run_id="run-1",
        )
    )

    assert refs["task_contract"]["original_issue"] == "Fix the exact boundary behavior."
    assert refs["task_contract"]["fail_to_pass"] == ["tests/test_core.py::test_boundary"]
    assert "secret" not in refs["task_contract"]


def test_a2a_task_envelope_carries_stage_tools_acceptance_and_budget():
    task = SPSubagentTask(
        action_id="act-a2a",
        subagent_type="researcher",
        task="Find verified evidence",
        description="Research",
        stage="research",
        allowed_tools=["web_search", "web_fetch"],
        acceptance_criteria=["Cite the source"],
        budgets={"max_searches": 4},
        run_id="run-a2a",
    )

    envelope = task.protocol_payload()
    assert envelope["protocol_version"] == A2A_PROTOCOL_VERSION
    assert envelope["message_type"] == A2A_DELEGATE_MESSAGE
    assert envelope["task_id"] == "act-a2a"
    assert envelope["receiver"] == "researcher"
    assert envelope["stage"] == "research"
    assert envelope["allowed_tools"] == ["web_search", "web_fetch"]
    assert envelope["acceptance_criteria"] == ["Cite the source"]
    assert envelope["budgets"] == {"max_searches": 4}


def test_a2a_task_rejects_non_central_sender_and_mismatched_receiver():
    task = _sp_task()
    task.sender = "researcher"
    try:
        task.validate_protocol()
    except ValueError as exc:
        assert "Only Central" in str(exc)
    else:
        raise AssertionError("non-central sender must be rejected")

    task.sender = "central"
    task.receiver = "coder"
    try:
        task.validate_protocol()
    except ValueError as exc:
        assert "receiver" in str(exc)
    else:
        raise AssertionError("mismatched receiver must be rejected")


def test_dr2_subagent_executor_adapter_normalizes_success_result():
    executor = FakeDR2Executor(FakeDR2Result(status="completed", result="done", task_id="task-1"))
    adapter = DR2SubagentExecutorAdapter(lambda task: executor)

    result = adapter.execute(_sp_task())

    assert result.status == SPSubagentStatus.COMPLETED
    assert result.result == "done"
    assert result.task_id == "task-1"
    assert len(executor.prompts) == 1
    assert "Research migration risks" in executor.prompts[0]


def test_dr2_subagent_executor_adapter_maps_unknown_status_to_failed():
    executor = FakeDR2Executor(FakeDR2Result(status="unexpected", error="bad status", task_id="task-bad"))
    adapter = DR2SubagentExecutorAdapter(lambda task: executor)

    result = adapter.execute(_sp_task())

    assert result.status == SPSubagentStatus.FAILED
    assert result.error == "bad status"
    assert result.task_id == "task-bad"


def test_no_response_is_not_marked_complete():
    executor = FakeDR2Executor(
        FakeDR2Result(
            status="completed",
            result="No response generated",
            task_id="task-no-response",
        )
    )

    result = DR2SubagentExecutorAdapter(lambda _: executor).execute(_sp_task())

    assert result.artifact_metadata["completion_status"] == "partial"
    assert result.artifact_metadata["evidence_gaps"] == ["Subagent returned no usable response or artifact."]


def test_adapter_parses_structured_artifact_contract_and_usage():
    executor = FakeDR2Executor(
        FakeDR2Result(
            status="completed",
            result="""```json
{"summary":"Verified three migration risks.","artifact_content":"# Evidence\\n\\nFull research body","artifact_type":"research_observation","artifact_metadata":{"sources":3}}
```""",
            task_id="task-structured",
            token_usage_records=[{"input_tokens": 10, "output_tokens": 5}],
        )
    )

    result = DR2SubagentExecutorAdapter(lambda task: executor).execute(_sp_task())

    assert result.result == "Verified three migration risks."
    assert result.artifact_content == "# Evidence\n\nFull research body"
    assert result.artifact_type == "research_observation"
    assert result.artifact_metadata == {
        "sources": 3,
        "completion_status": "complete",
        "evidence_gaps": [],
    }
    assert result.token_usage_records == [{"input_tokens": 10, "output_tokens": 5}]
    result_envelope = result.protocol_payload(task=_sp_task())
    assert result_envelope["message_type"] == "task_result"
    assert result_envelope["completion_status"] == "complete"
    assert result_envelope["receiver"] == "central"


def test_adapter_parses_structured_artifact_after_natural_language_preamble():
    executor = FakeDR2Executor(
        FakeDR2Result(
            status="completed",
            result=('我已经拿到了所有数据，现在生成报告。\n\n{"summary":"报告已完成","artifact_content":"# 干净的 Markdown 报告\\n\\n正文","artifact_type":"report_revision","artifact_metadata":{}}'),
            task_id="task-prefaced-json",
        )
    )

    result = DR2SubagentExecutorAdapter(lambda task: executor).execute(_sp_task())

    assert result.result == "报告已完成"
    assert result.artifact_content == "# 干净的 Markdown 报告\n\n正文"
    assert result.artifact_type == "report_revision"


def test_adapter_cleans_fenced_markdown_and_nested_report_contract():
    nested = '{"summary":"inner","artifact_content":"```markdown\\n# Clean report\\n\\nBody\\n```","artifact_type":"report_revision","artifact_metadata":{}}'
    executor = FakeDR2Executor(
        FakeDR2Result(
            status="completed",
            result=json.dumps(
                {
                    "summary": "outer",
                    "artifact_content": nested,
                    "artifact_type": "report_revision",
                    "artifact_metadata": {},
                }
            ),
            task_id="task-nested-report",
        )
    )
    task = _sp_task()
    task.subagent_type = "reporter"

    result = DR2SubagentExecutorAdapter(lambda _: executor).execute(task)

    assert result.artifact_content == "# Clean report\n\nBody"


def test_adapter_recovers_created_output_when_final_json_has_unescaped_quotes():
    executor = FakeDR2Executor(
        FakeDR2Result(
            status="completed",
            result=('The webpage is finished.\n{"summary":"采用"Neural Noir"风格","artifact_content":null,"artifact_type":"generated_file","artifact_metadata":{"created_paths":["/mnt/user-data/outputs/index.html"]}}'),
            task_id="task-malformed-json",
        )
    )
    task = _sp_task()
    task.subagent_type = "coder"

    result = DR2SubagentExecutorAdapter(lambda _: executor).execute(task)

    assert result.artifact_content is None
    assert result.artifact_metadata["created_paths"] == ["/mnt/user-data/outputs/index.html"]


def test_adapter_recovers_output_path_from_file_tool_calls_when_result_omits_it():
    executor = FakeDR2Executor(
        FakeDR2Result(
            status="completed",
            result="Webpage created and validated.",
            task_id="task-tool-path",
            ai_messages=[
                {
                    "type": "ai",
                    "tool_calls": [
                        {
                            "name": "write_file",
                            "args": '{"path":"/mnt/user-data/outputs/site.html","content":"<html></html>"}',
                        },
                        {
                            "name": "write_file",
                            "args": {"path": "/mnt/user-data/workspace/internal.txt", "content": "private"},
                        },
                    ],
                }
            ],
        )
    )
    task = _sp_task()
    task.subagent_type = "coder"

    result = DR2SubagentExecutorAdapter(lambda _: executor).execute(task)

    assert result.artifact_metadata["created_paths"] == ["/mnt/user-data/outputs/site.html"]


def test_adapter_externalizes_unstructured_large_result_as_defensive_fallback():
    large_result = "research evidence " * 200
    executor = FakeDR2Executor(FakeDR2Result(status="completed", result=large_result, task_id="task-large"))

    result = DR2SubagentExecutorAdapter(lambda task: executor).execute(_sp_task())

    assert result.artifact_content == large_result
    assert result.artifact_type == "research_observation"
    assert result.result.endswith("...<truncated>")
    assert len(result.result) == 700


def test_adapter_preserves_timeout_status_and_observes_raw_result():
    raw = FakeDR2Result(status="timed_out", error="deadline", task_id="task-timeout")
    executor = FakeDR2Executor(raw)
    observed = []

    result = DR2SubagentExecutorAdapter(lambda task: executor, result_observer=observed.append).execute(_sp_task())

    assert result.status == SPSubagentStatus.TIMED_OUT
    assert result.error == "deadline"
    assert observed == [raw]


def test_adapter_marks_guard_capped_success_as_partial_with_an_evidence_gap():
    executor = FakeDR2Executor(
        FakeDR2Result(
            status="completed",
            result='{"summary":"Recovered partial analysis.","artifact_content":null,"artifact_type":"research_observation","artifact_metadata":{"completion_status":"complete"}}',
            stop_reason="token_capped",
            task_id="task-capped",
        )
    )

    result = DR2SubagentExecutorAdapter(lambda task: executor).execute(_sp_task())

    assert result.status == SPSubagentStatus.COMPLETED
    assert result.stop_reason == "token_capped"
    assert result.artifact_metadata["completion_status"] == "partial"
    assert result.artifact_metadata["evidence_gaps"] == ["Subagent execution ended early: token_capped"]


def test_researcher_recovers_successful_fetch_evidence_when_contract_omits_artifact():
    executor = FakeDR2Executor(
        FakeDR2Result(
            status="completed",
            result=json.dumps(
                {
                    "summary": "Fetched China; India and Brazil remain.",
                    "artifact_content": None,
                    "artifact_type": "research_observation",
                    "artifact_metadata": {
                        "completion_status": "partial",
                        "evidence_gaps": ["India and Brazil were not fetched."],
                    },
                }
            ),
            task_id="task-partial-fetch",
            ai_messages=[
                {
                    "type": "ai",
                    "tool_calls": [
                        {
                            "id": "fetch-china",
                            "name": "web_fetch",
                            "args": {"url": "https://api.worldbank.org/v2/country/CHN/indicator/NY.GDP.PCAP.CD?date=2023&format=json"},
                        }
                    ],
                },
                {
                    "type": "tool",
                    "name": "web_fetch",
                    "tool_call_id": "fetch-china",
                    "content": '[{"countryiso3code":"CHN","date":"2023","value":12951.1782397043}]',
                },
            ],
        )
    )

    result = DR2SubagentExecutorAdapter(lambda _: executor).execute(_sp_task())

    assert result.artifact_metadata["completion_status"] == "partial"
    assert result.artifact_metadata["recovered_tool_evidence"] is True
    assert result.artifact_content is not None
    assert "Untrusted source data" in result.artifact_content
    assert '"countryiso3code":"CHN"' in result.artifact_content
    assert "12951.1782397043" in result.artifact_content
    assert len(executor.prompts) == 2


def test_researcher_does_not_recover_failed_fetch_as_evidence():
    executor = FakeDR2Executor(
        FakeDR2Result(
            status="completed",
            result='{"summary":"Fetch failed.","artifact_content":null,"artifact_type":"research_observation","artifact_metadata":{"completion_status":"blocked","evidence_gaps":["API unavailable."]}}',
            task_id="task-failed-fetch",
            ai_messages=[
                {
                    "type": "tool",
                    "name": "web_fetch",
                    "tool_call_id": "fetch-failed",
                    "content": "Error: upstream timeout",
                }
            ],
        )
    )

    result = DR2SubagentExecutorAdapter(lambda _: executor).execute(_sp_task())

    assert result.artifact_content is None
    assert "recovered_tool_evidence" not in result.artifact_metadata


def test_researcher_automatically_continues_bounded_multi_item_fetches():
    def pass_result(
        *,
        country: str,
        value: float,
        status: str,
        gaps: list[str],
    ) -> FakeDR2Result:
        return FakeDR2Result(
            status="completed",
            result=json.dumps(
                {
                    "summary": f"Fetched {country}={value}.",
                    "artifact_content": json.dumps({"country": country, "value": value}),
                    "artifact_type": "research_observation",
                    "artifact_metadata": {
                        "completion_status": status,
                        "evidence_gaps": gaps,
                    },
                }
            ),
            task_id=f"task-{country.lower()}",
            ai_messages=[
                {
                    "type": "tool",
                    "name": "web_fetch",
                    "tool_call_id": f"fetch-{country.lower()}",
                    "content": json.dumps({"country": country, "value": value}),
                }
            ],
        )

    executor = SequenceDR2Executor(
        [
            pass_result(
                country="CHN",
                value=12951.1782397043,
                status="partial",
                gaps=["IND and BRA remain."],
            ),
            pass_result(
                country="IND",
                value=2434.45111237626,
                status="partial",
                gaps=["BRA remains."],
            ),
            pass_result(
                country="BRA",
                value=10377.5892792557,
                status="complete",
                gaps=[],
            ),
        ]
    )
    task = _sp_task()
    task.task = "Fetch CHN, IND, and BRA from the official API."
    task.expected_output = "All three exact values."

    result = DR2SubagentExecutorAdapter(lambda _: executor).execute(task)

    assert len(executor.prompts) == 3
    assert "resolve only the remaining items" in executor.prompts[1]
    assert "IND and BRA remain." in executor.prompts[1]
    assert "BRA remains." in executor.prompts[2]
    assert result.artifact_metadata["completion_status"] == "complete"
    assert result.artifact_metadata["research_continuation_attempts"] == 2
    assert result.artifact_metadata["research_pass_count"] == 3
    assert "2434.45111237626" in result.artifact_metadata["central_evidence_preview"]
    assert result.artifact_content is not None
    assert all(expected in result.artifact_content for expected in ("12951.1782397043", "2434.45111237626", "10377.5892792557"))
    assert result.artifact_content.index("10377.5892792557") < (result.artifact_content.index("12951.1782397043"))


def test_coder_complete_claim_is_downgraded_without_successful_execution_evidence():
    executor = FakeDR2Executor(
        FakeDR2Result(
            status="completed",
            result=json.dumps(
                {
                    "summary": "Script written and tests passed.",
                    "artifact_content": "print('ok')\n",
                    "artifact_type": "generated_file",
                    "artifact_metadata": {
                        "completion_status": "complete",
                        "evidence_gaps": [],
                    },
                }
            ),
            task_id="task-unverified-code",
            ai_messages=[],
        )
    )
    task = _sp_task()
    task.subagent_type = "coder"
    task.task = "Write a Python script and actually run its tests."
    task.expected_output = "A tested downloadable Python file."

    result = DR2SubagentExecutorAdapter(lambda _: executor).execute(task)

    assert result.artifact_metadata["completion_status"] == "partial"
    assert result.artifact_metadata["execution_verification"] == {
        "required": True,
        "passed": False,
        "successful_execution_count": 0,
        "latest_source_write_index": None,
        "latest_successful_execution_index": None,
    }
    assert result.artifact_metadata["evidence_gaps"] == ["Coder claimed execution or test verification without a successful execution-tool result after the latest source change."]


def test_verification_stage_uses_stage_role_in_unverified_test_gap():
    executor = FakeDR2Executor(
        FakeDR2Result(
            status="completed",
            result=json.dumps(
                {
                    "summary": "The local test environment is unavailable.",
                    "artifact_metadata": {"completion_status": "complete", "evidence_gaps": []},
                }
            ),
            task_id="task-verification-role",
            ai_messages=[],
        )
    )
    task = _sp_task()
    task.subagent_type = "coder"
    task.stage = "verification"
    task.metadata["verification_only"] = True

    result = DR2SubagentExecutorAdapter(lambda _: executor).execute(task)

    assert result.artifact_metadata["completion_status"] == "partial"
    assert result.artifact_metadata["evidence_gaps"] == [
        "Verification subagent claimed test verification without a successful test-command result after the latest source change."
    ]


def test_coder_complete_claim_accepts_successful_execution_after_source_write():
    executor = FakeDR2Executor(
        FakeDR2Result(
            status="completed",
            result=json.dumps(
                {
                    "summary": "Script written and tests passed.",
                    "artifact_content": None,
                    "artifact_type": "generated_file",
                    "artifact_metadata": {
                        "completion_status": "complete",
                        "evidence_gaps": [],
                        "created_paths": ["/mnt/user-data/outputs/verified.py"],
                    },
                }
            ),
            task_id="task-verified-code",
            ai_messages=[
                {
                    "type": "ai",
                    "tool_calls": [
                        {
                            "id": "write-1",
                            "name": "write_file",
                            "args": {
                                "path": "/mnt/user-data/outputs/verified.py",
                                "content": "print('ok')\n",
                            },
                        }
                    ],
                },
                {
                    "type": "tool",
                    "name": "write_file",
                    "tool_call_id": "write-1",
                    "content": "OK",
                },
                {
                    "type": "ai",
                    "tool_calls": [
                        {
                            "id": "bash-1",
                            "name": "bash",
                            "args": {"command": "python /mnt/user-data/outputs/verified.py"},
                        }
                    ],
                },
                {
                    "type": "tool",
                    "name": "bash",
                    "tool_call_id": "bash-1",
                    "content": "ok",
                },
            ],
        )
    )
    task = _sp_task()
    task.subagent_type = "coder"
    task.task = "Write a Python script and actually run its tests."
    task.expected_output = "A tested downloadable Python file."

    result = DR2SubagentExecutorAdapter(lambda _: executor).execute(task)

    assert result.artifact_metadata["completion_status"] == "complete"
    assert result.artifact_metadata["execution_verification"]["passed"] is True
    assert result.artifact_metadata["execution_verification"]["successful_execution_count"] == 1


def test_coder_complete_claim_is_downgraded_when_execution_failed():
    executor = FakeDR2Executor(
        FakeDR2Result(
            status="completed",
            result=json.dumps(
                {
                    "summary": "Script written and tests passed.",
                    "artifact_content": "raise AssertionError()\n",
                    "artifact_type": "generated_file",
                    "artifact_metadata": {"completion_status": "complete"},
                }
            ),
            task_id="task-failed-code",
            ai_messages=[
                {
                    "type": "ai",
                    "tool_calls": [
                        {
                            "id": "bash-failed",
                            "name": "bash",
                            "args": {"command": "python broken.py"},
                        }
                    ],
                },
                {
                    "type": "tool",
                    "name": "bash",
                    "tool_call_id": "bash-failed",
                    "content": "AssertionError\n\nExit Code: 1",
                },
            ],
        )
    )
    task = _sp_task()
    task.subagent_type = "coder"
    task.task = "Run the Python tests and return the verified file."
    task.expected_output = "A tested Python file."

    result = DR2SubagentExecutorAdapter(lambda _: executor).execute(task)

    assert result.artifact_metadata["completion_status"] == "partial"
    assert result.artifact_metadata["execution_verification"]["passed"] is False
    assert "Coder reported observable behavioral check failures or a regression." in result.artifact_metadata["evidence_gaps"]


def test_coder_reported_regression_is_structured_for_central_recovery():
    executor = FakeDR2Executor(
        FakeDR2Result(
            status="completed",
            result=(
                "I found an issue: the applied fix breaks the existing compatibility test. "
                "The source must be revised before completion."
            ),
            ai_messages=[],
        )
    )
    task = _sp_task()
    task.subagent_type = "coder"
    task.task = "Verify backward compatibility."
    task.metadata = {
        "stage": "verification",
        "verification_only": True,
        "requires_implementation": False,
    }

    result = DR2SubagentExecutorAdapter(lambda _: executor).execute(task)

    assert result.artifact_metadata["completion_status"] == "partial"
    assert "Coder reported observable behavioral check failures or a regression." in result.artifact_metadata["evidence_gaps"]


def test_coder_environment_blocker_and_intermediate_narration_are_not_a_regression():
    executor = FakeDR2Executor(
        FakeDR2Result(
            status="completed",
            result=(
                "Native test collection failed because the host environment dependency is incompatible. "
                "A focused behavioral check passed and the existing patch is ready for canonical grading."
            ),
            ai_messages=[
                {
                    "type": "ai",
                    "content": "The test failed, so I need to inspect whether this is a regression.",
                },
                {
                    "type": "tool",
                    "name": "bash",
                    "tool_call_id": "native-test",
                    "content": "ImportError: incompatible host environment dependency\nExit Code: 1",
                },
                {
                    "type": "tool",
                    "name": "bash",
                    "tool_call_id": "focused-check",
                    "content": "BEHAVIOR_CHECK: PASS\nExit Code: 0",
                },
            ],
        )
    )
    task = _sp_task()
    task.subagent_type = "coder"
    task.metadata = {
        "stage": "verification",
        "verification_only": True,
        "requires_implementation": False,
    }

    result = DR2SubagentExecutorAdapter(lambda _: executor).execute(task)

    assert "Coder reported observable behavioral check failures or a regression." not in result.artifact_metadata["evidence_gaps"]


def test_coder_test_command_with_masked_pipeline_exit_is_not_verification():
    executor = FakeDR2Executor(
        FakeDR2Result(
            status="completed",
            result=json.dumps(
                {
                    "summary": "Tests passed.",
                    "artifact_content": None,
                    "artifact_type": "generated_file",
                    "artifact_metadata": {"completion_status": "complete"},
                }
            ),
            ai_messages=[
                {
                    "type": "ai",
                    "tool_calls": [
                        {
                            "id": "write-source",
                            "name": "write_file",
                            "args": {
                                "path": "/mnt/user-data/workspace/pkg/core.py",
                                "content": "VALUE = 1\n",
                            },
                        },
                        {
                            "id": "masked-test",
                            "name": "bash",
                            "args": {
                                "command": "python -m pytest -q tests/test_core.py 2>&1 | tail -20"
                            },
                        },
                    ],
                },
                {
                    "type": "tool",
                    "name": "write_file",
                    "tool_call_id": "write-source",
                    "content": "OK",
                },
                {
                    "type": "tool",
                    "name": "bash",
                    "tool_call_id": "masked-test",
                    "content": "ModuleNotFoundError: No module named 'pkg'",
                },
            ],
        )
    )
    task = _sp_task()
    task.subagent_type = "coder"
    task.task = "Implement the source fix and run tests."
    task.metadata = {"stage": "implementation", "requires_implementation": True}

    result = DR2SubagentExecutorAdapter(lambda _: executor).execute(task)

    assert result.artifact_metadata["completion_status"] == "partial"
    assert result.artifact_metadata["test_verification"]["passed"] is False
    assert result.artifact_metadata["test_verification"]["masked_test_command_count"] == 1
    assert "Coder test command masked or discarded the test runner exit status." in result.artifact_metadata["evidence_gaps"]


def test_coder_embedded_real_test_exit_is_not_treated_as_success():
    executor = FakeDR2Executor(
        FakeDR2Result(
            status="completed",
            result=json.dumps(
                {
                    "summary": "Tests passed despite the environment warning.",
                    "artifact_content": None,
                    "artifact_type": "generated_file",
                    "artifact_metadata": {"completion_status": "complete"},
                }
            ),
            ai_messages=[
                {
                    "type": "ai",
                    "tool_calls": [
                        {
                            "id": "write-source",
                            "name": "write_file",
                            "args": {
                                "path": "/mnt/user-data/workspace/pkg/core.py",
                                "content": "VALUE = 1\n",
                            },
                        },
                        {
                            "id": "focused-test",
                            "name": "bash",
                            "args": {"command": "python -m pytest -q tests/test_core.py"},
                        },
                    ],
                },
                {
                    "type": "tool",
                    "name": "write_file",
                    "tool_call_id": "write-source",
                    "content": "OK",
                },
                {
                    "type": "tool",
                    "name": "bash",
                    "tool_call_id": "focused-test",
                    "content": "PYTEST_REAL_EXIT=4\npytest could not collect tests",
                },
            ],
        )
    )
    task = _sp_task()
    task.subagent_type = "coder"
    task.task = "Implement the source fix and run tests."
    task.metadata = {"stage": "implementation", "requires_implementation": True}

    result = DR2SubagentExecutorAdapter(lambda _: executor).execute(task)

    assert result.artifact_metadata["completion_status"] == "partial"
    assert result.artifact_metadata["test_verification"]["passed"] is False


def test_coder_explicit_false_check_evidence_outranks_success_claim():
    executor = FakeDR2Executor(
        FakeDR2Result(
            status="completed",
            result=json.dumps(
                {
                    "summary": "All independent checks passed.",
                    "artifact_content": None,
                    "artifact_type": "verification_observation",
                    "artifact_metadata": {"completion_status": "complete"},
                }
            ),
            ai_messages=[
                {
                    "type": "ai",
                    "tool_calls": [
                        {
                            "id": "focused-check",
                            "name": "bash",
                            "args": {"command": "python verify_behavior.py"},
                        }
                    ],
                },
                {
                    "type": "tool",
                    "name": "bash",
                    "tool_call_id": "focused-check",
                    "content": "legacy PASS: False\nCHECK boundary PASS\nExit Code: 0",
                },
            ],
        )
    )
    task = _sp_task()
    task.subagent_type = "coder"
    task.task = "Run an independent behavioral check and verify the fix."
    task.metadata = {
        "stage": "verification",
        "verification_only": True,
        "requires_implementation": False,
    }

    result = DR2SubagentExecutorAdapter(lambda _: executor).execute(task)

    assert result.artifact_metadata["completion_status"] == "partial"
    assert result.artifact_metadata["execution_verification"]["passed"] is False


def test_declared_evidence_gap_cannot_be_complete():
    executor = FakeDR2Executor(
        FakeDR2Result(
            status="completed",
            result=json.dumps(
                {
                    "summary": "Mostly done.",
                    "artifact_content": "evidence",
                    "artifact_type": "research_observation",
                    "artifact_metadata": {
                        "completion_status": "complete",
                        "evidence_gaps": ["One required source is still unavailable."],
                    },
                }
            ),
        )
    )

    result = DR2SubagentExecutorAdapter(lambda _: executor).execute(_sp_task())

    assert result.artifact_metadata["completion_status"] == "partial"


def test_coder_implementation_claim_is_downgraded_when_only_tests_change():
    executor = FakeDR2Executor(
        FakeDR2Result(
            status="completed",
            result=json.dumps(
                {
                    "summary": "Implementation complete.",
                    "artifact_content": None,
                    "artifact_type": "generated_file",
                    "artifact_metadata": {"completion_status": "complete"},
                }
            ),
            task_id="task-test-only",
            ai_messages=[
                {
                    "type": "ai",
                    "tool_calls": [
                        {
                            "id": "write-test",
                            "name": "write_file",
                            "args": {"path": "/mnt/user-data/workspace/tests/test_config.py", "content": "def test_new(): pass\n"},
                        }
                    ],
                },
                {"type": "tool", "name": "write_file", "tool_call_id": "write-test", "content": "OK"},
            ],
        )
    )
    task = _sp_task()
    task.subagent_type = "coder"
    task.task = "Implement the Config.from_file mode parameter and update tests."
    task.expected_output = "Implementation patch and focused test result."
    task.metadata = {"stage": "implementation", "requires_implementation": True}

    result = DR2SubagentExecutorAdapter(lambda _: executor).execute(task)

    assert result.artifact_metadata["completion_status"] == "partial"
    assert result.artifact_metadata["implementation_verification"] == {
        "required": True,
        "passed": False,
        "source_write_count": 0,
        "source_paths": [],
        "test_write_count": 1,
        "test_paths": ["/mnt/user-data/workspace/tests/test_config.py"],
    }
    assert any("no non-test source file was changed" in gap for gap in result.artifact_metadata["evidence_gaps"])


def test_coder_implementation_claim_accepts_non_test_source_change():
    executor = FakeDR2Executor(
        FakeDR2Result(
            status="completed",
            result=json.dumps(
                {
                    "summary": "Implementation complete.",
                    "artifact_content": None,
                    "artifact_type": "generated_file",
                    "artifact_metadata": {"completion_status": "complete"},
                }
            ),
            task_id="task-source-change",
            ai_messages=[
                {
                    "type": "ai",
                    "tool_calls": [
                        {
                            "id": "write-source",
                            "name": "str_replace",
                            "args": {"path": "/mnt/user-data/workspace/src/flask/config.py", "old_string": "def from_file", "new_string": "def from_file"},
                        }
                    ],
                },
                {"type": "tool", "name": "str_replace", "tool_call_id": "write-source", "content": "OK"},
                {
                    "type": "ai",
                    "tool_calls": [
                        {
                            "id": "bash-test",
                            "name": "bash",
                            "args": {"command": "python -m pytest tests/test_config.py"},
                        }
                    ],
                },
                {"type": "tool", "name": "bash", "tool_call_id": "bash-test", "content": "1 passed"},
            ],
        )
    )
    task = _sp_task()
    task.subagent_type = "coder"
    task.task = "Implement the Config.from_file mode parameter."
    task.expected_output = "Implementation patch."
    task.metadata = {"stage": "implementation", "requires_implementation": True}

    result = DR2SubagentExecutorAdapter(lambda _: executor).execute(task)

    assert result.artifact_metadata["completion_status"] == "complete"
    assert result.artifact_metadata["implementation_verification"]["passed"] is True
    assert result.artifact_metadata["implementation_verification"]["source_paths"] == [
        "/mnt/user-data/workspace/src/flask/config.py"
    ]


def test_memory_recaller_keeps_full_recall_json_for_normalization():
    recall_json = """```json
{"summary":"Use phased tests.","items":[{"content":"Test each unit.","memory_id":"mem-1"}]}
```"""
    executor = FakeDR2Executor(FakeDR2Result(status="completed", result=recall_json, task_id="memory-task"))
    task = _sp_task()
    task.subagent_type = "memory_recaller"

    result = DR2SubagentExecutorAdapter(lambda _: executor).execute(task)

    assert result.result == '{"summary":"Use phased tests.","items":[{"content":"Test each unit.","memory_id":"mem-1"}]}'
    assert result.artifact_content is None
