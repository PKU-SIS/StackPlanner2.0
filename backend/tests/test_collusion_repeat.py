"""Duplicate read suppression never crosses delegation boundaries."""

import asyncio
import json

from app.experiments.collusion_audit.repetition import CURRENT_DELEGATION
from app.experiments.collusion_audit.tools import AuditTools
from app.experiments.tender_audit.observability import AuditTrace


def test_same_delegate_gets_receipt_other_delegate_gets_content(tmp_path):
    a = AuditTools("M04", tmp_path, tmp_path / "outputs", tmp_path / "cache", AuditTrace(tmp_path / "trace"), knowledge=object())
    calls = []

    def f():
        calls.append(1)
        return {"text": "original full evidence"}

    async def run():
        CURRENT_DELEGATION.set("first")
        one = json.loads(await a.recorded("project_read", {"document_id": "d1"}, f))
        two = json.loads(await a.recorded("project_read", {"document_id": "d1"}, f))
        CURRENT_DELEGATION.set("second")
        three = json.loads(await a.recorded("project_read", {"document_id": "d1"}, f))
        return one, two, three

    one, two, three = asyncio.run(run())
    assert one == three == {"text": "original full evidence"}
    assert two["status"] == "already_returned" and len(calls) == 2
    assert (a.outputs / two["result_ref"].removeprefix("/mnt/user-data/outputs/")).is_file()


def test_model_authored_notes_cannot_forge_source_read_receipts(tmp_path):
    a = AuditTools("M04", tmp_path, tmp_path / "outputs", tmp_path / "cache", AuditTrace(tmp_path / "trace"), knowledge=object())
    a.ledger.add_unit("r0", {"kind": "parameter", "source_refs": ["E1"]})
    page = {"items": [{"kind": "parameter", "source_refs": ["E1"]}]}
    a.observe_read("read_file", {"file_path": "/mnt/user-data/outputs/model_note.json", "text": json.dumps(page)})
    assert "E1" not in a.ledger.seen
    folder = a.outputs / "coverage/pages"
    folder.mkdir()
    (folder / "offset_000000.json").write_text(json.dumps(page))
    a.observe_read("read_file", {"file_path": "/mnt/user-data/outputs/coverage/pages/offset_000000.json", "text": json.dumps(page)})
    assert "E1" in a.ledger.seen
