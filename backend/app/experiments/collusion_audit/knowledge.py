"""Immutable per-document MinerU snapshots and exact-scope retrieval."""

import hashlib
import json
from pathlib import Path

DEFAULT_OPTIONS = {
    "chunk_method": "naive",
    "embedding_model": "qwen3-embedding-4b@qwen3-embedding-4b@VLLM",
    "parser_config": {
        "chunk_token_num": 512,
        "delimiter": "\n",
        "auto_keywords": 0,
        "auto_questions": 0,
        "layout_recognize": "mineru-from-env@mineru-from-env@MinerU",
        "mineru_lang": "Chinese",
        "mineru_chunk_strategy": "v3",
        "mineru_paddle_table_enhance": True,
        "raptor": {"use_raptor": False},
        "graphrag": {"use_graphrag": False},
    },
}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".pending")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def order_chunks(rows):
    result = []
    ids = set()
    for raw in rows:
        row = dict(raw)
        if not row.get("id") or row["id"] in ids:
            raise ValueError("Missing/duplicate chunk id")
        ids.add(row["id"])
        positions = row.get("positions") or row.get("position_int")
        if not isinstance(positions, list) or not positions or any(not isinstance(p, (list, tuple)) or len(p) < 5 for p in positions):
            raise ValueError("Reliable source position required; API/id order is not source order")
        row["positions"] = positions
        row["content"] = row.get("content") or row.get("content_with_weight") or ""
        if not isinstance(row["content"], str):
            raise ValueError("Invalid parsed content type")
        row["content_state"] = "text" if row["content"].strip() else "image_only_or_empty"
        # RAGFlow positions: physical page, x0, x1, y0, y1 (one-based pages).
        row["source_order_key"] = min((int(p[0]), float(p[3]), float(p[1])) for p in positions)
        result.append(row)
    result.sort(key=lambda r: (*r["source_order_key"], r["id"]))
    for i, row in enumerate(result):
        row["offset"] = i
    return result


class KnowledgeStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.index = json.loads((self.root / "index.json").read_text())
        if not self.index.get("ready"):
            raise ValueError("Knowledge index is not ready")
        datasets = [d["dataset_id"] for d in self.index["documents"].values()]
        if len(datasets) != len(set(datasets)):
            raise ValueError("Each knowledge base must contain exactly one document")
        self._rows = {}

    def document(self, document_id):
        if document_id not in self.index["documents"]:
            raise ValueError("Document is outside the current project knowledge scope")
        return self.index["documents"][document_id]

    def rows(self, document_id):
        entry = self.document(document_id)
        if document_id not in self._rows:
            folder = (self.root / entry["snapshot"]).resolve()
            if not folder.is_relative_to(self.root):
                raise ValueError("Snapshot escaped knowledge root")
            source = folder / "chunks.jsonl"
            expected = entry.get("chunk_export_sha256")
            if expected and hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                raise ValueError("Knowledge snapshot changed")
            self._rows[document_id] = [json.loads(s) for s in source.read_text().splitlines()]
        return self._rows[document_id]

    def read(self, document_id, offset=0, limit=5):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("offset>=0 and limit1..20 required")
        rows = self.rows(document_id)
        selected = rows[offset : offset + limit]
        return {
            "document_id": document_id,
            "offset": offset,
            "total": len(rows),
            "items": selected,
            "next_offset": offset + len(selected) if offset + len(selected) < len(rows) else None,
            "source": "persistent_MinerU_snapshot",
            "note": "Read coverage is not semantic review.",
        }

    def page_text(self, document_id, page_start, page_end):
        entry = self.document(document_id)
        if type(page_start) is not int or type(page_end) is not int or not 1 <= page_start <= page_end <= entry["original_pages"]:
            raise ValueError("Physical pages must be within the original document")
        selected = [row for row in self.rows(document_id) if any(page_start <= int(p[0]) <= page_end for p in row["positions"])]
        text = "\n\n".join("[chunk " + row["id"] + " | pages " + ",".join(str(p) for p in sorted({int(p[0]) for p in row["positions"]})) + "]\n" + row["content"] for row in selected)
        represented = {int(p[0]) for row in selected if row["content"].strip() for p in row["positions"]}
        return {
            "document_id": document_id,
            "requested_pages": list(range(page_start, page_end + 1)),
            "text": text,
            "pages_without_text_chunks": [p for p in range(page_start, page_end + 1) if p not in represented],
            "note": "Physical page addresses select original-position chunks, returned whole and in source order. Multi-page chunks may include adjacent pages; OCR does not prove image or stamp identity.",
        }

    def validate_hits(self, document_id, hits):
        entry = self.document(document_id)
        known = {row["id"] for row in self.rows(document_id)}
        for hit in hits:
            if hit.get("document_id", hit.get("doc_id")) != entry["document_id"] or str(hit.get("id", hit.get("chunk_id", ""))) not in known:
                raise ValueError("Retrieval returned foreign/changed chunks")

    def context(self, *, logical_ids_only=False):
        value = {
            "documents": {
                k: {
                    field: v[field]
                    for field in ["dataset_id", "document_id", "source_sha256", "chunk_count", "dataset_url", "chunks_url", "parsed_file", "original_pages", "represented_pages", "pages_without_text_chunks", "counter_warning"]
                    if field in v
                }
                for k, v in self.index["documents"].items()
            },
            "snapshot_fingerprint": self.index.get("fingerprint"),
            "read_file_root": "/knowledge/",
            "note": "One PDF per KB; use scoped tools, credentials stay server-side.",
        }
        if logical_ids_only:
            for key, doc in value["documents"].items():
                if "document_id" in doc:
                    doc["ragflow_remote_document_id"] = doc.pop("document_id")
                doc["source_document_id"] = key
            value["note"] += " Tool document_id accepts source_document_id (dXXX), never ragflow_remote_document_id."
            value["page_read_file_template"] = "/knowledge/{source_document_id}/pages/{page_start}-{page_end}.md"
            value["page_read_note"] = (
                "Use one-based PDF physical pages. read_file preserves its character cursor for long spans; no guessing character offsets to find a page. Chunks spanning pages are returned whole; explicit empty-page gaps remain."
            )
        return value


