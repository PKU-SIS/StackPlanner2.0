"""Restore existing RAGFlow parses using a checked, credential-free project mapping."""

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from pypdf import PdfReader

from .data import sha256
from .knowledge import RagClient, atomic, order_chunks
from .settings import ragflow_settings


def restore(mapping, project, output, *, env_file=None):
    mapping, project, output = Path(mapping), Path(project).resolve(), Path(output).resolve()
    published = json.loads(mapping.read_text())
    manifest = json.loads((project / "manifest.json").read_text())
    reference = published["project_manifest"]

    def identify(value):
        return {d["id"]: (d["path"], d["expected_sha256"]) for d in value["documents"]}

    if identify(manifest) != identify(reference):
        raise ValueError("Project manifest does not match the published original paths, identities and SHA256")
    for doc in manifest["documents"]:
        source = (project / doc["path"]).resolve()
        if not source.is_relative_to(project) or sha256(source) != doc["expected_sha256"]:
            raise ValueError("Original document changed or escaped the project")
    pdfs = {d["id"]: d for d in manifest["documents"] if Path(d["path"]).suffix.lower() == ".pdf"}
    if not published.get("ready") or set(pdfs) != set(published["documents"]):
        raise ValueError("Published mapping is incomplete for this project")
    datasets = [d["dataset_id"] for d in published["documents"].values()]
    if len(datasets) != len(set(datasets)):
        raise ValueError("Shared knowledge must contain one document per dataset")
    if output.exists() or output.is_relative_to(project) or project.is_relative_to(output):
        raise ValueError("Choose a new snapshot directory separate from the originals")
    config = ragflow_settings(env_file)
    if config["RAGFLOW_API_URL"].removesuffix("/api/v1") != published["base_url"].rstrip("/").removesuffix("/api/v1"):
        raise ValueError("Configured RAGFlow endpoint differs from the published mapping")
    client = RagClient(config["RAGFLOW_API_URL"], config["RAGFLOW_API_KEY"])
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".shared-knowledge-", dir=output.parent))
    state = {key: published[key] for key in ("fingerprint", "options", "base_url")}
    state.update(ready=False, documents={})
    try:
        for logical, doc in pdfs.items():
            entry = published["documents"][logical]
            if entry["source_sha256"] != doc["expected_sha256"]:
                raise ValueError("Remote mapping points to different source bytes")
            remote = client.documents(entry["dataset_id"])
            if len(remote) != 1 or remote[0]["id"] != entry["document_id"] or str(remote[0].get("run")).upper() not in {"3", "DONE"}:
                raise ValueError("Shared dataset document scope or completed status changed")
            rows = order_chunks(client.chunks(entry["dataset_id"], entry["document_id"]))
            if not rows or any(row.get("document_id", row.get("doc_id", entry["document_id"])) != entry["document_id"] for row in rows):
                raise ValueError("Remote chunk response has missing or foreign content")
            body = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
            if hashlib.sha256(body.encode()).hexdigest() != entry["chunk_export_sha256"] or len(rows) != entry["chunk_count"]:
                raise ValueError("Remote parse differs from the published snapshot")
            pages = len(PdfReader(project / doc["path"]).pages)
            if pages != entry["original_pages"] or any(not 1 <= int(pos[0]) <= pages for row in rows for pos in row["positions"]):
                raise ValueError("Original page count or chunk page mapping changed")
            folder = stage / logical
            folder.mkdir()
            (folder / "chunks.jsonl").write_text(body)
            (folder / "parsed.md").write_text("\n\n".join(f"[chunk {row['id']} | pages {','.join(str(pos[0]) for pos in row['positions'])}]\n{row['content']}" for row in rows))
            state["documents"][logical] = {**entry, "snapshot": logical}
        state["ready"] = True
        atomic(stage / "index.json", state)
        stage.rename(output)
        return state
    finally:
        client.session.close()
        if stage.exists():
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    state = restore(args.mapping, args.project, args.output, env_file=args.env_file)
    print(json.dumps({"ready": state["ready"], "documents": len(state["documents"])}))


if __name__ == "__main__":
    main()
