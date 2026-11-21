"""Align a bound CLAUSE model prompt with its existing public final transport."""

from collections.abc import Mapping, Sequence
from typing import Any

from deerflow.sp.evidence_quotes import clause_public_audit_scope

# This exact, harness-authored legacy control block occurs in prepare_task's
# prompt. Match it whole: never heuristically delete user/source instructions.
_LEGACY_DELIVERY = (
    "Delegate the document audit and final JSON file to reporter with the clause skill. "
    "The required deliverable is a UTF-8 .json file containing only the requested JSON array. "
    "The reporter must write /mnt/user-data/outputs/clause_answer-<action_id>.json, replacing "
    "<action_id> with its exact current delegation action_id. This action-specific filename "
    "is required by native artifact ownership checks. Read back and verify the entire file. "
    "Each array item must have exactly two string fields, text and explanation; text must "
    "be nonblank. No findings means []. No Markdown, code fences, report wrapper, or prose "
    "may appear outside the array. The input contract and recovered tool evidence are not answers. "
    "The array-only restriction applies to the delivered file bytes, not to internal SP control messages. "
    "After the file is successfully written and read back as valid, immediately return the internal "
    "SP specialist envelope; do not rewrite an unchanged valid file or repeat completed checks. "
    "Keep that envelope separate from the answer file: after writing, "
    "set artifact_content to null and artifact_metadata.created_paths to the actual JSON path. "
    "Central must select that registered .json file in native FINISH using its exact path "
    "as final_artifact_ref and its registered artifact family as required_artifact_type, "
    "with allow_without_artifact=false. A task brief, report summary, raw document, "
    "or incomplete artifact must never be selected as the final answer."
)
_PUBLIC_DELIVERY = (
    "Delegate the complete document audit to reporter with the clause skill. "
    "For a successful completed audit, the reporter's final message must be the complete raw JSON array itself. "
    "Each item must have exactly two string fields, text and explanation; text must be nonblank. "
    "Use [] only after a completed substantive audit finds no supported issue. "
    "Return no Markdown, code fences, report wrapper, summary, internal success envelope or file receipt. "
    "Native file tools remain available for source inspection and an optional draft; a draft write is not the final delivery. "
    "After any optional draft write or review correction, return the complete current corrected array in the final message, "
    "preserving every supported finding and original quotation. Do not revert to an earlier raw array or substitute a file receipt. "
    "The native host validates and persists that original final value as the current-action artifact without changing findings. "
    "Central selects the artifact actually registered by that successful delivery through native FINISH. "
    "The input contract and recovered tool evidence are not answers. "
    "If a mandatory source or audit requirement remains unresolved, return the ordinary partial/blocked failure envelope with concrete evidence_gaps."
)


def align_clause_delivery_payload(payload: dict[str, Any], *, contract: Mapping[str, Any] | None, skill_names: Sequence[str] | None, auditor_role: str) -> dict[str, Any]:
    """Edit only detached prompt control fields, leaving the host task untouched."""
    if not isinstance(contract, Mapping) or not isinstance(skill_names, list | tuple) or not clause_public_audit_scope(contract, skill_names, auditor_role):
        return payload

    def align(container, key):
        value = container.get(key)
        if isinstance(value, str):
            container[key] = value.replace(_LEGACY_DELIVERY, _PUBLIC_DELIVERY)

    for key in ("task", "description", "expected_output"):
        align(payload, key)
    protocol = payload.get("protocol")
    if isinstance(protocol, dict):
        align(protocol, "objective")
    context = payload.get("context_refs")
    public = context.get("task_contract") if isinstance(context, dict) else None
    if isinstance(public, dict):
        for key in ("original_issue", "original_request"):
            align(public, key)
    return payload
