"""Reference-ID permissions from observed source facts, never model text."""

import re

from deerflow.sp.actions.handlers.delegate import _REPORT_REFERENCE_TOKEN_PATTERN


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


def source_reference_tokens(tool, result, arguments):
    """Select factual fields of actual tool receipts; exclude query/draft echoes."""
    if not isinstance(result, dict) or result.get("status") in ("error", "already_returned"):
        return ()
    facts = []
    if re.fullmatch(r"audit_m\d{2}", tool):
        packet = result.get("complete_code_result") or {}
        facts = [result.get("run_id"), packet.get("collections"), packet.get("sources"), result.get("result_navigation"), result.get("m01_navigation")]
    elif tool == "audit_m01_candidates":
        facts = [{"candidate": row.get("candidate"), "evidence": row.get("evidence")} for row in result.get("candidates", [])]
    elif tool == "audit_read_complete":
        facts = [result.get("collections"), result.get("sources")]
    elif tool == "audit_read_result":
        facts = [result.get("items")]
    elif tool in {"ragflow_retrieve", "knowledge_read"}:
        rows = result.get("chunks" if tool == "ragflow_retrieve" else "items") or []
        facts = [row.get("content") or row.get("content_with_weight") for row in rows if isinstance(row, dict)]
    elif tool == "project_search":
        facts = [row.get("excerpt") for row in result.get("items", []) if isinstance(row, dict)]
    elif tool == "project_read":
        facts = [row.get("text") for row in result.get("pages", []) if isinstance(row, dict)]
    elif tool == "read_file":
        path = result.get("file_path", "")
        original = bool(result.get("document_id")) and not path.startswith("/mnt/user-data/outputs/")
        knowledge = re.fullmatch(r"/knowledge/d\d+/parsed\.md", path) and result.get("source") == "persistent_MinerU_snapshot"
        calculation = result.get("source") == "verified_current_calculation_source" and bool(result.get("source_sha256"))
        if path == arguments.get("file_path") and (original or knowledge or calculation):
            facts = [result.get("text")]
    return tuple(sorted({token for text in _strings(facts) for token in _REPORT_REFERENCE_TOKEN_PATTERN.findall(text) if len(token) <= 256}))


def evidence_reference_catalogs(values):
    """Partition large exact allowlists into disjoint native-bounded catalogs."""
    unique = tuple(dict.fromkeys(values))
    if any(not isinstance(value, str) or not re.fullmatch(r"E[0-9]{6}", value) for value in unique):
        raise ValueError("Evidence catalogs require canonical observed E IDs")
    boundary_left = r"(?<![A-Za-z0-9_])E"
    boundary_right = r"(?![A-Za-z0-9_])"
    if len(unique) <= 20000:
        return ((boundary_left + r"\d{6}" + boundary_right, unique),)
    buckets = {f"{prefix:02d}": [] for prefix in range(100)}
    for value in unique:
        buckets[value[1:3]].append(value)
    groups = []
    prefixes, allowed = [], []
    for prefix, bucket in buckets.items():
        if len(allowed) + len(bucket) > 20000:
            groups.append((prefixes, tuple(allowed)))
            prefixes, allowed = [], []
        prefixes.append(prefix)
        allowed.extend(bucket)
    groups.append((prefixes, tuple(allowed)))
    catalogs = []
    for index, (prefixes, allowed) in enumerate(groups):
        alternatives = "|".join(prefixes)
        if index == len(groups) - 1:
            # Preserve the legacy Unicode-digit rejection domain as well: these
            # spellings are never canonical source IDs, but cannot evade checks.
            alternatives += r"|(?![0-9]{2})\d{2}"
        pattern = boundary_left + "(?:" + alternatives + r")\d{4}" + boundary_right
        catalogs.append((pattern, allowed))
    if len(catalogs) > 29:
        raise ValueError("Evidence catalogs exceed native32 catalogs with3 other identifier domains; no IDs were truncated")
    return tuple(catalogs)
