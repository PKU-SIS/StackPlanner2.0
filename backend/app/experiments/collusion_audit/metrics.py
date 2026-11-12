"""Count new knowledge tools without confusing cached reads with HTTP calls."""


def knowledge_metrics(records):
    rag = [r for r in records if r.get("tool") == "ragflow_retrieve"]
    returned = sum(r.get("chunk_count", 0) for r in rag)
    unique = len({i for r in rag for i in r.get("chunk_ids", [])})
    return {
        "ragflow_calls": len(rag),
        "ragflow_http_calls": sum(bool(r.get("http_request_attempted")) for r in rag),
        "ragflow_failed_calls": sum(r["status"] != "completed" for r in rag),
        "ragflow_duration_ms": sum(r["duration_ms"] for r in rag),
        "returned_chunks": returned,
        "unique_chunks": unique,
        "duplicate_ratio": (returned - unique) / returned if returned else 0,
        "repeat_read_receipts": sum(bool(r.get("deduplicated_read")) for r in records),
        "continuous_read_calls": sum(r.get("tool") == "knowledge_read" for r in records),
        "judgement_commit_calls": sum(r.get("tool") == "audit_record_reviews" for r in records),
    }
