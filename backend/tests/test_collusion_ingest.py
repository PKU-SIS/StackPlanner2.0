"""Real preparation contracts with a deterministic, isolated fake service."""

import hashlib
import json

from pypdf import PdfWriter

from app.experiments.collusion_audit import ingest
from app.experiments.collusion_audit.knowledge import KnowledgeStore


def test_one_pdf_one_kb_and_resume_never_reuploads_or_reparses(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    docs = []
    for i in [1, 2]:
        f = project / f"{i}.pdf"
        w = PdfWriter()
        w.add_blank_page(width=100, height=100)
        with f.open("wb") as out:
            w.write(out)
        docs.append({"id": f"d{i:03d}", "path": f.name, "role": "bid", "bidder": str(i), "round": "1", "lot": "1", "expected_sha256": hashlib.sha256(f.read_bytes()).hexdigest()})
    (project / "manifest.json").write_text(json.dumps({"schema_version": "1.0", "project_id": "synthetic", "documents": docs}))
    monkeypatch.setattr(ingest, "dotenv_values", lambda path: {"RAGFLOW_API_URL": "https://fake", "RAGFLOW_API_KEY": "private"})
    db = {}
    writes = []

    class Client:
        def __init__(self, *a):
            self.session = type("Session", (), {"close": lambda s: None})()

        def request(self, method, path, **kwargs):
            if method == "GET" and path == "/datasets":
                identifier = kwargs["params"].get("id")
                return [r for k, r in db.items() if identifier is None or k == identifier]
            if method == "POST" and path == "/datasets":
                writes.append("create")
                v = kwargs["json"]
                kb = "kb" + str(len(db))
                db[kb] = {"id": kb, **v, "docs": []}
                return db[kb]
            kb = path.split("/")[2]
            if method == "POST" and path.endswith("/documents"):
                writes.append("upload")
                name = kwargs["files"]["file"][0]
                d = {"id": "doc" + kb, "name": name, "run": "UNSTART"}
                db[kb]["docs"] = [d]
                return [d]
            if method == "POST" and path.endswith("/chunks"):
                writes.append("parse")
                db[kb]["docs"][0].update(run="DONE", chunk_count=1)
                return None
            raise AssertionError((method, path))

        def documents(self, kb):
            return db[kb]["docs"]

        def chunks(self, kb, doc):
            return [{"id": "chunk" + doc, "document_id": doc, "content": "actual parsed synthetic text", "positions": [[1, 0, 1, 0, 1]]}]

    monkeypatch.setattr(ingest, "RagClient", Client)
    root = tmp_path / "knowledge"
    one = ingest.build(project, root, workers=1)
    before = list(writes)
    two = ingest.build(project, root, workers=1)
    assert one["ready"] and two["ready"] and writes == before
    assert before.count("create") == before.count("upload") == before.count("parse") == 2
    store = KnowledgeStore(root)
    assert len({d["dataset_id"] for d in store.index["documents"].values()}) == 2
    assert all("MinerU" in d["parser_config"]["layout_recognize"] for d in db.values())
