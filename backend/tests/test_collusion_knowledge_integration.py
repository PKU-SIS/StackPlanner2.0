"""OCR sidecars, judgement persistence and default-off native hooks."""

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from pypdf import PdfWriter

from app.experiments.collusion_audit.profile import CollusionProfile
from app.experiments.tender_audit.observability import AuditTrace


def test_ocr_rows_are_review_units_and_stage_metadata_can_commit(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "vendor/bid_audit_tools"))
    project = tmp_path / "project"
    project.mkdir()
    kb = tmp_path / "kb"
    kb.mkdir()
    docs = []
    entries = {}
    for i in [1, 2]:
        name = f"d{i:03d}"
        pdf = project / (name + ".pdf")
        w = PdfWriter()
        w.add_blank_page(width=100, height=100)
        with pdf.open("wb") as f:
            w.write(f)
        sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
        docs.append({"id": name, "path": pdf.name, "role": "bid", "bidder": name, "round": "1", "lot": "1", "expected_sha256": sha})
        (kb / name).mkdir()
        row = {
            "id": "chunk" + name,
            "content": "<table><tr><th>招标要求</th><th>实际响应</th></tr><tr><td>at least10</td><td>8</td></tr></table><table><tr><td>Unrelated</td><td>certificate</td></tr></table>",
            "positions": [[1, 0, 10, 0, 10]],
        }
        (kb / name / "chunks.jsonl").write_text(json.dumps(row) + "\n")
        entries[name] = {"dataset_id": "kb" + name, "document_id": "doc" + name, "snapshot": name, "source_sha256": sha}
    (project / "manifest.json").write_text(json.dumps({"schema_version": "1.0", "project_id": "test", "documents": docs}))
    (kb / "index.json").write_text(json.dumps({"ready": True, "fingerprint": "fake-test", "documents": entries}))
    p = CollusionProfile({"合并规则ID": "M04", "规则名称": "test"}, project, tmp_path / "outputs", tmp_path / "cache", AuditTrace(tmp_path / "trace"), knowledge_root=kb)
    # Tests must never create the real shared calculation cache.
    p.adapters.shared_cache = tmp_path / "calc-cache"
    p.adapters.cache = tmp_path / "parse-cache"
    tools = {t.name: t for t in p.tools}

    async def run():
        result = json.loads(await tools["audit_m04"].ainvoke({}))
        for d in entries:
            await tools["knowledge_read"].ainvoke({"document_id": d})
        units = json.loads(await tools["audit_review_units"].ainvoke({}))
        return result, units

    result, units = asyncio.run(run())
    assert result["status"] != "error" and len(units["items"]) == 4
    assert p.extensions().finish_validator(None, None)
    records = [{"unit_id": r["unit_id"], "judgement": "已核对" if r["kind"] == "source_scope" else "负偏离", "comparison": "8 less than10; original synthetic OCR comparison", "evidence_refs": r["source_refs"][:1]} for r in units["items"]]
    response = SimpleNamespace(artifact_metadata={"review_records": records}, result="done", completion_status="complete")
    p.observe_result(SimpleNamespace(action_id="a"), response)
    assert p.adapters.ledger.status()["complete"] and p.extensions().finish_validator(None, None) is None
    assert p.adapters.runs[0]["cache"]["hit"] is False
    again = json.loads(asyncio.run(tools["audit_m04"].ainvoke({})))
    assert again["cache"]["hit"] and again["run_id"] == result["run_id"]

    added = json.loads(asyncio.run(tools["audit_add_review_units"].ainvoke({"document_id": "d001", "items": [{"requirement": "at least10", "response": "8", "source_refs": ["d001:chunkd001"]}]})))
    assert len(added["unit_ids"]) == 1
    assert p.adapters.ledger.status()["unreviewed"] == 1
