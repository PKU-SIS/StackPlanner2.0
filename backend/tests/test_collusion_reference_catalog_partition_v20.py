"""Large real evidence-ID sets fit native bounds without admitting fake IDs."""

import re

from test_collusion_audit import scoped_profile as scoped_profile

from app.experiments.collusion_audit.identifier_provenance import evidence_reference_catalogs
from deerflow.sp.subagents.adapter import SPReportReferenceContract


def test_small_catalog_preserves_existing_contract_and_deduplicates():
    values = ("E000001", "E000002", "E000001")
    catalogs = evidence_reference_catalogs(values)
    assert catalogs == ((r"(?<![A-Za-z0-9_])E\d{6}(?![A-Za-z0-9_])", ("E000001", "E000002")),)
    SPReportReferenceContract(catalogs=catalogs)


def test_large_exact_allowed_set_has_disjoint_complete_detection_domain():
    ids = tuple(f"E{i:06d}" for i in range(1, 62802))
    catalogs = evidence_reference_catalogs(ids)
    SPReportReferenceContract(catalogs=catalogs)
    assert len(catalogs) == 4
    assert set().union(*(set(values) for _, values in catalogs)) == set(ids)
    assert sum(len(values) for _, values in catalogs) == len(ids)
    compiled = [(re.compile(pattern), set(values)) for pattern, values in catalogs]
    # Known IDs are all permitted by exactly one catalog. Missing IDs throughout
    # the whole six-digit domain still match one rejecting catalog.
    probes = [*ids, "E000000", "E062802", "E999999", "E٠١٢٣٤٥", "E0١2345", "E01٢345"]
    probes += [f"E{prefix:02d}{suffix:04d}" for prefix in range(100) for suffix in [0, 5000, 9999]]
    truth = set(ids)
    for token in probes:
        matched = [values for pattern, values in compiled if pattern.fullmatch(token)]
        assert len(matched) == 1
        assert (token in matched[0]) == (token in truth)
    for pattern, _ in compiled:
        assert not pattern.search("prefix_E000001_suffix")


def test_profile_builds_large_contract_without_changing_task_or_native_limits(scoped_profile):
    import json

    from deerflow.sp.subagents import SPSubagentTask

    run_id = scoped_profile.rule_id + "_20261109T000000_0123456789"
    scoped_profile.adapters.runs = [{"run_id": run_id, "status": "ok"}]
    directory = scoped_profile.adapters.result_root(run_id)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "evidence.jsonl").open("w") as stream:
        for i in range(1, 62802):
            stream.write(json.dumps({"evidence_id": f"E{i:06d}"}) + "\n")
    task = SPSubagentTask("a", "reporter", "report the actual evidence", "test")
    task.context_refs["latest_user_input"] = scoped_profile.user_input()
    scoped_profile.explicit_report_contract(task, [{"audit_run_id": run_id}])
    assert task.subagent_type == "reporter" and task.task == "report the actual evidence"
    assert len(task.report_reference_contract.catalogs) == 7
    assert all(len(values) <= 20000 for _, values in task.report_reference_contract.catalogs)
    assert task.report_reference_contract.required_literals == (scoped_profile.rule_id, run_id)
