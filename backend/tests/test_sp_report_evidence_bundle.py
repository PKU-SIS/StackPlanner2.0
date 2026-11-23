"""Read one section and bounded verified evidence without metadata-reading loops."""

import json

import pytest
from test_sp_report_p3 import _finish, _seed, _send_review
from test_sp_report_turns import followup

from deerflow.sp.report import store as native
from deerflow.sp.report.ledger import ReportValidationError
from deerflow.sp.report.turns import adapter
from deerflow.sp.report.turns.store import file_manifest
from deerflow.sp.report.turns.tools import build_tools


def test_evidence_bundle_is_saved_scoped_and_source_identified(tmp_path):
    ctx, ledger = _seed(tmp_path, body="Existing report claim [S1].\n")
    text = "Original saved source line.\nSecond line.\n"
    path = ledger.paths.material_file("S1", native.sha256_text(text))
    native.write_text_atomic(path, text)
    ledger.payload["sources"] = [{"source_id": "S1", "title": "Saved source", "url": "https://example.com/source"}]
    ledger.payload["materials"] = [{"material_id": "m1", "source_id": "S1", "path": str(path), "virtual_path": ledger.paths.virtual(path), "sha256": native.sha256_text(text), "chars": len(text), "lines": 2}]
    ledger.save()
    assert not _send_review(ctx, status="ready", outcome="deliver").error
    assert not _finish(ctx).error
    followup(ctx, text="Read only: explain this section and evidence")
    store, message, head = adapter.session(ctx.state, thread_id="t1")
    before = file_manifest(tmp_path / "report/r1")
    inspect = build_tools(store, message, operation="read_only")[0]
    result = json.loads(inspect.invoke({"version_id": head["turn_id"], "view": "evidence:n2"}))
    assert result["node_id"] == "n2" and "[S1]" in result["content"]
    source = result["evidence_sources"][0]
    assert source["source_id"] == "S1" and source["material_id"] == "m1"
    assert source["material_sha256"] == native.sha256_text(text)
    assert source["content_excerpt"] == text and not source["truncated"]
    assert source["char_range"] == [1, len(text)]
    assert file_manifest(tmp_path / "report/r1") == before
    with pytest.raises(ReportValidationError):
        inspect.invoke({"version_id": head["turn_id"], "view": "evidence:../../ledger.json"})
