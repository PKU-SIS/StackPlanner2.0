"""Serialize explicit Outline decisions without guessing business intent."""

from copy import deepcopy

from deerflow.sp.report.ledger import ReportValidationError


def serialize_draft_choices(tree, ledger, *, keep_nodes, rewrite_nodes, drop_nodes, reason):
    if tree.get("draft_dispositions"):
        raise ReportValidationError("Use node choices or draft_dispositions, not both")
    current = {}
    for draft in ledger.payload.get("drafts") or []:
        if not draft.get("is_current"):
            continue
        node_id = draft["node_id"]
        if node_id in current:
            raise ReportValidationError("Multiple current drafts for one node; no decision inferred")
        current[node_id] = draft["draft_id"]
    selected = {}
    for action, nodes in [("keep", keep_nodes), ("rewrite", rewrite_nodes), ("drop", drop_nodes)]:
        if nodes is None:
            nodes = []
        if not isinstance(nodes, list) or any(not isinstance(n, str) or not n.strip() for n in nodes):
            raise ReportValidationError("Node choices must be lists of nonempty node IDs")
        for node_id in nodes:
            if node_id in selected:
                raise ReportValidationError("Duplicate or conflicting node choices")
            selected[node_id] = action
    if set(selected) != set(current):
        raise ReportValidationError("Node choices must cover exactly all current drafts; unknown/missing choices are not inferred")
    if current and (not isinstance(reason, str) or not reason.strip()):
        raise ReportValidationError("Explicit node choices require a nonempty decision_reason")
    present = {n["node_id"] for n in tree.get("nodes") or [] if n.get("kind") == "writable"}
    for node_id, action in selected.items():
        if (action == "drop") == (node_id in present):
            raise ReportValidationError("Use drop only for a removed node; keep/rewrite require a present writable node")
    result = deepcopy(tree)
    result["draft_dispositions"] = [{"node_id": node_id, "draft_id": draft_id, "action": selected[node_id], "reason": reason.strip()} for node_id, draft_id in sorted(current.items())]
    return result
