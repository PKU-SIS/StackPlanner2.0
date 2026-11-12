"""Behavioral regression: frequent public records cannot hide another source kind."""

import json

from app.experiments.collusion_audit.general_delivery import materialize, priority


def test_large_preview_preserves_distinct_evidence_kinds_and_full_source_bodies(tmp_path):
    frequent = [{"kind": "shared_error_hypothesis", "text_a": "long repeated source " + str(i), "text_b": "other long source " + str(i), "evidence_refs": []} for i in range(25)]
    rare = {"kind": "single_side_edit_artifact_for_review", "text_a": "full private installation wording", "text_b": "complete independent counterpart", "evidence_refs": []}
    rows = frequent + [rare, {"kind": "shared_edit_candidate_not_confirmed_error", "requirement": "original requirement", "comparisons": [], "evidence_refs": []}]
    result = materialize(tmp_path, "/current", {"summary": {"run_id": "M07_synthetic"}}, rows, [])
    assert result["candidate_count"] == 27 and result["preview_count"] == 6
    kinds = {r["candidate"]["kind"] for r in result["candidate_previews"]}
    assert kinds == {r["kind"] for r in rows}
    restored = json.loads((tmp_path / "candidates/C000026.json").read_text())
    assert restored["candidate"] == rare
    assert next(r["candidate"] for r in result["candidate_previews"] if r["candidate"]["kind"] == rare["kind"]) == rare


def test_record_field_labels_only_change_navigation_priority_not_source_verdict(tmp_path):
    ordinary = {"kind": "shared_error_hypothesis", "non_template_shared_chars": 600, "text_a": "许可机关 数据来源单位 统一社会信用代码 行政许可决定文书号"}
    independent = {"kind": "shared_error_hypothesis", "non_template_shared_chars": 600, "text_a": "private implementation sequence"}
    assert priority(ordinary) < priority(independent)
    result = materialize(tmp_path, "/current", {"summary": {"run_id": "x"}}, [ordinary, independent], [])
    assert result["preview_count"] == 2
    assert json.loads((tmp_path / "candidates/C000001.json").read_text())["candidate"] == ordinary
    assert "risk_verdict" not in ordinary


def test_single_kind_keeps_rank_order_and_small_sets_remain_complete(tmp_path):
    rows = [{"kind": "one_kind", "occurrence_count": i, "text_a": "body " + str(i)} for i in range(30)]
    result = materialize(tmp_path, "/current", {"summary": {"run_id": "x"}}, rows, [])
    assert [r["finding_offset"] for r in result["candidate_previews"]] == [29, 28, 27, 26, 25, 24]
    (tmp_path / "small").mkdir()
    small = materialize(tmp_path / "small", "/small", {"summary": {"run_id": "s"}}, rows[:12], [])
    assert small["preview_count"] == 12
