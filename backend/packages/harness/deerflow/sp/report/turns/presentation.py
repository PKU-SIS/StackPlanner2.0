"""Read-only dialogue projection. No lazy freezing, activation or publication."""

from collections.abc import Mapping

from deerflow.sp.report import store as native
from deerflow.sp.report.ledger import ReportValidationError
from deerflow.sp.report.turns.adapter import opted_request
from deerflow.sp.report.turns.store import ReportTurnStore


def dialogue_view(state, *, user_id, thread_id):
    """Expose program-recorded turn outcomes separately from native report status.

    The caller must authorize thread ownership before supplying checkpoint state.
    This is a metadata projection, not a new integrity/factual acceptance check.
    """
    empty = {"available": False, "binding": None, "head_version_id": None, "active_version_id": None, "versions": [], "current_turn": None}
    if not isinstance(state, Mapping) or not state.get("thread_data"):
        return empty
    outputs = native.outputs_path_from_state(state)
    store = ReportTurnStore(outputs, user_id=user_id, thread_id=thread_id)
    index = store.index()  # Enforces owner even if current native pointer is disabled.
    if not index["head"]:
        context = native.load_report_context(state)
        if context.status == "error":
            raise ReportValidationError("report context unavailable")
        if context.status != "delivered":
            return empty
        store._accepted(store._paths(context.report_id))
        return {**empty, "available": True, "binding": {"enabled": True, "report_id": context.report_id}}

    active = index.get("active_version") or index["head"]
    if active not in index["versions"] or index["head"] not in index["versions"]:
        raise ReportValidationError("invalid accepted version reference")
    versions = []
    for version_id, ref in index["versions"].items():
        turn = store.inspect(version_id)
        if turn["status"] != "accepted":
            raise ReportValidationError("version is not accepted")
        versions.append({"version_id": version_id, "report_id": ref["report_id"], "base_version_id": ref.get("base_version_id"), "is_head": version_id == index["head"], "is_active": version_id == active})

    current = None
    request = opted_request(state)
    if request is not None:
        message, binding = request
        if binding["report_id"] in {ref["report_id"] for ref in index["versions"].values()}:
            from deerflow.sp.report.hooks import _message_id

            turn_id = index["requests"].get(_message_id(message))
            if turn_id:
                turn = store.inspect(turn_id)
                # Deliberately whitelist fields: diagnostics, raw queries and host
                # paths are not part of a public status response.
                current = {key: turn.get(key) for key in ("turn_id", "message_id", "status", "mode", "report_id", "base_version_id")}
                current["native_delivery_created"] = turn["status"] == "accepted" and turn["mode"] != "origin"
                current["response_only"] = bool(turn.get("response_only"))
    return {**empty, "available": True, "binding": {"enabled": True, "report_id": index["versions"][active]["report_id"]}, "head_version_id": index["head"], "active_version_id": active, "versions": versions, "current_turn": current}
