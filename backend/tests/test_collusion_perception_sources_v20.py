"""Real delegated source files prevent invented missing-source clarification."""

import hashlib

from test_collusion_audit import scoped_profile as scoped_profile

from deerflow.sp.actions.handlers.delegate import _apply_perception_acceptance_checks
from deerflow.sp.subagents import SPSubagentResult, SPSubagentTask


def current_source(profile):
    path = profile.outputs / "bid_audit/current/facts-index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"actual":"source observations"}')
    profile.adapters._calculation_source_files[str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, "/mnt/user-data/outputs/bid_audit/current/facts-index.json"


def test_declared_real_source_does_not_invent_board_questions_and_preserves_genuine_gaps(scoped_profile):
    scoped_profile.quality_controls = True
    _, virtual = current_source(scoped_profile)
    task = SPSubagentTask("inspect", "perception", "Read " + virtual, "research")
    task.context_refs["latest_user_input"] = "Create a board report from the supplied project documents."
    scoped_profile.prepare_task(task)
    assert task.input_refs == [virtual]
    assert task.task == "Read " + virtual and task.subagent_type == "perception"
    result = SPSubagentResult(status="completed", artifact_content="Inspected the supplied observations.")
    _apply_perception_acceptance_checks(result, task=task)
    assert result.artifact_metadata["task_ready"] is True
    assert result.artifact_metadata["clarification_questions"] == []
    genuine = SPSubagentResult(status="completed", artifact_content="Inspection completed.", artifact_metadata={"task_ready": False, "clarification_questions": ["Which of the two supplied scopes is authorized?"]})
    _apply_perception_acceptance_checks(genuine, task=task)
    assert genuine.artifact_metadata["task_ready"] is False and len(genuine.artifact_metadata["clarification_questions"]) == 1


def test_default_off_and_existing_input_refs_remain_native(scoped_profile):
    _, virtual = current_source(scoped_profile)
    task = SPSubagentTask("a", "perception", "Read " + virtual, "research")
    scoped_profile.prepare_task(task)
    assert task.input_refs == []
    scoped_profile.quality_controls = True
    task = SPSubagentTask("b", "perception", "Read " + virtual, "research", input_refs=["central-selected-source"])
    scoped_profile.prepare_task(task)
    assert task.input_refs == ["central-selected-source"]
    other = SPSubagentTask("c", "researcher", "Read " + virtual, "research")
    scoped_profile.prepare_task(other)
    assert other.input_refs == []


def test_drafts_nonexistent_and_changed_calculation_sources_are_not_declared(scoped_profile):
    scoped_profile.quality_controls = True
    path, virtual = current_source(scoped_profile)
    path.write_text("changed")
    draft = scoped_profile.outputs / "draft.json"
    draft.write_text('{"fake":"source"}')
    task = SPSubagentTask("a", "perception", "Read " + virtual + " and /mnt/user-data/outputs/draft.json and /mnt/user-data/outputs/not-real.json", "research")
    scoped_profile.prepare_task(task)
    assert task.input_refs == []
