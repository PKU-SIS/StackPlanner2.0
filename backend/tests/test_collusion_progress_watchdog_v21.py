"""Explicit longer model waits must fit inside the experiment progress guard."""

import pytest
from test_collusion_audit import scoped_profile as scoped_profile

from app.experiments.collusion_audit.runner import progress_watchdog_limit
from deerflow.sp.subagents import SPSubagentTask
from deerflow.subagents.config import SubagentConfig


def test_default_and_existing_timeouts_preserve_original_watchdog():
    assert progress_watchdog_limit() == 600
    assert progress_watchdog_limit(480, 240) == 600


def test_explicit_longer_requests_get_bounded_cleanup_headroom():
    assert progress_watchdog_limit(960, 480) == 1080
    assert progress_watchdog_limit(240, 1200) == 1320


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, "960"])
def test_invalid_resource_values_rejected(value):
    with pytest.raises(ValueError):
        progress_watchdog_limit(value, None)


@pytest.mark.parametrize("role", ["coder", "researcher", "reporter"])
def test_extended_wait_keeps_same_model_completion_budget_and_native_role(scoped_profile, tmp_path, role):
    p = type(scoped_profile)(
        scoped_profile.rule,
        scoped_profile.project,
        scoped_profile.outputs,
        tmp_path / "cache",
        scoped_profile.trace,
        quality_controls=True,
        rule_level_review=True,
        report_format="html",
        reporter_max_tokens=16384,
        reporter_request_timeout=960,
        evidence_role_capacity=True,
    )
    task = SPSubagentTask("a", role, "核查当前规则", "test")
    p.prepare_task(task)
    configured = p.configure_subagent(task, SubagentConfig("sp-" + role, "native"))
    assert configured.model_max_tokens == 16384
    assert configured.model_timeout_seconds == 960
    assert configured.model_request_deadline_seconds == 960
    assert task.subagent_type == role and task.task == "核查当前规则"
