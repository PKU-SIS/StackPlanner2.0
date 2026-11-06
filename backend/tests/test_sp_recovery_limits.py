"""Execution limits are caller-owned, bounded, and consistent across gates."""

import pytest

from deerflow.sp.recovery import normalize_recovery_limits, runtime_recovery_limits


@pytest.mark.parametrize("value", [None, "invalid", True, float("inf")])
def test_invalid_budget_retains_default(value):
    assert normalize_recovery_limits({"reporter": value})["reporter"] == 3


def test_runtime_limits_are_bounded_and_guard_retries_cannot_exceed_ordinary():
    assert runtime_recovery_limits({"sp_report_recovery_max_attempts": 999, "sp_delegate_recovery_max_attempts": 4, "sp_capped_delegate_recovery_max_attempts": 8}) == {"reporter": 32, "delegate": 4, "capped_delegate": 4}
