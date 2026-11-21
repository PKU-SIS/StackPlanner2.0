import pytest
from test_sp_report_acceptance_state import _host_review, _review_result
from test_sp_report_length import _small_contract
from test_sp_report_p3 import _seed

from deerflow.sp.artifacts import SPArtifactAdapter
from deerflow.sp.report import hooks
from deerflow.sp.report.acceptance import _revise_arguments, next_acceptance_action
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError
from deerflow.sp.report.length import normalize_contract


def test_length_failure_stops_without_compression_or_delivery(tmp_path):
    ctx, ledger = _seed(tmp_path, body="证" * 200)
    ledger.payload["length_contract"] = _small_contract(length_auto_revision=False)
    ledger.save()
    action, task, entry = _host_review(ctx)
    result = hooks.handle_success(action, ctx, _review_result(task, status="ready", outcome="deliver"), task, artifact_adapter=SPArtifactAdapter(), delegate_entry=entry)
    assert result.error is None
    msg = next_acceptance_action(ctx.state)
    assert msg.tool_calls[0]["name"] == "sp_ask_human"
    ledger = ReportLedger.load(ledger.paths)
    assert not ledger.payload["acceptance"]["content_revision_used"]
    assert ledger.payload["final_report"] is None


def test_native_content_revision_excludes_host_length_finding_when_disabled():
    length = {"kind": "other", "severity": "blocking", "message": "host length constraint"}
    content = {"kind": "coverage", "severity": "blocking", "message": "Missing required analysis"}
    review = {"length_auto_revision": False, "host_length_finding": length, "findings": [length, content]}
    task = _revise_arguments(review, {}, "a")["task"]
    assert "Missing required analysis" in task and "host length constraint" not in task


@pytest.mark.parametrize("value", [1, "false", None])
def test_flag_requires_boolean(value):
    with pytest.raises(ReportValidationError, match="length_auto_revision"):
        normalize_contract({"length_auto_revision": value})
