"""Use producer-verified current sources, not guessed external requirements."""

import pytest
from test_collusion_audit import scoped_profile as scoped_profile

from deerflow.sp.actions.handlers.delegate import _apply_perception_acceptance_checks
from deerflow.sp.subagents import SPSubagentResult, SPSubagentTask


def register_actual_source(p):
    run_id = p.rule_id + "_20261116T120000_0123456789"
    root = p.outputs / "bid_audit" / run_id
    root.mkdir(parents=True, exist_ok=True)
    path = root / "facts-index.json"
    path.write_text('{"actual":"已提供当前计算与来源，不是历史分析"}')
    row = {"run_id": run_id, "status": "ok", "result_directory": "/mnt/user-data/outputs/bid_audit/" + run_id, "collections": {}}
    p.adapters.runs.append(row)
    p.adapters.register_calculation_sources(row)
    return path, "/mnt/user-data/outputs/bid_audit/" + run_id + "/facts-index.json"


@pytest.mark.parametrize("explicit", [False, True])
def test_actual_producer_registration_prevents_invented_missing_sources(scoped_profile, explicit):
    p = scoped_profile
    p.general_quality = True
    p.quality_controls = True
    path, virtual = register_actual_source(p)
    assert isinstance(p.adapters._calculation_source_files[str(path.resolve())], dict)
    text = "Read " + virtual if explicit else "Inspect the two current image candidates already supplied in context"
    task = SPSubagentTask("local", "perception", text, "perception")
    task.context_refs["latest_user_input"] = "Review the supplied project and generate a report independently."
    p.prepare_task(task)
    assert virtual in task.input_refs
    assert task.task == text and task.subagent_type == "perception"
    result = SPSubagentResult(status="completed", artifact_content="Inspected current source. Decision-Critical Missing Information: none")
    _apply_perception_acceptance_checks(result, task=task)
    assert result.artifact_metadata["task_ready"] is True
    assert result.artifact_metadata["clarification_questions"] == []
    genuine = SPSubagentResult(status="completed", artifact_content="Inspection complete", artifact_metadata={"task_ready": False, "clarification_questions": ["Missing the second-round original quote; can you supply it?"]})
    _apply_perception_acceptance_checks(genuine, task=task)
    assert genuine.artifact_metadata["task_ready"] is False
    assert genuine.artifact_metadata["clarification_questions"]


@pytest.mark.parametrize("condition", ["flag_off", "changed_source", "unregistered_draft", "existing_refs"])
def test_source_fallback_preserves_flags_integrity_and_central_refs(scoped_profile, condition):
    p = scoped_profile
    p.quality_controls = True
    p.general_quality = condition != "flag_off"
    path, virtual = register_actual_source(p)
    if condition == "changed_source":
        path.write_text("changed")
    if condition == "unregistered_draft":
        p.adapters._calculation_source_files.clear()
    original = ["central-selected-source"] if condition == "existing_refs" else []
    task = SPSubagentTask("local", "perception", "Inspect current image candidates", "perception", input_refs=list(original))
    p.prepare_task(task)
    assert task.input_refs == original


def test_local_perception_guidance_keeps_scope_decisions_native_and_contract_explicit(scoped_profile):
    from deerflow.subagents.config import SubagentConfig

    p = scoped_profile
    p.general_quality = True
    p.quality_controls = True
    register_actual_source(p)
    task = SPSubagentTask("local", "perception", "Inspect current image candidates", "perception")
    p.prepare_task(task)
    configured = p.configure_subagent(task, SubagentConfig("sp-perception", "local inspection", system_prompt="native original constraints"))
    assert "perception_observation" in configured.system_prompt
    assert "JSON字符串" in configured.system_prompt
    assert "补查深度" in p.quality_guidance()
    assert configured.system_prompt.startswith("native original constraints")