class RagClient:
    def __init__(self, base_url, key, timeout=60):
        import requests

        self.base_url = base_url.rstrip("/").removesuffix("/api/v1")
        self._key = key
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers["Authorization"] = "Bearer " + key
        self.timeout = timeout

    def request(self, method, path, **kwargs):
        response = self.session.request(method, self.base_url + "/api/v1" + path, timeout=self.timeout, allow_redirects=False, **kwargs)
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            message = str(data.get("message", "RAGFlow request failed")).replace(self._key, "<REDACTED>")[:400]
            raise RuntimeError(message)
        return data.get("data")

    def documents(self, dataset_id):
        data = self.request("GET", f"/datasets/{dataset_id}/documents", params={"page": 1, "page_size": 100})
        return data if isinstance(data, list) else (data or {}).get("docs", [])

    def chunks(self, dataset_id, document_id):
        rows = []
        page = 1
        while True:
            data = self.request("GET", f"/datasets/{dataset_id}/documents/{document_id}/chunks", params={"page": page, "page_size": 100})
            current = data.get("chunks", [])
            rows.extend(current)
            if len(rows) >= data.get("total", len(rows)) or not current:
                break
            page += 1
        if len(rows) != data.get("total", len(rows)):
            raise ValueError("Incomplete chunk export")
        return rows

    def retrieve(self, store, document_id, question, page=1, page_size=5):
        if not question.strip() or len(question) > 4000 or page < 1 or not 1 <= page_size <= 20:
            raise ValueError("Invalid retrieval query/pagination")
        entry = store.document(document_id)
        data = self.request(
            "POST",
            "/retrieval",
            json={
                "question": question,
                "dataset_ids": [entry["dataset_id"]],
                "document_ids": [entry["document_id"]],
                "page": page,
                "page_size": page_size,
                "similarity_threshold": 0.0,
                "vector_similarity_weight": 0.7,
                "top_k": 1024,
                "highlight": False,
                "keyword": False,
            },
        )
        store.validate_hits(document_id, data.get("chunks", []))
        return {"document_id": document_id, "dataset_id": entry["dataset_id"], "page": page, "total": data.get("total"), "chunks": data.get("chunks", [])}
