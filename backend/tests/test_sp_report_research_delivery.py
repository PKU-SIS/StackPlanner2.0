"""Pre-research delivers complete Markdown and never silently repairs by truncation."""

import json
from dataclasses import replace
from types import SimpleNamespace

from deerflow.sp.report.research_delivery import delivery_issues, is_markdown_research, normalize_markdown_research, prepare_delivery
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus, SPSubagentTask
from deerflow.sp.subagents.dr2_adapter import normalize_dr2_subagent_result, render_sp_subagent_prompt


def task():
    return SPSubagentTask(action_id="a1", subagent_type="researcher", task="research", description="research", metadata={"report_role": "researcher", "report_id": "r1", "report_pre_research": True})


def test_long_markdown_with_quotes_is_preserved_without_the_raw_result_limit():
    body = '# Evidence\n\n中文 “引号” and "English quotes" [S1].\n' * 2000
    raw = SimpleNamespace(status="completed", result=body, result_source="final_ai_message", ai_messages=[], stop_reason=None, error=None)
    result = normalize_dr2_subagent_result(raw, task=task())
    assert result.artifact_content == body
    assert "raw_result_truncated" not in result.artifact_metadata
    assert len(result.result) <= 700
    assert "Return the complete Markdown" in render_sp_subagent_prompt(task())
    assert "return compact JSON" not in render_sp_subagent_prompt(task())


def test_scope_excludes_ordinary_and_supplementary_research():
    assert is_markdown_research(task())
    assert not is_markdown_research(replace(task(), metadata={}))
    assert not is_markdown_research(replace(task(), metadata={"report_role": "researcher", "report_id": "r1", "report_gap_ids": ["g1"]}))


def test_legacy_valid_envelope_can_be_unwrapped_without_guessing_invalid_json():
    body = '# Evidence\n"quoted" [S1].'
    assert normalize_markdown_research(SimpleNamespace(status="completed", result=json.dumps({"artifact_content": body})), task()).artifact_content == body
    invalid = '{"artifact_content":"text with "bad quotes" [S1]"}'
    result = normalize_markdown_research(SimpleNamespace(status="completed", result=invalid), task())
    assert result.artifact_content == invalid
    assert "raw_json_envelope" in delivery_issues(result.artifact_content, {"S1"})


def test_checks_real_citations_without_a_production_source_count_quota():
    assert delivery_issues("# Note\nA supported observation [S1].", {"S1"}) == []
    assert "missing_citations" in delivery_issues("# Note\nNo source.", {"S1"})
    assert "unknown_sources:S120" in delivery_issues("# Note\nFinding [S120].", {"S119"})
    assert "truncated_raw_result" in delivery_issues("# Note [S1]\n...<raw-result-truncated; head and tail preserved>...", {"S1"})


class Ledger:
    payload = {"sources": [{"source_id": "S1"}]}

    def materials(self):
        return [{"source_id": "S1", "material_id": "m1", "preview": "A supported observation", "path": "unused"}]


def test_only_one_tool_free_repair_and_usage_is_retained(tmp_path):
    ledger = Ledger()
    ledger.paths = SimpleNamespace(summary_file=lambda _: tmp_path / "summary.md")
    calls = []
    original = SPSubagentResult(status=SPSubagentStatus.COMPLETED, artifact_content="# Note\nFinding [S99].", token_usage_records=[{"total_tokens": 10}])

    class Executor:
        def execute(self, repair_task):
            calls.append(repair_task)
            return SPSubagentResult(status=SPSubagentStatus.COMPLETED, artifact_content="# Note\nFinding [S1].", token_usage_records=[{"total_tokens": 20}])

    result = prepare_delivery(original, task(), ledger, Executor())
    assert len(calls) == 1
    assert calls[0].allowed_tools == []
    assert calls[0].metadata["report_summary_repair"] is True
    assert result.artifact_content.endswith("[S1].")
    assert result.token_usage_records == [{"total_tokens": 10}, {"total_tokens": 20}]
    assert (tmp_path / "diagnostics/original.md").read_text() == original.artifact_content


def test_failed_repair_is_not_repeated_and_original_is_kept(tmp_path):
    ledger = Ledger()
    ledger.paths = SimpleNamespace(summary_file=lambda _: tmp_path / "summary.md")
    calls = []
    result = SPSubagentResult(status=SPSubagentStatus.COMPLETED, artifact_content="# Note without citations")

    class Executor:
        def execute(self, repair_task):
            calls.append(repair_task)
            return SPSubagentResult(status=SPSubagentStatus.COMPLETED, artifact_content="# Still no citations")

    repaired = prepare_delivery(result, task(), ledger, Executor())
    prepare_delivery(result, task(), ledger, Executor())
    assert len(calls) == 1
    assert "missing_citations" in repaired.artifact_metadata["research_delivery_issues"]
    assert (tmp_path / "diagnostics/original.md").exists()


def test_provider_length_stop_is_not_misreported_as_complete_markdown():
    raw = SimpleNamespace(status="completed", result="# Partial [S1]", ai_messages=[{"type": "ai", "response_metadata": {"finish_reason": "length"}}])
    result = normalize_markdown_research(raw, task())
    assert result.stop_reason == "output_capped"
    assert result.completion_status == "partial"


def test_capped_research_does_not_start_a_repair(tmp_path):
    ledger = Ledger()
    ledger.paths = SimpleNamespace(summary_file=lambda _: tmp_path / "summary.md")
    result = SPSubagentResult(status=SPSubagentStatus.COMPLETED, artifact_content="# Note [S1]", stop_reason="loop_capped")
    executor = SimpleNamespace(execute=lambda _: (_ for _ in ()).throw(AssertionError("must not repair")))
    assert prepare_delivery(result, task(), ledger, executor).artifact_metadata["research_delivery_issues"]
