"""Host research execution is not erased by a missing gap_attempts declaration."""

from deerflow.sp.report.hooks import _infer_missing_gap_attempts


class _LedgerWithMaterials:
    def __init__(self, materials=None):
        self._materials = materials or []

    def materials(self):
        return self._materials


def test_missing_attempt_with_saved_material_is_retained_not_counted_as_searched():
    ledger = _LedgerWithMaterials(
        [{"material_id": "m1", "action_id": "a-r1", "gap_ids": ["g1"]}]
    )
    attempts = _infer_missing_gap_attempts(
        [],
        gap_ids=["g1"],
        host_executions=[
            {"tool": "report_fetch_material", "ok": True, "detail": "saved m1"}
        ],
        ledger=ledger,
        action_id="a-r1",
        stop_reason="token_capped",
    )

    assert len(attempts) == 1
    assert attempts[0]["gap_id"] == "g1"
    assert attempts[0]["execution"] == "technical_error"
    assert attempts[0]["material_ids"] == ["m1"]
    assert "No effective round" in attempts[0]["note"]
    assert "m1" in attempts[0]["note"]


def test_multi_gap_missing_attempt_does_not_attribute_success_to_every_gap():
    ledger = _LedgerWithMaterials(
        [{"material_id": "m1", "action_id": "a-r1", "gap_ids": ["g1"]}]
    )
    attempts = _infer_missing_gap_attempts(
        [],
        gap_ids=["g1", "g2"],
        host_executions=[
            {"tool": "report_fetch_material", "ok": True, "detail": "saved m1"}
        ],
        ledger=ledger,
        action_id="a-r1",
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
        ledger=_LedgerWithMaterials(),
        action_id="a-r1",
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
        host_executions=[{"tool": "report_fetch_material", "ok": True, "detail": "saved m2"}],
        ledger=_LedgerWithMaterials(),
        action_id="a-r1",
        stop_reason=None,
    )

    assert attempts[0] == declared[0]
    assert attempts[1]["execution"] == "technical_error"
    assert attempts[1]["material_ids"] == []
