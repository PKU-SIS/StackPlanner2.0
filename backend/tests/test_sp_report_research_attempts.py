"""Host research execution is not erased by a missing gap_attempts declaration."""

from deerflow.sp.report.hooks import _infer_missing_gap_attempts


def test_missing_attempt_with_saved_material_is_retained_not_counted_as_searched():
    attempts = _infer_missing_gap_attempts(
        [],
        gap_ids=["g1"],
        host_executions=[
            {"tool": "report_fetch_material", "ok": True, "detail": '{"saved":true,"material_id":"m1"}'}
        ],
        stop_reason="token_capped",
    )

    assert attempts == [
        {
            "gap_id": "g1",
            "execution": "technical_error",
            "material_ids": ["m1"],
            "note": (
                "Host recorded research tool execution but the result did not declare gap_attempts. "
                "No effective round is charged under the existing definition; saved materials and "
                "host execution records are retained for Outline's evidence judgement."
            ),
        }
    ]


def test_multi_gap_missing_attempt_does_not_attribute_success_to_every_gap():
    attempts = _infer_missing_gap_attempts(
        [],
        gap_ids=["g1", "g2"],
        host_executions=[
            {"tool": "report_fetch_material", "ok": True, "detail": '{"material_id":"m1"}'}
        ],
        stop_reason="token_capped",
    )

    assert [item["execution"] for item in attempts] == ["technical_error", "technical_error"]
    assert [item["material_ids"] for item in attempts] == [[], []]
    assert all("cannot be attributed" in item["note"] for item in attempts)


def test_missing_attempt_without_host_research_is_not_attempted():
    attempts = _infer_missing_gap_attempts(
        [],
        gap_ids=["g1"],
        host_executions=[],
        stop_reason=None,
    )

    assert attempts[0]["execution"] == "not_attempted"


def test_explicit_declared_attempt_is_preserved():
    declared = [
        {"gap_id": "g1", "execution": "searched", "material_ids": ["m1"], "note": "declared"}
    ]

    attempts = _infer_missing_gap_attempts(
        declared,
        gap_ids=["g1", "g2"],
        host_executions=[{"tool": "report_fetch_material", "ok": True, "detail": '{"material_id":"m2"}'}],
        stop_reason=None,
    )

    assert attempts[0] == declared[0]
    assert attempts[1]["execution"] == "technical_error"
    assert attempts[1]["material_ids"] == []
