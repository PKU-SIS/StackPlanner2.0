from __future__ import annotations


def test_swebench_runner_waits_for_post_adapter_verification_acceptance():
    from scripts.run_swebench_sp2 import _is_external_grader_handoff_event

    raw_completed = {
        "type": "task_completed",
        "a2a_stage": "verification",
    }
    partial = {
        **raw_completed,
        "completion_status": "partial",
        "test_verification": {"passed": False},
    }
    accepted = {
        **raw_completed,
        "completion_status": "complete",
        "test_verification": {"passed": True},
    }

    assert _is_external_grader_handoff_event("custom", raw_completed, enabled=True) is False
    assert _is_external_grader_handoff_event("custom", raw_completed, enabled=True, verification_completions=1) is False
    assert _is_external_grader_handoff_event("custom", raw_completed, enabled=True, verification_completions=2) is False
    assert _is_external_grader_handoff_event("custom", raw_completed, enabled=True, verification_completions=3) is True
    assert (
        _is_external_grader_handoff_event(
            "custom",
            raw_completed,
            enabled=True,
            verification_completions=1,
            verification_environment_blocked=True,
        )
        is True
    )
    assert (
        _is_external_grader_handoff_event(
            "custom",
            raw_completed,
            enabled=True,
            verification_completions=1,
            verification_environment_blocked=True,
            verification_behavior_failed=True,
        )
        is False
    )
    assert _is_external_grader_handoff_event("custom", partial, enabled=True) is False
    assert _is_external_grader_handoff_event("custom", accepted, enabled=True) is True
    assert _is_external_grader_handoff_event("values", accepted, enabled=True) is False
    assert _is_external_grader_handoff_event("custom", accepted, enabled=False) is False
    assert (
        _is_external_grader_handoff_event(
            "custom",
            {"type": "task_running", "a2a_stage": "verification"},
            enabled=True,
        )
        is False
    )


def test_swebench_prompt_requires_real_repository_cases_when_pytest_is_blocked():
    from scripts.run_swebench_sp2 import _prompt

    prompt = _prompt(
        {
            "repo": "owner/project",
            "problem_statement": "Fix the boundary behavior.",
            "FAIL_TO_PASS": ["tests/test_core.py::test_boundary"],
            "PASS_TO_PASS": ["tests/test_core.py::test_legacy"],
        }
    )

    assert "actual named test functions" in prompt
    assert "literal" in prompt and "parameterized inputs" in prompt
    assert "Do not substitute" in prompt and "self-chosen examples" in prompt


def test_swebench_prompt_prioritizes_behaviorally_relevant_regressions():
    from scripts.run_swebench_sp2 import _select_prompt_tests

    row = {
        "problem_statement": "Float representation in a FITS Card is too long.",
        "FAIL_TO_PASS": ["tests/test_header.py::test_floating_point_string_representation_card"],
        "PASS_TO_PASS": [
            *(f"tests/test_header.py::test_unrelated_{index}" for index in range(30)),
            "tests/test_header.py::test_invalid_float_cards2",
        ],
    }

    selected = _select_prompt_tests(row, limit=6)

    assert selected[0].endswith("test_floating_point_string_representation_card")
    assert "tests/test_header.py::test_invalid_float_cards2" in selected
    assert len(selected) == 6


def test_swebench_prompt_preserves_immutable_original_contract():
    from scripts.run_swebench_sp2 import _prompt

    prompt = _prompt(
        {
            "repo": "owner/project",
            "problem_statement": "Remove the automatic transform, do not add a warning.",
            "FAIL_TO_PASS": ["tests/test_core.py::test_exact_behavior"],
            "PASS_TO_PASS": ["tests/test_core.py::test_legacy"],
        }
    )

    assert "Immutable benchmark contract" in prompt
    assert "authoritative original requirement" in prompt
    assert "tests/test_core.py::test_exact_behavior" in prompt
    assert "Never manufacture a source edit" in prompt


def test_swebench_handoff_gate_rejects_valid_but_behaviorally_failed_patch():
    from scripts.run_swebench_sp2 import _benchmark_handoff_eligibility

    quality = {"valid_source_patch": True}
    result = _benchmark_handoff_eligibility(quality, {"status": "failed"})

    assert result == {
        "eligible": False,
        "reason": "declared_benchmark_test_failed",
        "verification_status": "failed",
    }


def test_swebench_handoff_gate_allows_external_grader_when_local_env_blocked():
    from scripts.run_swebench_sp2 import _benchmark_handoff_eligibility

    result = _benchmark_handoff_eligibility({"valid_source_patch": True}, {"status": "blocked"})

    assert result["eligible"] is True
    assert result["verification_status"] == "blocked"


def test_swebench_grader_rejects_unverified_result_before_harness():
    from scripts.grade_swebench_sp2_results import _pregrade_rejection

    assert (
        _pregrade_rejection(
            {
                "patch_quality": {"valid_source_patch": False},
                "patch_status": "none",
            }
        )
        == "invalid_source_patch"
    )
    assert (
        _pregrade_rejection(
            {
                "patch_quality": {"valid_source_patch": True},
                "patch_status": "needs_revision",
                "verification_gate": {
                    "eligible": False,
                    "reason": "declared_benchmark_test_failed",
                },
            }
        )
        == "needs_revision"
    )


