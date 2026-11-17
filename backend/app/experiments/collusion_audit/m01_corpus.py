"""Trusted original-only OCR inputs for text recall; no generated KB metadata."""


def parsed_sources(context, knowledge):
    if knowledge is None:
        return None
    if getattr(knowledge, "general_quality", False):
        from bid_audit.source_quality import VERSION

    result = {}
    for doc in context.docs:
        if doc["role"] not in {"bid", "tender", "public_template"} or doc["id"] not in knowledge.index["documents"]:
            continue
        entry = knowledge.document(doc["id"])
        if entry["source_sha256"] != doc["sha256"]:
            raise ValueError("Original/knowledge source hash mismatch: " + doc["id"])
        chunks = []
        for row in knowledge.rows(doc["id"]):
            if row.get("available") is False or not str(row.get("content", "")).strip():
                continue
            chunks.append({"id": row["id"], "content": row["content"], "positions": row.get("positions", [])})
        result[doc["id"]] = {"source_sha256": doc["sha256"], "chunks": chunks, **({"source_view_version": VERSION} if getattr(knowledge, "general_quality", False) else {})}
    return result
