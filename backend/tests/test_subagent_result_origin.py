"""Real executor extraction and DR2 normalization must keep recovered tool text
from becoming a report Outline artifact."""

from __future__ import annotations

from types import SimpleNamespace

from deerflow.sp.subagents import SPSubagentStatus, SPSubagentTask
from deerflow.sp.subagents.dr2_adapter import normalize_dr2_subagent_result

RECOVERED = "[Recovered tool evidence before safety stop]\n\n[read_file output]\n" + ("body " * 300)


def _outline_task() -> SPSubagentTask:
    return SPSubagentTask(
        action_id="a-outline",
        subagent_type="outline",
        task="plan",
        description="plan",
    )


def test_empty_length_tool_evidence_does_not_become_outline_artifact():
    raw = SimpleNamespace(
        status=SPSubagentStatus.COMPLETED,
        result=RECOVERED,
        result_source="recovered_tool_evidence",
        artifact_content=None,
        artifact_type=None,
        artifact_metadata={},
        stop_reason=None,
        ai_messages=[{"response_metadata": {"finish_reason": "length"}}],
    )
    normalized = normalize_dr2_subagent_result(raw, task=_outline_task())

    assert normalized.artifact_content is None
    assert normalized.artifact_type is None
    assert normalized.artifact_metadata["result_source"] == "recovered_tool_evidence"


def test_same_recovered_result_still_archives_for_ordinary_long_results():
    raw = SimpleNamespace(
        status=SPSubagentStatus.COMPLETED,
        result=RECOVERED,
        result_source="recovered_tool_evidence",
        artifact_content=None,
        artifact_type=None,
        artifact_metadata={},
        stop_reason=None,
        ai_messages=[{"response_metadata": {"finish_reason": "length"}}],
    )
    task = SPSubagentTask(
        action_id="a-research",
        subagent_type="researcher",
        task="research",
        description="research",
    )

    normalized = normalize_dr2_subagent_result(raw, task=task)

    assert normalized.artifact_content is not None
    assert normalized.artifact_type == "research_observation"

