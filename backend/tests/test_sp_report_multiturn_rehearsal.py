"""Seven consecutive turns through the production graph, without provider calls."""

import os
from pathlib import Path

import pytest
from _report_multiturn_rehearsal import rehearse


@pytest.mark.parametrize("inject_cancel", [False, True], ids=["seven-turn", "cancel-duplicate-retry"])
def test_seven_turn_native_rehearsal(tmp_path, monkeypatch, inject_cancel):
    requested = os.environ.get("SP_MULTITURN_REHEARSAL_DIR")
    root = Path(requested) / ("cancel-duplicate-retry" if inject_cancel else "seven-turn") if requested else tmp_path / "rehearsal"
    result = rehearse(root, monkeypatch, inject_cancel=inject_cancel)
    assert result["successful_turns"] == 7
    assert result["provider_calls"] == 0
    assert result["network_connect_attempts"] == 0
    assert result["accepted_generation_turns"] == [1, 2, 3, 4, 7]
    assert result["facts_evaluated"] is False
    assert result["rounds"][4]["roles"] == ["git_agent"]
    assert result["rounds"][5]["roles"] == ["git_agent"]
    assert result["rounds"][6]["base_user_turn"] == 2
    assert all(row["old_version_sha_unchanged"] for row in result["rounds"])
    assert all(row["native_length_ok"] for row in result["rounds"] if row["native_delivered"] and row["user_turn"] == 1)
    assert all(row["native_length_ok"] is None for row in result["rounds"] if row["native_delivered"] and row["user_turn"] > 1)
    assert all(row["restorable"] for row in result["rounds"])
    if inject_cancel:
        assert result["cancelled_attempt_preserved"]
        assert result["duplicate_did_not_fork_or_write"]
        assert result["retry_used_new_source_message"]
    assert (root / "result.json").is_file()


def test_bound_namespace_survives_native_acceptance_cycle(tmp_path):
    from test_sp_report_turns import accepted, followup

    from deerflow.sp.report import acceptance, hooks
    from deerflow.sp.report.ledger import ReportLedger
    from deerflow.sp.report.turns import adapter

    ctx, store = accepted(tmp_path)
    followup(ctx)
    _, message, head = adapter.session(ctx.state, thread_id="t1")
    turn = store.prepare(head["turn_id"], message_id="m2", raw_query=message.content, resolved_query="Complete requirements")
    adapter.activate(store, turn, ctx)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    original = acceptance.acceptance_state(ledger)
    namespace = original["turn_namespace"]
    acceptance._start_new_cycle(ledger, original)
    current = acceptance.acceptance_state(ledger)
    assert current["turn_namespace"] == namespace
    assert current["cycle_id"] == original["cycle_id"] + 1
    assert acceptance._base_action_id(current, "review") != acceptance._base_action_id(original, "review")
    assert turn["turn_id"] in acceptance._base_action_id(current, "review")
    assert acceptance._base_action_id({"cycle_id": 0, "review_round": 1}, "review") == "sp-fixed-c0-review-r1"
