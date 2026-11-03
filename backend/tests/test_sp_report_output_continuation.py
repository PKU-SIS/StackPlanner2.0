"""Length-truncated research gets one bounded, tool-free continuation."""

from types import SimpleNamespace

from deerflow.sp.report import supplement_delivery
from deerflow.sp.report.research_delivery import prepare_delivery
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus, SPSubagentTask


def _task(supplement=False):
    return SPSubagentTask(
        action_id="a1", subagent_type="researcher", task="research", description="research", metadata={"report_role": "researcher", "report_id": "r1", **({"report_gap_ids": ["g1"]} if supplement else {"report_pre_research": True})}
    )


def _ledger(tmp_path):
    return SimpleNamespace(
        paths=SimpleNamespace(summary_file=lambda _: tmp_path / "summary.md"), payload={"sources": [{"source_id": "S1"}]}, materials=lambda: [{"source_id": "S1", "material_id": "m1", "preview": "Saved evidence", "path": "unused"}]
    )


def test_capped_pre_research_preserves_prefix_and_continues_only_once(tmp_path):
    original = SPSubagentResult(status=SPSubagentStatus.COMPLETED, artifact_content="# Findings\nEvidence [S1]. The final sentence is ", stop_reason="output_capped", token_usage_records=[{"total_tokens": 100}])
    calls = []

    def execute(task):
        calls.append(task)
        assert task.allowed_tools == []
        assert task.metadata["report_output_continuation"]
        assert task.metadata["report_delivery_remaining_tokens"] == 1_999_900
        assert task.metadata["report_delivery_output_limit"] <= 65536
        return SPSubagentResult(status=SPSubagentStatus.COMPLETED, artifact_content="complete [S1].", token_usage_records=[{"total_tokens": 50}])

    result = prepare_delivery(original, _task(), _ledger(tmp_path), SimpleNamespace(execute=execute))
    assert result.artifact_content == original.artifact_content + "complete [S1]."
    assert result.stop_reason is None
    assert result.artifact_metadata.get("research_delivery_issues", []) == []
    assert sum(r["total_tokens"] for r in result.token_usage_records) == 150
    prepare_delivery(original, _task(), _ledger(tmp_path), SimpleNamespace(execute=execute))
    assert len(calls) == 1


def test_capped_continuation_is_rejected_without_another_repair(tmp_path):
    original = SPSubagentResult(status=SPSubagentStatus.COMPLETED, artifact_content="Evidence [S1].", stop_reason="output_capped")
    calls = []

    def execute(task):
        calls.append(task)
        return SPSubagentResult(status=SPSubagentStatus.COMPLETED, artifact_content="Still cut", stop_reason="output_capped")

    result = prepare_delivery(original, _task(), _ledger(tmp_path), SimpleNamespace(execute=execute))
    assert "output_capped" in result.artifact_metadata["research_delivery_issues"]
    assert len(calls) == 1


def test_insufficient_remaining_budget_does_not_call_model(tmp_path):
    task = _task()
    task.metadata["report_research_token_budget"] = {"enabled": True, "max_tokens": 10000}
    original = SPSubagentResult(status=SPSubagentStatus.COMPLETED, artifact_content="Evidence [S1].", stop_reason="output_capped", token_usage_records=[{"total_tokens": 9990}])
    executor = SimpleNamespace(execute=lambda _: (_ for _ in ()).throw(AssertionError("must not call")))
    result = prepare_delivery(original, task, _ledger(tmp_path), executor)
    assert result.artifact_metadata["research_delivery_issues"]
    assert result.artifact_metadata["continuation_skipped_reason"] == "insufficient_remaining_budget"


def test_supplement_continuation_preserves_host_records_and_parses_completed_table(tmp_path):
    original = SPSubagentResult(status=SPSubagentStatus.COMPLETED, artifact_content="Evidence [S1].\n", stop_reason="output_capped", artifact_metadata={"host_tool_execution_records": [{"tool": "web_search", "ok": True}]})
    calls = []

    def execute(task):
        calls.append(task)
        return SPSubagentResult(status=SPSubagentStatus.COMPLETED, artifact_content="\n| gap_id | execution | material_ids |\n|---|---|---|\n| g1 | searched | m1 |")

    result = supplement_delivery.prepare(original, _task(True), _ledger(tmp_path), SimpleNamespace(execute=execute))
    assert result.artifact_metadata["supplement_delivery_issues"] == []
    assert result.artifact_metadata["host_tool_execution_records"] == original.artifact_metadata["host_tool_execution_records"]
    assert result.artifact_metadata["gap_attempts"][0]["gap_id"] == "g1"
    assert len(calls) == 1


def test_effective_hard_ceiling_and_input_budget_are_not_reset(tmp_path):
    task = _task()
    task.metadata["report_research_token_budget"] = {"enabled": True, "max_tokens": 20000, "hard_stop_threshold": 0.5}
    original = SPSubagentResult(status=SPSubagentStatus.COMPLETED, artifact_content="Evidence [S1].", stop_reason="output_capped", token_usage_records=[{"total_tokens": 9990}])
    executor = SimpleNamespace(execute=lambda _: (_ for _ in ()).throw(AssertionError("must not call")))
    result = prepare_delivery(original, task, _ledger(tmp_path), executor)
    assert result.artifact_metadata["continuation_skipped_reason"] == "insufficient_remaining_budget"


def test_report_research_defaults_to_64k_for_normal_and_repair_tasks(tmp_path, monkeypatch):
    from dataclasses import replace

    from deerflow.sp.report import hooks
    from deerflow.subagents.config import SubagentConfig

    monkeypatch.setattr(hooks, "active_report_required", lambda _: SimpleNamespace(report_id="r1", outputs_path=tmp_path))
    base = SubagentConfig(name="sp-researcher", description="research")
    for supplement in (False, True):
        for repair in (False, True):
            task = _task(supplement)
            if repair:
                task.metadata["report_supplement_repair" if supplement else "report_summary_repair"] = True
            config, _, _ = hooks.configure_report_subagent(task, state={}, available_tools=[SimpleNamespace(name="web_fetch")], subagent_config=base)
            assert config.max_output_tokens == 65536
            if repair:
                assert config.tools == [] and config.skills == []
    ordinary, _, role = hooks.configure_report_subagent(replace(_task(), metadata={}), state={}, available_tools=[], subagent_config=base)
    assert ordinary is base and ordinary.max_output_tokens is None and role is None


def test_continuation_prompt_does_not_request_full_body_again():
    from deerflow.sp.subagents.dr2_adapter import render_sp_subagent_prompt

    task = _task()
    task.metadata["report_output_continuation"] = True
    prompt = render_sp_subagent_prompt(task)
    assert "ONLY the missing suffix" in prompt
    assert "Return the complete Markdown research synthesis" not in prompt
