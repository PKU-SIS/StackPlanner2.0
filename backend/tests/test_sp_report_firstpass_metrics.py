"""Observability and first-pass denominators must not manufacture success."""

import json
from decimal import Decimal

from report_pilot_audit import BudgetGuard, PilotUsageAudit, RateCard


def test_numeric_call_records_and_tool_rejections_are_deduplicated():
    audit = PilotUsageAudit(RateCard())
    audit.start("m1", turn=2, role="writer", model="deepseek-flash")
    audit.end("m1", {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "input_token_details": {"cache_read": 60}}, reported_model="deepseek-flash")
    message = {"id": "t1", "type": "tool", "name": "report_save_node_draft", "tool_call_id": "call1", "content": json.dumps({"saved": False, "error": "invalid_node_choices", "secret_content": "NOT_TO_BE_RECORDED"})}
    audit.observe_message(message, role="writer", turn=2, execution_id="exec", step=1)
    audit.observe_message(message, role="writer", turn=2, execution_id="exec", step=1)
    snapshot = audit.snapshot()
    assert snapshot["model_call_records"]["m1"]["usage"]["total_tokens"] == 120
    assert len(snapshot["tool_events"]) == 1 and snapshot["tool_events"][0]["error_code"] == "invalid_node_choices"
    assert "NOT_TO_BE_RECORDED" not in json.dumps(snapshot)
    assert not audit.abort_event.is_set()  # observation does not become another guard


def test_aggregate_experiment_limits_do_not_raise_cost_ceiling():
    from report_pilot_entry import apply_experiment_limits

    guard = BudgetGuard(max_cost_cny="48", max_input_tokens=15000000, max_output_tokens=1000000)
    apply_experiment_limits(guard, {"max_input_tokens": 50000000, "max_output_tokens": 2000000})
    assert guard.max_input == 50000000 and guard.maximum == Decimal("48")


def test_missing_cache_and_usage_are_not_zero_in_token_report():
    from report_firstpass_batch import token_summary

    report = {
        "usage": {
            "model_call_records": {
                "m": {"turn": 2, "role": "git_agent", "status": "complete", "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "cache_read_tokens": None}},
                "unknown": {"turn": 2, "role": "central", "status": "missing_usage", "usage": None},
            }
        },
        "http_attempts": [],
    }
    stats = token_summary(report)
    assert stats["total_tokens"] is None and stats["observed_total_tokens"] == 120
    assert stats["cache_read_tokens"] is None and stats["cache_missing_calls"] == 1
    assert stats["by_role"]["git_agent"]["total_tokens"] == 120


def test_first_completion_and_clean_completion_are_distinct():
    from report_firstpass_batch import firstpass_summary

    rows = [{"completed": True, "attempted": 6, "succeeded": 6, "tool_errors": 1}, {"completed": False, "attempted": 2, "succeeded": 1, "tool_errors": 2}]
    r = firstpass_summary(rows, planned_per_case=6)
    assert r["conversation_completed"] == 1 and r["conversation_denominator"] == 2
    assert r["clean_conversation_completed"] == 0
    assert r["planned_turns"] == 12 and r["attempted_turns"] == 8 and r["not_attempted_turns"] == 4
    assert r["successful_turns"] == 7
