"""Supplementary Markdown must stay separate from pre-research and ordinary agents."""

from dataclasses import replace
from types import SimpleNamespace

from deerflow.sp.report import supplement_delivery as delivery
from deerflow.sp.report.research_delivery import is_markdown_research
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus, SPSubagentTask
from deerflow.sp.subagents.dr2_adapter import normalize_dr2_subagent_result, render_sp_subagent_prompt


def task():
    return SPSubagentTask(action_id="a1", subagent_type="researcher", task="research", description="research", metadata={"report_role": "researcher", "report_id": "r1", "report_gap_ids": ["g1"]})


def body():
    return '# Evidence\nUseful "quoted" finding [S1].\n\n## Gap execution\n| gap_id | execution | material_ids |\n|---|---|---|\n| g1 | searched | m1 |'


def test_scope_does_not_change_preresearch_or_ordinary_agents():
    assert delivery.is_supplement(task())
    assert not is_markdown_research(task())
    assert not delivery.is_supplement(replace(task(), metadata={}))
    pre = replace(task(), metadata={**task().metadata, "report_pre_research": True})
    assert not delivery.is_supplement(pre)
    assert is_markdown_research(pre)
    assert "compact JSON" not in render_sp_subagent_prompt(task())


def test_long_markdown_preserves_body_and_gap_declarations():
    text = body() + ('\nDetails with "quotes" [S1].' * 2000)
    raw = SimpleNamespace(status="completed", result=text, stop_reason=None, error=None, ai_messages=[])
    result = normalize_dr2_subagent_result(raw, task=task())
    assert result.artifact_content == text
    assert result.artifact_metadata["gap_attempts"][0]["material_ids"] == ["m1"]
    assert "pre_research_markdown" not in result.artifact_metadata
    assert len(result.result) <= 700


def test_bad_or_missing_gap_table_never_claims_searched():
    assert delivery.parse_attempts("No table.", task())[0] == []
    attempts, issues = delivery.parse_attempts(body().replace("| g1 | searched | m1 |", "| g2 | searched | m1 |"), task())
    assert attempts == [] and issues
    attempts, issues = delivery.parse_attempts(body().replace("| g1 | searched | m1 |", "| g1 | searched | m1 |\n| g1 | searched | m1 |"), task())
    assert issues


class Ledger:
    payload = {"sources": [{"source_id": "S1"}]}

    def materials(self):
        return [{"source_id": "S1", "material_id": "m1", "preview": "Useful finding", "path": "unused"}]


def test_one_tool_free_repair_preserves_execution_metadata_and_usage(tmp_path):
    ledger = Ledger()
    ledger.paths = SimpleNamespace(summary_file=lambda _: tmp_path / "summary.md")
    original = SPSubagentResult(
        status=SPSubagentStatus.COMPLETED, artifact_content=body().replace("[S1]", "S1/m1"), artifact_metadata={"host_tool_execution_records": [{"tool": "web_search", "ok": True}]}, token_usage_records=[{"total_tokens": 10}]
    )
    calls = []

    class Executor:
        def execute(self, repair):
            calls.append(repair)
            assert repair.allowed_tools == []
            assert repair.metadata["report_supplement_repair"]
            assert not repair.metadata.get("report_summary_repair")
            return delivery.normalize(SimpleNamespace(status="completed", result=body(), stop_reason=None, error=None, ai_messages=[], token_usage_records=[{"total_tokens": 20}]), repair)

    result = delivery.prepare(original, task(), ledger, Executor())
    assert len(calls) == 1
    assert result.artifact_metadata["supplement_delivery_issues"] == []
    assert result.artifact_metadata["host_tool_execution_records"] == original.artifact_metadata["host_tool_execution_records"]
    assert [x["total_tokens"] for x in result.token_usage_records] == [10, 20]
    assert result.artifact_metadata["gap_attempts"][0]["gap_id"] == "g1"
    assert len(calls) == 1
    delivery.prepare(original, task(), ledger, Executor())
    assert len(calls) == 1


def test_truncation_and_caps_cannot_be_accepted_as_summaries(tmp_path):
    ledger = Ledger()
    ledger.paths = SimpleNamespace(summary_file=lambda _: tmp_path / "summary.md")
    capped = SPSubagentResult(status=SPSubagentStatus.COMPLETED, artifact_content=body(), stop_reason="loop_capped")
    executor = SimpleNamespace(execute=lambda _: (_ for _ in ()).throw(AssertionError("must not repair cap")))
    assert "loop_capped" in delivery.prepare(capped, task(), ledger, executor).artifact_metadata["supplement_delivery_issues"]
    assert delivery.issues("{bad JSON <raw-result-truncated; head and tail preserved>", task(), {"S1"})


def test_zero_result_without_saved_sources_does_not_require_invented_citations():
    assert delivery.content_issues("No usable documents were retrieved; no factual conclusion is asserted.", set()) == []
    assert "missing_citations" in delivery.content_issues("Useful facts without citations.", {"S1"})
    assert "raw_json_envelope" in delivery.content_issues('{"broken":', set())


def test_absent_gap_table_uses_existing_host_fallback_without_claiming_execution():
    attempts, problems = delivery.parse_attempts("No usable new documents were retrieved.", task())
    assert attempts == []
    assert problems == []


def test_format_repair_cannot_change_historical_gap_execution(tmp_path):
    ledger = Ledger()
    ledger.paths = SimpleNamespace(summary_file=lambda _: tmp_path / "summary.md")
    raw = SimpleNamespace(status="completed", result=body().replace("[S1]", "S1/m1"), stop_reason=None, error=None, ai_messages=[])
    original = delivery.normalize(raw, task())
    changed = body().replace("| g1 | searched | m1 |", "| g1 | not_attempted | - |")
    executor = SimpleNamespace(execute=lambda repair: delivery.normalize(SimpleNamespace(status="completed", result=changed, stop_reason=None, error=None, ai_messages=[]), repair))
    result = delivery.prepare(original, task(), ledger, executor)
    assert "changed_gap_execution_during_repair" in result.artifact_metadata["supplement_delivery_issues"]
    assert result.artifact_metadata["gap_attempts"][0]["execution"] == "searched"


def test_provider_output_cap_is_not_a_complete_supplement():
    raw = SimpleNamespace(status="completed", result=body(), stop_reason=None, error=None, ai_messages=[{"type": "ai", "response_metadata": {"finish_reason": "length"}}])
    result = delivery.normalize(raw, task())
    assert result.stop_reason == "output_capped"
    assert result.completion_status == "partial"
