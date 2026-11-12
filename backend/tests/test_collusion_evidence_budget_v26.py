"""Explicit evidence-role budget shares trusted capacity without routing work."""

import pytest
from test_collusion_audit import scoped_profile as scoped_profile

from deerflow.sp.subagents import SPSubagentTask
from deerflow.subagents.config import SubagentConfig


@pytest.mark.parametrize("role,expected", [("coder", 16384), ("researcher", 16384), ("reporter", 16384), ("perception", None), ("memory_recaller", None)])
def test_host_opt_in_expands_only_two_evidence_roles(scoped_profile, tmp_path, role, expected):
    p = type(scoped_profile)(
        scoped_profile.rule, scoped_profile.project, scoped_profile.outputs, tmp_path / "expanded-cache", scoped_profile.trace, quality_controls=True, reporter_max_tokens=16384, reporter_request_timeout=480, evidence_role_capacity=True
    )
    task = SPSubagentTask("a", role, "Inspect supplied source", "bounded source check", metadata={"model_max_tokens": 99999})
    p.prepare_task(task)
    original = SubagentConfig("sp-" + role, "native prompt", skills=["existing-skill"])
    configured = p.configure_subagent(task, original)
    assert configured.model_max_tokens == expected
    assert configured.model_timeout_seconds == (480 if expected else None)
    assert configured.model_request_deadline_seconds == (480 if expected else None)
    assert original.model_max_tokens is None
    assert task.subagent_type == role and task.task == "Inspect supplied source"
    assert configured.skills == original.skills


@pytest.mark.parametrize("value", [1, "true", None])
def test_expansion_flag_rejects_untyped_values(scoped_profile, tmp_path, value):
    with pytest.raises(ValueError, match="explicit boolean"):
        type(scoped_profile)(
            scoped_profile.rule, scoped_profile.project, scoped_profile.outputs, tmp_path / "invalid-cache", scoped_profile.trace, quality_controls=True, reporter_max_tokens=16384, reporter_request_timeout=480, evidence_role_capacity=value
        )


def test_action_metadata_cannot_enable_expansion(scoped_profile):
    scoped_profile.quality_controls = True
    scoped_profile.reporter_max_tokens = 16384
    scoped_profile.reporter_request_timeout = 480
    task = SPSubagentTask("a", "coder", "Inspect source", "source check", metadata={"evidence_role_capacity": True, "model_max_tokens": 99999})
    scoped_profile.prepare_task(task)
    configured = scoped_profile.configure_subagent(task, SubagentConfig("sp-coder", "native"))
    assert configured.model_max_tokens is None
