"""Current-run OCR input receipts; no risk inference and no review ledger."""

import re
from collections import defaultdict


class OCRReadReceipts:
    def __init__(self, knowledge):
        self.knowledge = knowledge
        self.seen = defaultdict(set)
        self.windows = defaultdict(list)
        self.texts = {}

    def source_view(self, path):
        match = re.fullmatch(r"/knowledge/(d\d{3})/(parsed\.md|pages/(\d+)(?:-(\d+))?\.md)", path)
        if not match:
            return None
        doc = match[1]
        rows = self.knowledge.rows(doc)
        if path not in self.texts:
            if match[2] == "parsed.md":
                folder = (self.knowledge.root / self.knowledge.document(doc)["snapshot"]).resolve()
                if not folder.is_relative_to(self.knowledge.root):
                    raise ValueError("Snapshot escaped knowledge root")
                text = (folder / "parsed.md").read_text()
            else:
                text = self.knowledge.page_text(doc, int(match[3]), int(match[4] or match[3]))["text"]
            self.texts[path] = text
        return doc, self.texts[path], rows

    def describe_window(self, path, offset, length):
        source = self.source_view(path)
        if source is None:
            return []
        _, text, rows = source
        found = []
        for row in rows:
            if not row["content"].strip():
                continue
            marker = "[chunk " + row["id"] + " | "
            start = text.find(marker)
            if start < 0:
                continue
            start = text.index("\n", start) + 1
            end = start + len(row["content"])
            if start < offset + length and end > offset:
                found.append({"chunk_id": row["id"], "physical_pages": sorted({int(p[0]) for p in row["positions"]}), "full_content_returned": offset <= start and end <= offset + length})
        return found

    def observe(self, name, result, arguments):
        if not isinstance(result, dict) or result.get("status") in {"error", "already_returned"}:
            return
        if name == "read_file" and result.get("source") == "persistent_MinerU_snapshot":
            path = result.get("file_path", "")
            source = self.source_view(path)
            if source is None:
                return
            doc, text, rows = source
            offset, returned = result["offset"], result["text"]
            if text[offset : offset + len(returned)] != returned:
                raise ValueError("OCR receipt differs from immutable source")
            spans = self.windows[path] + [[offset, offset + len(returned)]]
            merged = []
            for a, b in sorted(spans):
                if merged and a <= merged[-1][1]:
                    merged[-1][1] = max(b, merged[-1][1])
                else:
                    merged.append([a, b])
            self.windows[path] = merged
            for a, b in merged:
                self.seen[doc].update(row["chunk_id"] for row in self.describe_window(path, a, b - a) if row["full_content_returned"])
        elif name in {"knowledge_read", "ragflow_retrieve"}:
            doc = result.get("document_id") or arguments.get("document_id")
            if doc not in self.knowledge.index["documents"]:
                return
            known = {r["id"]: r for r in self.knowledge.rows(doc)}
            for row in result.get("items", result.get("chunks", [])):
                key = str(row.get("id", row.get("chunk_id", "")))
                text = row.get("content") or row.get("content_with_weight") or ""
                if key in known and text and text == known[key]["content"]:
                    self.seen[doc].add(key)

    def observe_model_input(self, messages):
        """Only exact full original chunks actually present in model text count."""
        import json

        known = {}

        def walk(value, document_id=None):
            if isinstance(value, dict):
                if value.get("source") == "verified_current_calculation_source" and value.get("source_sha256") and isinstance(value.get("text"), str):
                    try:
                        decoded = json.loads(value["text"])
                    except (ValueError, TypeError):
                        decoded = None
                    if isinstance(decoded, (dict, list)):
                        walk(decoded, document_id)
                document_id = value.get("document_id", document_id)
                if document_id in self.knowledge.index["documents"] and value.get("chunk_id") and isinstance(value.get("content"), str):
                    if document_id not in known:
                        known[document_id] = {r["id"]: r["content"] for r in self.knowledge.rows(document_id)}
                    cid = value["chunk_id"]
                    if value["content"] and known[document_id].get(cid) == value["content"]:
                        self.seen[document_id].add(cid)
                for child in value.values():
                    if isinstance(child, (dict, list)):
                        walk(child, document_id)
            elif isinstance(value, list):
                for child in value:
                    walk(child, document_id)

        for message in messages:
            content = getattr(message, "content", "")
            texts = [content] if isinstance(content, str) else [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"] if isinstance(content, list) else []
            for text in texts:
                for candidate in [text, text.split("\n", 1)[-1]]:
                    try:
                        walk(json.loads(candidate))
                        break
                    except (ValueError, TypeError):
                        continue

    def page_sets(self):
        consulted, complete = {}, {}
        for doc, seen in self.seen.items():
            pages = defaultdict(set)
            for row in self.knowledge.rows(doc):
                for pos in row["positions"]:
                    pages[int(pos[0])].add(row["id"])
            consulted[doc] = sorted(p for p, ids in pages.items() if ids & seen)
            complete[doc] = sorted(p for p, ids in pages.items() if ids <= seen)
        return {"consulted": consulted, "complete": complete}

    def candidate_pairs(self, indexes):
        pages = self.page_sets()["consulted"]
        return {row["offset"] for index in indexes for row in index.get("findings", []) if len(row.get("pair", [])) == 2 and all(e["page"] in pages.get(e["document_id"], []) for e in row["pair"])}

    def facts(self, indexes):
        pages = self.page_sets()
        return {
            "ocr_pages_with_complete_chunks": pages["consulted"],
            "ocr_pages_with_all_chunks_returned": pages["complete"],
            "candidate_offsets_with_ocr_at_both_endpoints": sorted(self.candidate_pairs(indexes)),
            "note": "Input receipts are not semantic review. A multi-page chunk is returned whole; a page with some complete chunks is not necessarily fully read. No requirement to classify every candidate.",
        }
