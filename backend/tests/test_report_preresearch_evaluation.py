"""Evaluation must distinguish an accepted file from a usable research handoff."""

import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("preresearch_evaluation", Path(__file__).resolve().parents[1] / "scripts/evaluate_report_preresearch.py")
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def test_citation_breadth_requires_saved_sources_used_in_prose():
    ledger = {"sources": [{"source_id": f"S{i}"} for i in range(1, 7)]}
    body = "Useful findings. " * 50 + " ".join(f"[S{i}]" for i in range(1, 6))
    assert evaluation.assess(body, ledger)["rich_citations"]
    assert not evaluation.assess(body.replace("[S5]", "[S99]"), ledger)["rich_citations"]
    assert not evaluation.assess("Useful findings. " * 50 + "\n## References\n" + " ".join(f"[S{i}]" for i in range(1, 6)), ledger)["rich_citations"]
    assert not evaluation.assess("Useful findings. " * 50 + "\n## 9. 关键材料索引（来源）\n" + " ".join(f"[S{i}]" for i in range(1, 6)), ledger)["rich_citations"]
    assert not evaluation.assess("Useful findings. " * 50 + "\nUnread leads: " + " ".join(f"[S{i}]" for i in range(1, 6)), ledger)["rich_citations"]


def test_recovered_or_interrupted_output_cannot_pass_as_a_summary():
    text = "Detailed findings. " * 100
    assert evaluation.assess(text, {})["summary_ok"]
    assert not evaluation.assess("[FORCED STOP]" + text, {})["summary_ok"]
    assert not evaluation.assess(text, {}, stop_reason="loop_capped")["summary_ok"]
    assert not evaluation.assess(text, {}, error="provider failed")["no_error"]
    assert not evaluation.assess('{"artifact_content": "' + text + '"}', {})["summary_ok"]
    assert not evaluation.assess(text + "...<raw-result-truncated; head and tail preserved>...", {})["summary_ok"]


def test_fixed_twenty_samples_include_three_smoke_queries():
    assert len(evaluation.SAMPLE_IDS) == len(set(evaluation.SAMPLE_IDS)) == 20
    assert set(evaluation.SMOKE_IDS) <= set(evaluation.SAMPLE_IDS)


def test_offline_audit_keeps_the_real_failed_output_and_original_metrics(tmp_path):
    sample = tmp_path / "60"
    sample.mkdir()
    original = '{"artifact_content": "' + 'Findings with an unescaped "quotation". ' * 30 + '"}'
    (sample / "summary.md").write_text(original)
    (sample / "ledger.json").write_text('{"sources": []}')
    (sample / "metrics.json").write_text(json.dumps({"id": 60, "summary_ok": True, "no_error": True, "rich_citations": True, "error": None, "stop_reason": None}))
    results = evaluation.audit_batch(tmp_path)
    assert not results[0]["summary_ok"]
    assert results[0]["summary_format_issue"] == "raw_json_envelope"
    assert (sample / "summary.md").read_text() == original
    assert json.loads((sample / "metrics.raw.json").read_text())["summary_ok"]
