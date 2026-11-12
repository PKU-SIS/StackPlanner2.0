"""Recovery identities derived from real source receipts, never role selection."""

import re
from pathlib import PurePosixPath

from .knowledge import fingerprint


def tool_scope_refs(name, result, arguments):
    if not isinstance(result, dict) or result.get("status") in {"error", "already_returned"}:
        return set()
    if name.startswith("audit_m") and result.get("manifest_sha256"):
        return {"code:" + str(result["rule_id"]) + ":" + result["manifest_sha256"]}
    if name == "audit_m01_index" and result.get("navigation_only") and result.get("audit_run_id"):
        return {"code-index:" + result["audit_run_id"] + ":" + fingerprint(result.get("filters", {})) + ":" + str(result.get("offset", 0))}
    if name == "audit_m01_candidates" and result.get("audit_run_id"):
        return {"code-candidate:" + result["audit_run_id"] + ":" + row["candidate_id"] for row in result.get("candidates", [])}
    if name == "audit_read_complete" and result.get("complete") is True:
        return {"code:" + str(result["summary"]["rule_id"]) + ":" + result["summary"]["manifest_sha256"]}
    if name == "audit_read_result" and result.get("collection"):
        return {f"code-row:{result['run_id']}:{result['collection']}:{result['offset'] + i}" for i, _ in enumerate(result.get("items", []))}
    if name in {"knowledge_read", "ragflow_retrieve"} and result.get("document_id"):
        return {"kb:" + result["document_id"] + ":" + str(row.get("id", row.get("chunk_id"))) for row in result.get("items", result.get("chunks", []))}
    if name == "read_file":
        path = str(result.get("file_path", ""))
        cid = result.get("source_candidate_id")
        run = result.get("audit_run_id")
        if (
            result.get("source") == "verified_current_calculation_source"
            and result.get("source_sha256")
            and cid
            and run
            and re.fullmatch(r"/mnt/user-data/outputs/bid_audit/[^/]+/m01_candidates/C\d{6}\.json", path)
            and PurePosixPath(path).stem == cid
            and PurePosixPath(path).parts[-3] == run
        ):
            return {"code-candidate:" + run + ":" + cid}
        parts = PurePosixPath(path).parts
        if path.startswith("/knowledge/") and len(parts) == 4 and parts[-1] == "parsed.md" and result.get("text"):
            return {"kb-window:" + parts[-2] + ":" + str(result.get("offset", 0)) + ":" + fingerprint(result["text"])}
        if result.get("document_id") and result.get("text"):
            return {"original-window:" + result["document_id"] + ":" + str(result.get("offset", 0)) + ":" + fingerprint(result["text"])}
    if name == "project_read":
        return {f"original:{result['document_id']}:{row.get('physical_page')}:{row.get('text_offset', 0)}:" + fingerprint(row.get("text", "")) for row in result.get("pages", [])}
    if name == "project_search":
        return {"original-hit:" + str(row.get("document_id")) + ":" + str(row.get("physical_page")) + ":" + fingerprint(row.get("excerpt", "")) for row in result.get("items", [])}
    return set()


def recovery_scope_key(adapter, task):
    refs = adapter.scope_refs(task.action_id)
    if refs:
        return "audit-sources:" + fingerprint({"rule": adapter.rule_id, "sources": sorted(refs)})
    # A retry may work from a persisted stage Artifact without fetching again.
    # Inherit only the trusted identity of actual selected current artifacts.
    context = getattr(task, "context_refs", {})
    artifacts = context.get("artifact_refs", {})
    if not isinstance(artifacts, dict):
        return None
    sources = getattr(task, "metadata", {}).get("source_artifact_ids", [])
    sources = sources if isinstance(sources, list) else []
    primary = {s for s in getattr(task, "input_refs", []) if isinstance(s, str)}
    selected = {s for s in [*primary, *sources] if isinstance(s, str)}
    rows = [r for k, r in artifacts.items() if k != "_history" and isinstance(r, dict)] + artifacts.get("_history", [])
    primary_scopes = {
        r.get("metadata", {}).get("_sp_recovery_scope_key") for r in rows if isinstance(r, dict) and primary & {r.get("artifact_id"), r.get("virtual_path"), r.get("artifact_url")} and r.get("metadata", {}).get("_sp_recovery_scope_key")
    }
    if len(primary_scopes) == 1:
        return next(iter(primary_scopes))
    scopes = {
        r.get("metadata", {}).get("_sp_recovery_scope_key") for r in rows if isinstance(r, dict) and selected & {r.get("artifact_id"), r.get("virtual_path"), r.get("artifact_url")} and r.get("metadata", {}).get("_sp_recovery_scope_key")
    }
    return next(iter(scopes)) if len(scopes) == 1 else None
