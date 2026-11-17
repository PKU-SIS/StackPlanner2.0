"""Only exact, intact source/code facts can be reused, never model judgements."""

import hashlib
import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def vendor(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "vendor/bid_audit_tools"))


def test_exact_cache_hit_input_invalidation_and_integrity(tmp_path, monkeypatch):
    import bid_audit.runner

    from app.experiments.collusion_audit.cache import calculate_cached

    project = tmp_path / "project"
    project.mkdir()
    source = project / "original.txt"
    source.write_text("Original evidence only")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    (project / "manifest.json").write_text(json.dumps({"schema_version": "1.0", "project_id": "synthetic", "documents": [{"id": "a", "path": "original.txt", "role": "bid", "bidder": "A", "expected_sha256": sha}]}))
    calls = []

    def calculate(rule, manifest, output_root, **kwargs):
        calls.append(rule)
        run = rule + "_20260101T000000_0123456789"
        folder = output_root / run
        folder.mkdir(parents=True)
        (folder / "findings.jsonl").write_text('{"raw_source_fact":true}\n')
        return {"rule_id": rule, "run_id": run, "status": "partial", "requires_agent_review": True, "risk_verdict": None}

    monkeypatch.setattr(bid_audit.runner, "run_rule", calculate)
    cache = tmp_path / "shared"
    first = calculate_cached("M03", project, tmp_path / "out1", tmp_path / "parse", cache, parsed_texts={"a": "original OCR"})
    second = calculate_cached("M03", project, tmp_path / "out2", tmp_path / "parse", cache, parsed_texts={"a": "original OCR"})
    assert calls == ["M03"] and not first["cache"]["hit"] and second["cache"]["hit"]
    assert second["cache"]["model_analysis_reused"] is False and second["risk_verdict"] is None
    assert (tmp_path / "out2/bid_audit" / second["run_id"] / "findings.jsonl").read_text() == '{"raw_source_fact":true}\n'
    changed = calculate_cached("M03", project, tmp_path / "out3", tmp_path / "parse", cache, parsed_texts={"a": "different OCR"})
    assert calls == ["M03", "M03"] and changed["cache"]["key"] != first["cache"]["key"]
    (cache / first["cache"]["key"] / "data/findings.jsonl").write_text("tampered")
    with pytest.raises(ValueError, match="cache changed"):
        calculate_cached("M03", project, tmp_path / "out4", tmp_path / "parse", cache, parsed_texts={"a": "original OCR"})
    source.write_text("Changed original")
    with pytest.raises(ValueError, match="Source changed"):
        calculate_cached("M03", project, tmp_path / "out5", tmp_path / "parse", cache, parsed_texts={"a": "original OCR"})
