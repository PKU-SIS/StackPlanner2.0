"""Native file inspection can be delegated by real calculation collection names."""

import hashlib

from test_collusion_audit import scoped_profile as scoped_profile

from deerflow.sp.actions.handlers.delegate import _apply_perception_acceptance_checks
from deerflow.sp.subagents import SPSubagentResult, SPSubagentTask


def test_requested_named_collections_become_real_inputs_without_board_questions(scoped_profile):
    scoped_profile.quality_controls = True
    run = "M08_20261109T000000_0123456789"
    base = "/mnt/user-data/outputs/bid_audit/" + run
    scoped_profile.adapters.runs = [{"run_id": run, "status": "partial", "result_directory": base, "collections": {n: {"file": n + ".jsonl", "count": 1} for n in ["unpaired_anomalies", "template_exclusions", "evidence", "findings"]}}]
    for n in ["unpaired_anomalies", "template_exclusions", "evidence", "findings"]:
        p = scoped_profile.outputs / "bid_audit" / run / (n + ".jsonl")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"actual":"source"}\n')
        scoped_profile.adapters._calculation_source_files[str(p.resolve())] = hashlib.sha256(p.read_bytes()).hexdigest()
    task = SPSubagentTask("inspect", "perception", "For run " + run + " read unpaired_anomalies, template_exclusions and evidence collections.", "research")
    task.context_refs["latest_user_input"] = "Create the requested board report from the provided project data."
    scoped_profile.prepare_task(task)
    assert task.input_refs == [base + "/" + n + ".jsonl" for n in ["unpaired_anomalies", "template_exclusions", "evidence"]]
    assert task.subagent_type == "perception"
    result = SPSubagentResult(status="completed", artifact_content="Current delegated sources inspected.")
    _apply_perception_acceptance_checks(result, task=task)
    assert result.artifact_metadata["task_ready"] is True and result.artifact_metadata["clarification_questions"] == []


def test_collection_words_cannot_import_unrequested_or_unverified_sources(scoped_profile):
    scoped_profile.quality_controls = True
    run = "M08_20261109T000000_0123456789"
    scoped_profile.adapters.runs = [{"run_id": run, "status": "partial", "result_directory": "/mnt/user-data/outputs/bid_audit/" + run, "collections": {"evidence": {"file": "evidence.jsonl", "count": 1}}}]
    task = SPSubagentTask("inspect", "perception", "For run " + run + " read evidence.", "research")
    scoped_profile.prepare_task(task)
    assert task.input_refs == []
