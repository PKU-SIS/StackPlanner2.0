"""Actual network attempts and tool invocations have different units."""

from app.experiments.collusion_audit.metrics import knowledge_metrics


def test_cached_retrieval_is_not_an_extra_http_request():
    rows = [
        {"tool": "ragflow_retrieve", "status": "completed", "duration_ms": 5, "chunk_count": 2, "chunk_ids": ["a", "b"], "http_request_attempted": True},
        {"tool": "ragflow_retrieve", "status": "completed", "duration_ms": 1, "deduplicated_read": True, "http_request_attempted": False},
        {"tool": "ragflow_retrieve", "status": "failed", "duration_ms": 2, "http_request_attempted": True},
    ]
    m = knowledge_metrics(rows)
    assert m["ragflow_calls"] == 3 and m["ragflow_http_calls"] == 2 and m["ragflow_failed_calls"] == 1
    assert m["returned_chunks"] == m["unique_chunks"] == 2 and m["repeat_read_receipts"] == 1