def test_swebench_grader_allows_blocked_local_verification():
    from scripts.grade_swebench_sp2_results import _pregrade_rejection

    assert (
        _pregrade_rejection(
            {
                "patch_quality": {"valid_source_patch": True},
                "patch_status": "ready",
                "verification_gate": {
                    "eligible": True,
                    "reason": "local_verification_blocked_external_grader_required",
                },
            }
        )
        is None
    )


def test_swebench_runner_distinguishes_stalled_perception_timeout():
    from scripts.run_swebench_sp2 import _diagnose_stop_reason

    diagnostics = _diagnose_stop_reason(
        error="TimeoutError",
        last_state={
            "sp_current_stage": "perception",
            "sp_loop_iteration": 1,
            "sp_current_action_id": None,
        },
        event_count=12,
    )

    assert diagnostics["stop_reason"] == "no_progress_timeout"
    assert diagnostics["last_stage"] == "perception"
    assert diagnostics["last_action_id"] is None
    assert diagnostics["event_count"] == 12


def test_swebench_runner_detects_terminal_provider_fallback():
    from scripts.run_swebench_sp2 import _provider_fallback_error

    error = _provider_fallback_error(
        {
            "messages": [
                {
                    "content": "Provider unavailable.",
                    "additional_kwargs": {
                        "deerflow_error_fallback": True,
                        "error_type": "APIConnectionError",
                        "error_reason": "transient",
                        "error_detail": "Connection error.",
                    },
                }
            ]
        }
    )

    assert error == "LLM provider fallback (APIConnectionError, transient): Connection error."


def test_swebench_runner_preserves_last_custom_event_type():
    from scripts.run_swebench_sp2 import _diagnose_stop_reason

    diagnostics = _diagnose_stop_reason(
        error="No observable graph progress for 120s",
        last_state={"sp_current_stage": "perception"},
        event_count=8,
        last_event_type="task_running",
    )

    assert diagnostics["last_event_type"] == "task_running"


def test_swebench_runner_detects_environment_blocker_from_verification_events():
    from scripts.run_swebench_sp2 import _verification_event_flags

    environment_blocked, behavior_failed = _verification_event_flags(
        "custom",
        {
            "type": "task_running",
            "a2a_stage": "verification",
            "message": {
                "content": "ImportError: extension modules were not built; pytest is unavailable.",
            },
        },
    )

    assert environment_blocked is True
    assert behavior_failed is False

    environment_blocked, behavior_failed = _verification_event_flags(
        "custom",
        {
            "type": "task_completed",
            "a2a_stage": "verification",
            "result": "AssertionError: regression test failed after the patch.",
        },
    )

    assert environment_blocked is False
    assert behavior_failed is True


def test_swebench_runner_normalizes_test_metadata_and_pythonpath(tmp_path):
    from scripts.run_swebench_sp2 import _test_names, _validation_pythonpath

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "requests/packages/urllib3/packages/ssl_match_hostname").mkdir(parents=True)
    (repo / "requests/packages/urllib3/packages/ssl_match_hostname/_implementation.py").write_text("")

    assert _test_names('["tests/test_a.py::test_one"]') == ["tests/test_a.py::test_one"]
    pythonpath = _validation_pythonpath(repo)
    assert str(repo.resolve()) in pythonpath
    assert str((repo / "requests/packages/urllib3/packages/ssl_match_hostname").resolve()) in pythonpath


def test_swebench_runner_normalizes_canonical_50_rows():
    from scripts.run_swebench_sp2 import _normalize_row

    row = _normalize_row(
        {
            "task_id": "extracted-swe_bench_verified-test-owner__project-123",
            "original_id": "owner__project-123",
            "question": "Fix the regression.",
            "fully_specified_question": "Fix the regression with the original wording.",
            "swe_bench_metadata": {
                "repo": "owner/project",
                "base_commit": "abc123",
                "FAIL_TO_PASS": ["tests/test_fix.py::test_regression"],
                "PASS_TO_PASS": ["tests/test_fix.py::test_existing"],
            },
        }
    )

    assert row["instance_id"] == "owner__project-123"
    assert row["repo"] == "owner/project"
    assert row["base_commit"] == "abc123"
    assert row["problem_statement"] == "Fix the regression with the original wording."
    assert row["FAIL_TO_PASS"] == ["tests/test_fix.py::test_regression"]
    assert row["PASS_TO_PASS"] == ["tests/test_fix.py::test_existing"]


def test_swebench_runner_loads_canonical_docker_image_map(tmp_path):
    import json

    from scripts.run_swebench_sp2 import _load_docker_image_map

    manifest = tmp_path / "status.json"
    manifest.write_text(
        json.dumps(
            {
                "images": {
                    "owner__project-123": {
                        "instance_id": "owner__project-123",
                        "canonical_tag": "docker.io/swebench/example:latest",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert _load_docker_image_map(manifest) == {"owner__project-123": "docker.io/swebench/example:latest"}


def test_swebench_runner_marks_missing_test_metadata_not_configured(tmp_path):
    from scripts.run_swebench_sp2 import _run_benchmark_tests

    result = _run_benchmark_tests(tmp_path, {}, timeout=30)

    assert result["status"] == "not_configured"
    assert result["reason"] == "row has no FAIL_TO_PASS or PASS_TO_PASS metadata"
