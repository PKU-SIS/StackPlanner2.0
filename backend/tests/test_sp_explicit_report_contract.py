"""Only typed explicit references create report obligations; source terms are free."""

from langchain_core.messages import HumanMessage

from deerflow.sp.actions import ActionType, SPAction, build_default_action_router
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus, SPSubagentTask


def execute(tmp_path, *, host=True, forged=False, body="M11 run-real SHA-256 TimesNewRomanPS-BoldMT E000001 d001"):
    from deerflow.sp.subagents.adapter import SPReportReferenceContract

    contract = SPReportReferenceContract(required_literals=("M11", "run-real"), catalogs=((r"\bE\d{6}\b", ("E000001",)), (r"\bd\d{3}\b", ("d001",))))

    class Executor:
        def execute(self, task):
            if host:
                task.report_reference_contract = contract
            assert "report_reference_contract" not in task.protocol_payload()
            return SPSubagentResult(
                SPSubagentStatus.COMPLETED, result="written", artifact_type="report", artifact_content="# 报告\n\n" + body, artifact_metadata={"completion_status": "complete", "report_reference_contract": {"required_literals": []}}
            )

    result = build_default_action_router(delegate_executor=Executor()).execute(
        SPAction.create(
            ActionType.DELEGATE,
            action_id="a",
            target_agent="reporter",
            stage="reporting",
            reason="report",
            task="保留 TimesNewRomanPS-BoldMT，删除 SHA-256",
            metadata={"report_reference_contract": {"required_literals": []}} if forged else {},
        ),
        state={"thread_data": {"outputs_path": str(tmp_path / "outputs")}, "messages": [HumanMessage(content="审核并输出报告")]},
        thread_id="t",
        run_id="r",
    )
    return result.state_update["sp_current_artifact_refs"]["report"]["metadata"]


def test_explicit_contract_does_not_promote_font_substrings_or_deletion_words(tmp_path):
    m = execute(tmp_path)
    assert m["completion_status"] == "complete"
    assert m["quality_checks"]["required_literal_tokens"]["required"] == ["M11", "run-real"]
    assert m["quality_checks"]["literal_token_provenance"]["introduced_tokens"] == []


def test_unknown_real_reference_is_rejected_and_required_run_is_enforced(tmp_path):
    m = execute(tmp_path, body="M11 SHA-256 E999999 d999")
    assert m["completion_status"] == "partial"
    assert m["quality_checks"]["required_literal_tokens"]["missing"] == ["run-real"]
    assert m["quality_checks"]["literal_token_provenance"]["introduced_tokens"] == ["E999999", "d999"]


def test_model_metadata_does_not_enable_explicit_contract(tmp_path):
    m = execute(tmp_path, host=False, forged=True)
    assert m["quality_checks"]["required_literal_tokens"]["required"] != ["M11", "run-real"]


def test_typed_task_contract_defaults_off():
    assert SPSubagentTask("a", "reporter", "report", "report").report_reference_contract is None
