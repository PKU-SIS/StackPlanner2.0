"""Offline experiment fixtures: schema isolation, version references and frozen SHA."""

import json
import shutil
from pathlib import Path

import pytest
from validate_report_multiturn_cases import freeze_suite, load_public_cases, validate_suite

SUITE = Path(__file__).resolve().parents[2] / "docs/research-report/multiturn-evaluation-20261122"


def copied(tmp_path):
    dest = tmp_path / "suite"
    shutil.copytree(SUITE, dest)
    (dest / "manifest.json").unlink(missing_ok=True)
    return dest


def test_public_loader_has_no_evaluator_state():
    public = load_public_cases(SUITE / "requests.json")
    assert len(public["cases"]) == 2
    assert all(len(case["turns"]) == 7 for case in public["cases"])
    assert "expected_base_turn" not in json.dumps(public)
    assert "active_requirements" not in json.dumps(public)


def test_model_visible_schema_rejects_expected_answers(tmp_path):
    suite = copied(tmp_path)
    path = suite / "requests.json"
    data = json.loads(path.read_text())
    data["cases"][0]["turns"][1]["expected_base_turn"] = 1
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="model-visible"):
        load_public_cases(path)


def test_future_or_unaccepted_version_reference_is_rejected(tmp_path):
    suite = copied(tmp_path)
    path = suite / "evaluator_checks.json"
    data = json.loads(path.read_text())
    data["cases"][0]["turns"][3]["expected_base_turn"] = 5
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="accepted"):
        validate_suite(suite, verify_frozen=False)


def test_frozen_fixtures_reject_silent_edits(tmp_path):
    suite = copied(tmp_path)
    freeze_suite(suite)
    path = suite / "requests.json"
    data = json.loads(path.read_text())
    data["cases"][0]["turns"][1]["query"] += "额外增加一个要求"
    path.write_text(json.dumps(data, ensure_ascii=False))
    with pytest.raises(ValueError, match="checksum"):
        validate_suite(suite)
    with pytest.raises(ValueError, match="frozen"):
        freeze_suite(suite)


def test_freeze_is_idempotent_and_never_authorizes_api(tmp_path):
    suite = copied(tmp_path)
    first = freeze_suite(suite)
    assert freeze_suite(suite) == first
    assert first["api_experiments_authorized"] is False
    checked = validate_suite(suite)
    assert checked["cases"] == 2 and checked["turns"] == 14 and checked["api_calls"] == 0
