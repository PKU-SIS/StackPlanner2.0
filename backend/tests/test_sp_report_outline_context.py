"""Context dedupe removes only exact summary/artifact duplicates."""

from __future__ import annotations

from deerflow.sp.actions.handlers.delegate import _dedupe_outline_artifact_bodies


def test_only_exact_same_artifact_summary_is_removed():
    bundle = {
        "items": [
            {"artifact_id": "a1", "content": "same observation\n", "truncated": False},
            {"artifact_id": "a2", "content": "new version\n", "truncated": False},
            {"artifact_id": "a3", "content": "same observation\n", "truncated": False},
        ],
        "materialized_count": 3,
        "total_content_chars": 42,
        "truncated": False,
    }
    packet = {
        "summaries": [
            {"artifact_id": "a1", "content": "same observation\n", "content_truncated": False},
        ]
    }

    result = _dedupe_outline_artifact_bodies(bundle, packet)

    assert [item["artifact_id"] for item in result["items"]] == ["a2", "a3"]
    assert result["materialized_count"] == 2
    assert result["truncated"] is False


def test_truncated_summary_does_not_remove_artifact_body():
    bundle = {
        "items": [{"artifact_id": "a1", "content": "full observation\n", "truncated": False}],
        "materialized_count": 1,
        "total_content_chars": 17,
        "truncated": False,
    }
    packet = {"summaries": [{"artifact_id": "a1", "content": "full observation\n", "content_truncated": True}]}

    assert _dedupe_outline_artifact_bodies(bundle, packet) == bundle
