"""A recovered bad tool invocation must not be reported as error-free."""

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("stage_metrics", Path(__file__).resolve().parents[1] / "scripts/report_stage_metrics.py")
metrics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metrics)


def test_detects_file_tool_errors_even_when_the_report_ledger_has_none():
    steps = [
        {"type": "ai", "tool_calls": [{"id": "g", "name": "grep"}]},
        {"type": "tool", "tool_call_id": "g", "content": "Error: Path is not a directory: file.txt"},
        {"type": "tool", "tool_call_id": "r", "name": "read_file", "status": "error", "content": "invalid start_line"},
    ]
    errors = metrics.collect_tool_errors(steps)
    assert [item["tool"] for item in errors] == ["grep", "read_file"]


def test_provider_errors_are_errors_but_missing_candidate_bodies_are_not():
    steps = [
        {"type": "tool", "name": "web_search", "content": '{"error": "DEEPSEEK_REQUEST_FAILED", "status_code": 429}'},
        {"type": "tool", "name": "web_search", "content": '{"results": [{"saved": false, "error": "no_body"}]}'},
        {"type": "ai", "content": "Error: quoted in explanatory prose"},
    ]
    assert len(metrics.collect_tool_errors(steps)) == 1


def test_a_saved_raw_envelope_is_not_a_markdown_synthesis():
    malformed = '{"artifact_content": "A long text containing "unescaped" quotes"}'
    assert metrics.summary_format_issue(malformed) == "raw_json_envelope"
    assert metrics.summary_format_issue("```json\n{}\n```") == "raw_json_envelope"
    assert metrics.summary_format_issue("[FORCED STOP] some partial evidence") == "recovered_or_forced_output"
    assert metrics.summary_format_issue("# Notes\n...<raw-result-truncated; head and tail preserved>...") == "truncated_raw_result"
    assert metrics.summary_format_issue("# Research synthesis\nA supported observation [S1].") is None
