import json
from pathlib import Path

import pytest
from test_sp_report_p3 import _finish
from test_sp_report_writer_first import complete, setup_edit

from deerflow.config import paths as paths_module
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.sp.report import hooks
from deerflow.sp.report import store as native
from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.report.turns.store import file_manifest


def upload_fixture(tmp_path, monkeypatch, filename="brief.md", metadata_path=None, converted=False, symlink=False):
    paths = paths_module.Paths(tmp_path / "runtime")
    monkeypatch.setattr(paths_module, "_paths", paths)
    uploads = paths.sandbox_uploads_dir("t1", user_id=get_effective_user_id())
    uploads.mkdir(parents=True)
    text = "# 用户内部建议\r\n\r\n编号 MT-UPLOAD；导出样本、回退演练、交接责任人。\r\n"
    actual = uploads / filename
    if symlink:
        target = tmp_path / "outside.md"
        target.write_text("PRIVATE_NOT_AN_UPLOAD")
        actual.symlink_to(target)
    else:
        actual.write_bytes(b"binary original" if converted else text.encode())
    if converted:
        actual.with_suffix(".md").write_bytes(text.encode())
    files = [{"filename": filename, "path": metadata_path or "/mnt/user-data/uploads/" + filename, "status": "uploaded"}]
    ctx, action, task, tools, original = setup_edit(tmp_path / "outputs", followup_kwargs={"files": files})
    return ctx, action, task, tools, original, actual, text


def test_new_upload_becomes_native_material_and_citable_without_extra_agent(tmp_path, monkeypatch):
    ctx, action, task, tools, original, upload, text = upload_fixture(tmp_path, monkeypatch)
    refs = task.context_refs["report_local_edit"]["uploaded_materials"]
    assert len(refs) == 1 and refs[0]["status"] == "saved"
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    material = next(m for m in ledger.materials() if m["material_id"] == refs[0]["material_id"])
    assert native.read_text(Path(material["path"])) == text
    source = next(s for s in ledger.payload["sources"] if s["source_id"] == refs[0]["source_id"])
    assert source["source_kind"] == "user_upload" and not source.get("url")
    assert "MT-UPLOAD" in tools["report_read_material"].invoke({"material_id": material["material_id"]})
    marker = refs[0]["source_id"]
    result = json.loads(tools["report_replace_text"].invoke({"node_id": "n2", "old_text": "Seed", "new_text": "User recommendation [" + marker + ":1-3]"}))
    assert result["status"] == "saved"
    assert not complete(ctx, action, task).error
    assert not _finish(ctx).error
    final = ReportLedger.load(ledger.paths).payload["final_report"]
    assert marker in final["citation_numbering"]
    assert "brief.md" in native.read_text(Path(final["path"]))
    upload.write_text("upload changed after delivery")
    assert native.read_text(Path(material["path"])) == text
    assert file_manifest(tmp_path / "outputs/report/r1") == original


@pytest.mark.parametrize("converted", [False, True])
def test_uploaded_text_and_existing_conversion_are_saved_idempotently(tmp_path, monkeypatch, converted):
    ctx, _, task, _, _, _, text = upload_fixture(tmp_path, monkeypatch, filename="brief.docx" if converted else "brief.md", converted=converted)
    refs = task.context_refs["report_local_edit"]["uploaded_materials"]
    assert refs[0]["status"] == "saved"
    from deerflow.sp.report.turns import adapter
    from deerflow.sp.report.turns.uploads import ingest_message_uploads

    store, message, _, _ = adapter.current_turn(ctx.state, thread_id="t1")
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    before = (len(ledger.materials()), len(ledger.payload["sources"]))
    with native.report_lock(ledger.paths):
        repeated = ingest_message_uploads(ledger, message, store.owner)
    assert repeated[0]["material_id"] == refs[0]["material_id"]
    assert before == (len(ledger.materials()), len(ledger.payload["sources"]))
    assert native.read_text(Path(ledger.materials()[-1]["path"])) == text


@pytest.mark.parametrize("kwargs", [{"metadata_path": "/mnt/user-data/uploads/../secret.md"}, {"symlink": True}])
def test_upload_ingestion_does_not_read_outside_declared_thread_files(tmp_path, monkeypatch, kwargs):
    ctx, _, task, _, _, _, _ = upload_fixture(tmp_path, monkeypatch, **kwargs)
    refs = task.context_refs["report_local_edit"]["uploaded_materials"]
    assert refs[0]["status"] == "unavailable"
    assert "PRIVATE_NOT_AN_UPLOAD" not in str(refs)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert not any(s.get("source_kind") == "user_upload" for s in ledger.payload["sources"])
