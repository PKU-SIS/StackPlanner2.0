"""Context dedupe removes only exact summary/artifact duplicates."""

from __future__ import annotations

from deerflow.sp.actions.handlers.delegate import _dedupe_outline_artifact_bodies
from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.report.packets import build_outline_packet, build_pre_research_packet
from deerflow.sp.report.store import ReportPaths


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


def test_outline_keeps_all_identities_and_full_initial_summary_without_repeated_previews(tmp_path):
    paths = ReportPaths(tmp_path, "r1")
    ledger = ReportLedger.create(paths, request_text="Research")
    ledger.begin_research_action("a1", [], allow_empty=True, kind="pre_research")
    body = "# Cited synthesis\n" + "Useful findings [S1].\n" * 2000
    summary = paths.directory / "research/a1/summary.md"
    summary.parent.mkdir(parents=True)
    summary.write_text(body)
    ledger.payload["summaries"] = [{"action_id": "a1", "path": str(summary), "virtual_path": paths.virtual(summary)}]
    ledger.payload["materials"] = [{"material_id": f"m{i}", "source_id": f"S{i}", "title": f"Title {i}", "url": f"https://example.com/{i}", "lines": 99, "preview": "Large redundant preview " * 300} for i in range(1, 101)]
    packet = build_outline_packet(ledger)
    assert len(packet["materials"]) == 100
    assert packet["materials"][99]["material_id"] == "m100"
    assert packet["materials"][99]["lines"] == 99
    assert all("preview" not in item for item in packet["materials"])
    assert packet["summaries"][0]["content"] == body
    assert not packet["summaries"][0]["content_truncated"]
    assert "preview" in build_pre_research_packet(ledger, action_id="a1")["materials"][0]
