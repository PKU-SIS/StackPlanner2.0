"""Explicit preparation only: one immutable MinerU knowledge base per PDF."""

import argparse
import hashlib
import json
import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values
from pypdf import PdfReader

from .knowledge import DEFAULT_OPTIONS, RagClient, atomic, fingerprint, order_chunks

ROOT = Path(__file__).resolve().parents[4]
HOME = ROOT / "collusion_audit"


def build(project, destination, *, workers=3, timeout=7200, env_file=None):
    project, destination = Path(project), Path(destination)
    manifest = json.loads((project / "manifest.json").read_text())
    documents = [d for d in manifest["documents"] if Path(d["path"]).suffix.lower() == ".pdf"]
    identity = {"manifest": manifest, "options": DEFAULT_OPTIONS, "scope": "one_PDF_per_dataset_v1"}
    digest = fingerprint(identity)
    config = {**dotenv_values(Path(env_file or os.getenv("COLLUSION_RAGFLOW_ENV_FILE") or ROOT / ".deer-flow/collusion-ragflow.env")), **{k: v for k, v in os.environ.items() if k.startswith("RAGFLOW_")}}
    base, key = config["RAGFLOW_API_URL"].rstrip("/"), config["RAGFLOW_API_KEY"]
    destination.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    state_file = destination / "index.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {"fingerprint": digest, "options": DEFAULT_OPTIONS, "base_url": base, "ready": False, "documents": {}, "started_at": datetime.now(UTC).isoformat()}
    if state["fingerprint"] != digest or state["base_url"] != base:
        raise ValueError("Input/settings changed; create a new knowledge version")
    state["ready"] = False
    atomic(state_file, state)
    # This deployment treats an absent name as a permission error. Inventory
    # once, then match locally, as the reference branch does.
    inventory = {}
    page = 1
    listing = RagClient(base, key)
    while True:
        data = listing.request("GET", "/datasets", params={"page": page, "page_size": 100})
        rows = data if isinstance(data, list) else (data or {}).get("datasets", [])
        for row in rows:
            inventory.setdefault(row["name"], []).append(row)
        if len(rows) < 100:
            break
        page += 1
    listing.session.close()

    def process(doc):
        client = RagClient(base, key)
        doc_id = doc["id"]
        source = project / doc["path"]
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        if sha != doc["expected_sha256"]:
            raise ValueError("Source bytes changed")
        name = "collusion-v15-" + doc_id + "-" + fingerprint({"project": manifest["project_id"], "sha": sha, "settings": DEFAULT_OPTIONS})[:24]
        folder = destination / doc_id
        folder.mkdir(exist_ok=True)
        with lock:
            record = dict(state["documents"].get(doc_id, {}))

        def checkpoint():
            with lock:
                state["documents"][doc_id] = dict(record)
                atomic(state_file, state)

        if not record.get("dataset_id"):
            matches = inventory.get(name, [])
            if len(matches) > 1:
                raise ValueError("Ambiguous knowledge base name")
            kb = matches[0] if matches else client.request("POST", "/datasets", json={"name": name, **DEFAULT_OPTIONS})
            record.update(dataset_id=kb["id"], name=name, source_sha256=sha, snapshot=doc_id, original_path=doc["path"])
            checkpoint()
        kb = record["dataset_id"]
        data = client.request("GET", "/datasets", params={"id": kb, "page": 1, "page_size": 100})
        entries = data if isinstance(data, list) else data.get("datasets", [])
        effective = next(r for r in entries if r["id"] == kb)
        pc = effective.get("parser_config", {})
        if "mineru" not in str(pc.get("layout_recognize", "")).lower() or pc.get("chunk_token_num") != 512 or pc.get("raptor", {}).get("use_raptor") or pc.get("graphrag", {}).get("use_graphrag"):
            raise ValueError("Effective remote parser differs from explicit MinerU config")
        if effective.get("embedding_model") != DEFAULT_OPTIONS["embedding_model"] or effective.get("chunk_method") != "naive":
            raise ValueError("Remote embedding or chunk method changed")
        for field in ["delimiter", "auto_keywords", "auto_questions"]:
            if pc.get(field) != DEFAULT_OPTIONS["parser_config"][field]:
                raise ValueError("Remote parser field differs: " + field)
        atomic(folder / "effective_dataset.json", effective)
        uploaded = client.documents(kb)
        upload_name = doc_id + "-" + sha[:16] + ".pdf"
        if any(d["name"] != upload_name for d in uploaded) or len(uploaded) > 1:
            raise ValueError("Knowledge base has foreign documents")
        if not uploaded:
            with source.open("rb") as stream:
                uploaded = client.request("POST", f"/datasets/{kb}/documents", files={"file": (upload_name, stream, "application/pdf")})
        remote = uploaded[0]
        if record.get("document_id") and record["document_id"] != remote["id"]:
            raise ValueError("Remote document identity changed")
        record["document_id"] = remote["id"]
        checkpoint()
        status = str(remote.get("run", "UNSTART")).upper()
        if status in {"0", "UNSTART"}:
            client.request("POST", f"/datasets/{kb}/chunks", json={"document_ids": [remote["id"]]})
        elif status in {"4", "FAIL", "2", "CANCEL"}:
            raise ValueError("Remote parse failed; inspect saved state before explicit retry")
        deadline = time.monotonic() + timeout
        while True:
            ds = client.documents(kb)
            if len(ds) != 1 or ds[0]["id"] != remote["id"]:
                raise ValueError("Remote document scope changed")
            remote = ds[0]
            status = str(remote.get("run", "UNSTART")).upper()
            record.update(run=status, chunk_count=remote.get("chunk_count", 0), progress=remote.get("progress"), progress_msg=remote.get("progress_msg"))
            checkpoint()
            if status in {"3", "DONE"}:
                break
            if status in {"4", "FAIL", "2", "CANCEL"}:
                raise ValueError("MinerU parse failed; see index progress_msg")
            if time.monotonic() >= deadline:
                raise TimeoutError("MinerU still parsing; resume this preparation, never reupload")
            time.sleep(5)
        if not remote.get("chunk_count"):
            raise ValueError("Zero indexed chunks")
        rows = order_chunks(client.chunks(kb, remote["id"]))
        if not rows:
            raise ValueError("No enumerable indexed chunks")
        declared_chunk_count = remote["chunk_count"]
        count_warning = (
            None if len(rows) == declared_chunk_count else {"declared": declared_chunk_count, "enumerated": len(rows), "note": "Service counters differ; export is complete against list-chunks API total, not proof of source completeness."}
        )
        if any(r.get("document_id", r.get("doc_id", remote["id"])) != remote["id"] for r in rows):
            raise ValueError("Export includes a foreign document")
        with (folder / "chunks.jsonl").open("w") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        pages = sorted({int(p[0]) for r in rows for p in r["positions"]})
        count = len(PdfReader(source).pages)
        if any(p < 1 or p > count for p in pages):
            raise ValueError("Invalid physical page mapping")
        text = "\n\n".join(f"[chunk {r['id']} | pages {','.join(str(p[0]) for p in r['positions'])}]\n{r['content']}" for r in rows)
        (folder / "parsed.md").write_text(text)
        text_pages = sorted({int(p[0]) for r in rows if r["content_state"] == "text" for p in r["positions"]})
        atomic(folder / "remote_document.json", remote)
        atomic(
            folder / "page_coverage.json",
            {
                "original_pages": count,
                "pages_with_chunks": pages,
                "pages_with_text_chunks": text_pages,
                "pages_without_text_chunks": sorted(set(range(1, count + 1)) - set(text_pages)),
                "textless_chunk_count": sum(r["content_state"] != "text" for r in rows),
                "declared_vs_enumerated": count_warning,
                "unrepresented_pages": sorted(set(range(1, count + 1)) - set(pages)),
                "note": "Missing page is not assumed blank. OCR accuracy requires original-image checks.",
            },
        )
        record.update(
            ready=True,
            chunk_count=len(rows),
            dataset_url=base + "/api/v1/datasets?id=" + kb,
            chunks_url=base + f"/api/v1/datasets/{kb}/documents/{remote['id']}/chunks",
            parsed_file="/knowledge/" + doc_id + "/parsed.md",
            original_pages=count,
            represented_pages=len(pages),
            pages_without_text_chunks=sorted(set(range(1, count + 1)) - set(text_pages)),
            declared_chunk_count=declared_chunk_count,
            counter_warning=count_warning,
            chunk_export_sha256=hashlib.sha256((folder / "chunks.jsonl").read_bytes()).hexdigest(),
        )
        checkpoint()
        print(json.dumps({"document": doc_id, "ready": True, "chunks": len(rows), "pages": count, "represented": len(pages)}, ensure_ascii=False), flush=True)
        client.session.close()

    def observed_process(doc):
        try:
            process(doc)
        except Exception as exc:
            with lock:
                state["documents"].setdefault(doc["id"], {})["preparation_error"] = type(exc).__name__ + ": " + str(exc)
                atomic(state_file, state)
            raise

    # Bounded submission stops scheduling new documents after a failure.
    remaining = iter(documents)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(observed_process, d) for d in [next(remaining) for _ in range(min(workers, len(documents)))]}
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                future.result()
            for _ in done:
                doc = next(remaining, None)
                if doc is not None:
                    pending.add(pool.submit(observed_process, doc))
    state["ready"] = all(state["documents"].get(d["id"], {}).get("ready") for d in documents)
    state["completed_at"] = datetime.now(UTC).isoformat()
    atomic(state_file, state)
    return state


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument("--project", default=str(HOME / "project_complete"))
    p.add_argument("--output", default=str(HOME / "knowledge/mineru-v15"))
    p.add_argument("--env-file", type=Path)
    p.add_argument("--workers", type=int, default=3)
    a = p.parse_args()
    result = build(a.project, a.output, workers=a.workers, env_file=a.env_file)
    print(json.dumps({"ready": result["ready"], "documents": len(result["documents"])}))


if __name__ == "__main__":
    main()
