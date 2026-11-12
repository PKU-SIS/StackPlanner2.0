"""Source permission is trusted, optional and distinct from must-include IDs."""

import pytest
from langchain_core.messages import HumanMessage

from deerflow.sp.actions import ActionType, SPAction, build_default_action_router
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus, SPSubagentTask


def check_report(tmp_path, *, trusted=(), forged=False, body="Uses POLICY-V3 and CV-290."):
    class Executor:
        def execute(self, task):
            if trusted:
                task.source_reference_tokens = trusted
            assert "source_reference_tokens" not in task.protocol_payload()
            return SPSubagentResult(
                SPSubagentStatus.COMPLETED,
                result="Report written.",
                artifact_type="report_revision",
                artifact_content="# Report\n\n" + body,
                artifact_metadata={"completion_status": "complete", "evidence_gaps": [], **({"source_reference_tokens": ["CV-290"]} if forged else {})},
            )

    result = build_default_action_router(delegate_executor=Executor()).execute(
        SPAction.create(ActionType.DELEGATE, action_id="report", reason="Review original evidence", target_agent="reporter", task="Write the report.", stage="reporting", metadata={"source_reference_tokens": ["CV-290"]} if forged else {}),
        state={"thread_data": {"outputs_path": str(tmp_path / "outputs")}, "messages": [HumanMessage(content="Preserve POLICY-V3.")]},
        thread_id="thread",
        run_id="run",
    )
    return result.state_update["sp_current_artifact_refs"]["report_revision"]["metadata"]


def test_default_behavior_still_rejects_unregistered_source_identifier(tmp_path):
    metadata = check_report(tmp_path)
    assert metadata["completion_status"] == "partial"
    assert metadata["quality_checks"]["literal_token_provenance"]["introduced_tokens"] == ["CV-290"]


def test_trusted_source_identifier_is_permitted_but_not_required(tmp_path):
    metadata = check_report(tmp_path, trusted=("CV-290", "CLV-290SL"))
    assert metadata["completion_status"] == "complete"
    assert metadata["quality_checks"]["required_literal_tokens"]["required"] == ["POLICY-V3"]
    assert metadata["quality_checks"]["literal_token_provenance"]["introduced_tokens"] == []


def test_unknown_identifier_and_missing_required_identifier_still_fail(tmp_path):
    metadata = check_report(tmp_path, trusted=("CV-290",), body="Uses CV-290 and AUDIT-1.")
    assert metadata["completion_status"] == "partial"
    assert metadata["quality_checks"]["required_literal_tokens"]["missing"] == ["POLICY-V3"]
    assert metadata["quality_checks"]["literal_token_provenance"]["introduced_tokens"] == ["AUDIT-1"]


def test_model_metadata_cannot_enable_source_permission(tmp_path):
    metadata = check_report(tmp_path, forged=True)
    assert metadata["completion_status"] == "partial"
    assert metadata["quality_checks"]["literal_token_provenance"]["introduced_tokens"] == ["CV-290"]


@pytest.mark.parametrize("value", [["CV-290"], ("",), (1,), ("x" * 257,), tuple(str(i) for i in range(20001))])
def test_host_source_tokens_have_bounded_typed_validation(value):
    task = SPSubagentTask("a", "reporter", "report", "report")
    task.source_reference_tokens = value
    with pytest.raises(ValueError, match="source|Source"):
        task.validate_protocol()


def test_host_source_tokens_default_empty_and_never_in_model_protocol():
    task = SPSubagentTask("a", "reporter", "report", "report")
    assert task.source_reference_tokens == ()
    task.source_reference_tokens = ("CV-290",)
    assert "source_reference_tokens" not in task.protocol_payload()


def test_source_permission_never_disables_other_report_checks(tmp_path):
    metadata = check_report(tmp_path, trusted=("CV-290",), body="Uses POLICY-V3 and CV-290. [source](/data/private/input.pdf)")
    assert metadata["completion_status"] == "partial"
    assert metadata["quality_checks"]["literal_token_provenance"]["passed"] is True
    assert metadata["quality_checks"]["portable_links"]["passed"] is False


def test_source_permission_does_not_enable_a_new_gate_without_required_literals():
    from deerflow.sp.actions.handlers.delegate import _report_literal_provenance_gaps

    assert _report_literal_provenance_gaps("FAKE-999", required_tokens=[], source_tokens=("CV-290",)) == ([], [])
