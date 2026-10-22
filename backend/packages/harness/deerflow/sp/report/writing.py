"""P2 node draft, write-request, and node-feedback state transitions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from deerflow.sp.report import store as report_store
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError, utc_now_iso

WRITE_REQUEST_STATUSES = frozenset({"pending", "in_flight", "feedback_pending", "blocked", "technical_failed", "completed", "superseded"})
DRAFT_STATUSES = frozenset({"current", "rewritten", "dropped"})
FEEDBACK_STATUSES = frozenset({"open", "handed_off", "closed"})
DISPOSITION_ACTIONS = frozenset({"revise", "needs_research", "keep", "dismiss"})
DRAFT_ACTIONS = frozenset({"keep", "rewrite", "drop"})
FEEDBACK_KINDS = frozenset({"material_missing", "spec_conflict", "evidence_mismatch", "other"})


def write_requests(ledger: ReportLedger) -> list[dict[str, Any]]:
    raw = ledger.payload.get("write_requests")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def write_request(ledger: ReportLedger, key: str) -> dict[str, Any] | None:
    return next((item for item in write_requests(ledger) if item.get("key") == key), None)


def require_write_request(ledger: ReportLedger, key: str) -> dict[str, Any]:
    item = write_request(ledger, str(key))
    if item is None:
        raise ReportValidationError(f"unknown write request: {key}")
    return item


def drafts(ledger: ReportLedger) -> list[dict[str, Any]]:
    raw = ledger.payload.get("drafts")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def draft(ledger: ReportLedger, draft_id: str) -> dict[str, Any] | None:
    return next((item for item in drafts(ledger) if item.get("draft_id") == draft_id), None)


def current_draft(ledger: ReportLedger, node_id: str) -> dict[str, Any] | None:
    return next((item for item in reversed(drafts(ledger)) if item.get("node_id") == node_id and item.get("is_current") is True), None)


def feedback_entries(ledger: ReportLedger) -> list[dict[str, Any]]:
    raw = ledger.payload.get("feedback")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def next_feedback_id(ledger: ReportLedger) -> str:
    return report_store.next_identifier("f", [str(item.get("feedback_id")) for item in feedback_entries(ledger)])


def next_draft_id(ledger: ReportLedger) -> str:
    return report_store.next_identifier("d", [str(item.get("draft_id")) for item in drafts(ledger)])


def next_action(entry: Mapping[str, Any]) -> str:
    status = str(entry.get("status") or "")
    if status in {"pending", "technical_failed"}:
        return "delegate_writer"
    if status == "in_flight":
        return "wait"
    if status in {"feedback_pending", "blocked"}:
        return "delegate_outline"
    return "none"


def begin_writer_attempt(ledger: ReportLedger, key: str, *, action_id: str, node_id: str, run_id: str | None = None) -> dict[str, Any]:
    request = require_write_request(ledger, key)
    if list(request.get("node_ids") or []) != [node_id]:
        raise ReportValidationError(f"write request {key} does not target node {node_id}")
    status = str(request.get("status") or "")
    if status not in {"pending", "technical_failed"}:
        raise ReportValidationError(f"write request {key} is not dispatchable while status={status}")
    draft_id = next_draft_id(ledger)
    path = ledger.paths.draft_file(node_id, draft_id)
    now = utc_now_iso()
    request["status"] = "in_flight"
    request["last_action_id"] = action_id
    request["last_run_id"] = str(run_id) if run_id else None
    request["last_draft_id"] = draft_id
    request["updated_at"] = now
    request.setdefault("attempts", []).append({"action_id": action_id, "run_id": str(run_id) if run_id else None, "status": "in_flight", "draft_id": draft_id, "at": now})
    _refresh(ledger)
    prior = current_draft(ledger, node_id)
    if prior is None and request.get("last_draft_id"):
        prior = draft(ledger, str(request["last_draft_id"]))
    if prior is None:
        # An explicit rewrite disposition retires the old draft but must still
        # be the source of the next writing round, even when that round uses a
        # new write key. Keep the source in the ledger instead of inferring it
        # from a non-existent current draft.
        for item in reversed(drafts(ledger)):
            if item.get("node_id") == node_id and item.get("disposition") == "rewrite" and not item.get("rewrite_source_consumed_by"):
                prior = item
                break
    if prior is not None:
        request["prior_draft_id"] = prior.get("draft_id")
    return {
        "key": key,
        "node_id": node_id,
        "draft_id": draft_id,
        "draft_path": path,
        "virtual_path": ledger.paths.virtual(path),
        "prior_draft": prior,
        "request": request,
    }


def record_writer_feedback(
    ledger: ReportLedger,
    *,
    key: str,
    action_id: str,
    node_id: str,
    feedback_items: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    request = require_write_request(ledger, key)
    normalized = _normalize_feedback(feedback_items, ledger, node_id=node_id, key=key)
    if not normalized:
        raise ReportValidationError("node_feedback must contain at least one item")
    now = utc_now_iso()
    records: list[dict[str, Any]] = []
    for item in normalized:
        feedback_id = next_feedback_id(ledger)
        record = {
            "feedback_id": feedback_id,
            "write_key": key,
            "node_id": node_id,
            "action_id": action_id,
            "kind": item["kind"],
            "message": item["message"],
            "material_ids": item["material_ids"],
            "requirement_ids": item["requirement_ids"],
            "suggested_action": item["suggested_action"],
            "status": "open",
            "created_at": now,
        }
        ledger.payload.setdefault("feedback", []).append(record)
        records.append(record)
    request["status"] = "feedback_pending"
    request["updated_at"] = now
    _finish_attempt(request, action_id, "feedback")
    _refresh(ledger)
    return records


def accept_draft(
    ledger: ReportLedger,
    *,
    key: str,
    action_id: str,
    node_id: str,
    draft_id: str,
    virtual_path: str,
    host_path: str,
    content: str,
    basis: Mapping[str, Any],
    feedback_wanted: bool,
) -> dict[str, Any]:
    request = require_write_request(ledger, key)
    if request.get("status") != "in_flight" or request.get("last_action_id") != action_id:
        raise ReportValidationError(f"write request {key} is not in flight for action {action_id}")
    prior = current_draft(ledger, node_id)
    prior_was_rewrite_source = prior is not None and prior.get("is_current") is not True and prior.get("disposition") == "rewrite"
    now = utc_now_iso()
    if prior is not None:
        prior["is_current"] = False
        prior["status"] = "rewritten"
        prior["superseded_at"] = now
        prior["superseded_by"] = draft_id
    elif request.get("prior_draft_id"):
        prior = draft(ledger, str(request["prior_draft_id"]))
        prior_was_rewrite_source = prior is not None and prior.get("is_current") is not True and prior.get("disposition") == "rewrite"
    record = {
        "draft_id": draft_id,
        "write_key": key,
        "node_id": node_id,
        "action_id": action_id,
        "path": host_path,
        "virtual_path": virtual_path,
        "chars": len(content),
        "lines": report_store.line_count(content),
        "sha256": report_store.sha256_text(content),
        "generated_fingerprint": basis.get("fingerprint"),
        "generated_basis": dict(basis),
        "applicable_fingerprint": basis.get("fingerprint"),
        "confirmed_fingerprint": None,
        "review_status": "open" if feedback_wanted else "none",
        "is_current": True,
        "status": "current",
        "rewrite_of": prior.get("draft_id") if prior_was_rewrite_source and prior is not None else None,
        "created_at": now,
    }
    if prior_was_rewrite_source and prior is not None:
        prior["rewrite_source_consumed_by"] = draft_id
    ledger.payload.setdefault("drafts", []).append(record)
    request["status"] = "completed"
    request["draft_id"] = draft_id
    request["updated_at"] = now
    _finish_attempt(request, action_id, "completed", draft_id=draft_id)
    _refresh(ledger)
    return record


def fail_writer_attempt(ledger: ReportLedger, *, key: str, action_id: str, error: str, stop_reason: str | None = None) -> None:
    request = write_request(ledger, key)
    if request is None:
        return
    if request.get("status") == "completed":
        return
    request["status"] = "technical_failed"
    request["last_error"] = str(error)[:2000]
    request["last_stop_reason"] = str(stop_reason)[:100] if stop_reason else None
    request["updated_at"] = utc_now_iso()
    _finish_attempt(request, action_id, "technical_failed", stop_reason=stop_reason)
    _refresh(ledger)


def apply_feedback_dispositions(ledger: ReportLedger, tree: Mapping[str, Any]) -> list[dict[str, Any]]:
    open_feedback = [item for item in feedback_entries(ledger) if item.get("status") == "open"]
    dispositions = tree.get("feedback_dispositions")
    items = [item for item in dispositions if isinstance(item, Mapping)] if isinstance(dispositions, list) else []
    by_id: dict[str, dict[str, Any]] = {}
    for raw in items:
        feedback_id = str(raw.get("feedback_id") or "").strip()
        if not feedback_id:
            raise ReportValidationError("feedback_dispositions[].feedback_id is required")
        if feedback_id in by_id:
            raise ReportValidationError(f"duplicate feedback disposition: {feedback_id}")
        disposition = str(raw.get("disposition") or "").strip().lower()
        if disposition not in DISPOSITION_ACTIONS:
            raise ReportValidationError(f"unsupported feedback disposition: {disposition!r}")
        reason = str(raw.get("reason") or "").strip()
        if not reason:
            raise ReportValidationError(f"feedback disposition {feedback_id} requires a reason")
        by_id[feedback_id] = {"disposition": disposition, "reason": reason, "gap_id": raw.get("gap_id")}
    missing = [item["feedback_id"] for item in open_feedback if item["feedback_id"] not in by_id]
    if missing:
        raise ReportValidationError(f"undisposed open node feedback: {', '.join(missing)}")
    applied: list[dict[str, Any]] = []
    all_feedback = feedback_entries(ledger)
    for feedback_id, decision in by_id.items():
        record = next((item for item in all_feedback if item.get("feedback_id") == feedback_id), None)
        if record is None:
            raise ReportValidationError(f"feedback disposition references unknown feedback: {feedback_id}")
        disposition = decision["disposition"]
        gap_id = decision.get("gap_id")
        if record.get("status") != "open":
            if record.get("disposition") == disposition and str(record.get("disposition_reason") or "") == decision["reason"] and str(record.get("gap_id") or "") == str(gap_id or ""):
                applied.append(dict(record))
                continue
            raise ReportValidationError(f"feedback disposition references already-disposed feedback: {feedback_id}")
        request = write_request(ledger, str(record.get("write_key")))
        now = utc_now_iso()
        gap = None
        if disposition == "needs_research":
            gap_id = str(gap_id or "").strip()
            if not gap_id:
                raise ReportValidationError("needs_research requires gap_id")
            gap = ledger.require_gap(gap_id)
            record["gap_id"] = gap_id
            record["status"] = "handed_off"
            if request is not None:
                request["status"] = "blocked"
                request["updated_at"] = now
        else:
            record["status"] = "closed"
            if request is not None:
                request["status"] = "blocked"
                request["updated_at"] = now
        record["disposition"] = disposition
        record["disposition_reason"] = decision["reason"]
        record["disposed_at"] = now
        record["disposition_gap_status"] = gap.get("status") if gap is not None else None
        applied.append(dict(record))
    _refresh(ledger)
    return applied


def apply_draft_dispositions(
    ledger: ReportLedger,
    tree: Mapping[str, Any],
    fingerprints: Mapping[str, str],
) -> list[dict[str, Any]]:
    current = [item for item in drafts(ledger) if item.get("is_current") is True]
    nodes = {str(node.get("node_id")): node for node in tree.get("nodes") or [] if isinstance(node, Mapping)}
    required: dict[str, str] = {}
    for item in current:
        draft_id = str(item.get("draft_id"))
        node_id = str(item.get("node_id"))
        if item.get("review_status") == "open":
            required[draft_id] = "feedback_wanted review is open"
            continue
        if node_id not in nodes:
            required[draft_id] = f"node {node_id} was removed"
            continue
        if str(item.get("applicable_fingerprint") or "") != str(fingerprints.get(node_id) or ""):
            required[draft_id] = f"node {node_id} specification or referenced context changed"

    raw = tree.get("draft_dispositions")
    items = [item for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else []
    by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        draft_id = str(item.get("draft_id") or "").strip()
        if not draft_id:
            raise ReportValidationError("draft_dispositions[].draft_id is required")
        if draft_id in by_id:
            raise ReportValidationError(f"duplicate draft disposition: {draft_id}")
        action = str(item.get("action") or "").strip().lower()
        if action not in DRAFT_ACTIONS:
            raise ReportValidationError(f"unsupported draft disposition action: {action!r}")
        reason = str(item.get("reason") or "").strip()
        if not reason:
            raise ReportValidationError(f"draft disposition {draft_id} requires a reason")
        by_id[draft_id] = {"action": action, "reason": reason}
    missing = [f"{draft_id}: {reason}" for draft_id, reason in required.items() if draft_id not in by_id]
    if missing:
        raise ReportValidationError("draft dispositions required: " + "; ".join(missing))

    applied: list[dict[str, Any]] = []
    for draft_id, decision in by_id.items():
        record = draft(ledger, draft_id)
        if record is None:
            raise ReportValidationError(f"draft disposition references unknown draft: {draft_id}")
        if record.get("is_current") is not True:
            if record.get("disposition") == decision["action"] and str(record.get("disposition_reason") or "") == decision["reason"]:
                applied.append(dict(record))
                continue
            raise ReportValidationError(f"draft disposition references a retired draft with a different decision: {draft_id}")
        node_id = str(record.get("node_id"))
        request = write_request(ledger, str(record.get("write_key")))
        now = utc_now_iso()
        action = decision["action"]
        if node_id not in nodes and action != "drop":
            raise ReportValidationError(f"draft {draft_id} belongs to removed node {node_id}; use drop")
        record["review_status"] = "none"
        record["disposed_at"] = now
        record["disposition"] = action
        record["disposition_reason"] = decision["reason"]
        if action == "keep":
            previous = record.get("applicable_fingerprint")
            record["applicable_fingerprint"] = fingerprints.get(node_id)
            record["confirmed_fingerprint"] = fingerprints.get(node_id)
            record["confirmed_at"] = now
            record["keep_reason"] = decision["reason"]
            record["keep_from_fingerprint"] = previous
            record["is_current"] = True
            record["status"] = "current"
        else:
            record["is_current"] = False
            record["status"] = "rewritten" if action == "rewrite" else "dropped"
            if action == "rewrite":
                record["rewrite_source"] = {
                    "reason": decision["reason"],
                    "write_key": record.get("write_key"),
                    "at": now,
                }
            if request is not None:
                request["updated_at"] = now
                # A completed request keeps its identity. A rewrite retires the
                # old draft but does not silently re-enqueue the same completed
                # request; Outline must issue a new write key/node request for
                # the new writing round.
                request["status"] = "completed"
        applied.append(dict(record))
    _refresh(ledger)
    return applied


def apply_write_requests(ledger: ReportLedger, tree: Mapping[str, Any]) -> list[dict[str, Any]]:
    nodes = {str(node.get("node_id")): node for node in tree.get("nodes") or [] if isinstance(node, Mapping)}
    incoming_raw = tree.get("write_requests")
    incoming = [item for item in incoming_raw if isinstance(item, Mapping)] if isinstance(incoming_raw, list) else []
    incoming_keys_by_node: dict[str, set[str]] = {}
    for item in incoming:
        raw_key = str(item.get("key") or "").strip()
        raw_nodes = [str(value) for value in item.get("node_ids") or []]
        if raw_key and len(raw_nodes) == 1:
            incoming_keys_by_node.setdefault(raw_nodes[0], set()).add(raw_key)
    existing = {str(item.get("key")): item for item in write_requests(ledger)}
    seen: set[str] = set()
    applied: list[dict[str, Any]] = []
    for raw in incoming:
        key = str(raw.get("key") or "").strip()
        node_ids = [str(item) for item in raw.get("node_ids") or []]
        feedback_wanted = bool(raw.get("feedback_wanted", False))
        if key in seen:
            raise ReportValidationError(f"duplicate write request key: {key}")
        seen.add(key)
        if len(node_ids) != 1:
            raise ReportValidationError(f"write request {key} must target exactly one writable node in P2")
        node = nodes.get(node_ids[0])
        if node is None or node.get("kind") != "writable":
            raise ReportValidationError(f"write request {key} targets an unknown/non-writable node")
        current = existing.get(key)
        if current is None:
            record = {
                "key": key,
                "node_ids": node_ids,
                "feedback_wanted": feedback_wanted,
                "status": "pending",
                "round": 1,
                "created_tree_version": (ledger.payload.get("tree") or {}).get("version", 0) + 1,
                "attempts": [],
                "last_action_id": None,
                "last_draft_id": None,
                "draft_id": None,
                "last_error": None,
                "updated_at": utc_now_iso(),
            }
            ledger.payload.setdefault("write_requests", []).append(record)
            applied.append(record)
            continue
        if list(current.get("node_ids") or []) != node_ids or bool(current.get("feedback_wanted")) != feedback_wanted:
            raise ReportValidationError(f"write request {key} changed node/feedback_wanted; use a new key for a new request")
        status = str(current.get("status") or "")
        if status == "completed":
            node_id = node_ids[0]
            current_draft = next(
                (
                    item
                    for item in drafts(ledger)
                    if item.get("is_current") is True and str(item.get("node_id")) == node_id
                ),
                None,
            )
            if current_draft is None or str(current_draft.get("write_key") or "") != key:
                other_keys = incoming_keys_by_node.get(node_id, set()) - {key}
                other_active = any(
                    other_key != key
                    and node_id in [str(value) for value in record.get("node_ids") or []]
                    and str(record.get("status") or "") not in {"completed", "superseded"}
                    for other_key, record in existing.items()
                )
                if not other_keys and not other_active:
                    raise ReportValidationError(
                        f"write request {key} is completed but node {node_id} has no current draft for this key; "
                        "this tree requires new writing, so issue a new write key"
                    )
            applied.append(current)
            continue
        if status == "superseded":
            raise ReportValidationError(f"write request {key} was superseded; use a new key")
        linked = [item for item in feedback_entries(ledger) if item.get("write_key") == key and item.get("status") == "handed_off"]
        if linked:
            unresolved = [item for item in linked if str((ledger.gap(str(item.get("gap_id"))) or {}).get("status") or "") not in {"resolved", "not_needed"}]
            if unresolved:
                raise ReportValidationError(f"write request {key} is waiting for research on {', '.join(str(item.get('gap_id')) for item in unresolved)}; do not resume writing yet")
            for item in linked:
                item["status"] = "closed"
                item["continued_at"] = utc_now_iso()
        current["status"] = "pending"
        current["updated_at"] = utc_now_iso()
        if status in {"blocked", "feedback_pending"}:
            current["round"] = int(current.get("round") or 1) + 1
        applied.append(current)
    for key, record in existing.items():
        if key in seen or record.get("status") == "completed":
            continue
        if record.get("status") in {"blocked", "feedback_pending"}:
            # A feedback-handling tree may intentionally omit the write key
            # until Outline resumes it. Keep the blocked request and its
            # feedback history instead of silently superseding it.
            continue
        record["status"] = "superseded"
        record["updated_at"] = utc_now_iso()
    for record in write_requests(ledger):
        if record.get("status") in {"completed", "superseded"}:
            continue
        node_ids = [str(item) for item in record.get("node_ids") or []]
        if node_ids and all(node_id not in nodes for node_id in node_ids):
            # The accepted tree explicitly removed the node. Retire the active
            # write request, but keep attempts and feedback history. Research
            # gaps are not touched here; they may still serve other nodes.
            record["status"] = "superseded"
            record["superseded_reason"] = "node removed from the accepted tree"
            record["updated_at"] = utc_now_iso()
    _refresh(ledger)
    return applied


def _normalize_feedback(items: Iterable[Mapping[str, Any]], ledger: ReportLedger, *, node_id: str, key: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(items):
        if not isinstance(raw, Mapping):
            raise ReportValidationError(f"node_feedback[{index}] must be an object")
        item_node = str(raw.get("node_id") or node_id).strip()
        if item_node != node_id:
            raise ReportValidationError(f"node_feedback[{index}] node_id does not match this write request")
        kind = str(raw.get("kind") or "").strip().lower()
        if kind not in FEEDBACK_KINDS:
            raise ReportValidationError(f"node_feedback[{index}].kind is invalid")
        message = str(raw.get("message") or "").strip()
        if not message:
            raise ReportValidationError(f"node_feedback[{index}].message must be non-empty")
        material_ids = raw.get("material_ids") or []
        if not isinstance(material_ids, list):
            raise ReportValidationError(f"node_feedback[{index}].material_ids must be a list")
        for material_id in material_ids:
            if ledger.material(str(material_id)) is None:
                raise ReportValidationError(f"node_feedback[{index}] references unknown material: {material_id}")
        requirement_ids = raw.get("requirement_ids") or []
        if not isinstance(requirement_ids, list):
            raise ReportValidationError(f"node_feedback[{index}].requirement_ids must be a list")
        result.append(
            {
                "kind": kind,
                "message": message,
                "material_ids": [str(item) for item in material_ids],
                "requirement_ids": [str(item) for item in requirement_ids],
                "suggested_action": str(raw.get("suggested_action") or "").strip() or None,
            }
        )
    return result


def _finish_attempt(request: dict[str, Any], action_id: str, status: str, *, draft_id: str | None = None, stop_reason: str | None = None) -> None:
    attempts = request.setdefault("attempts", [])
    for attempt in reversed(attempts):
        if isinstance(attempt, dict) and attempt.get("action_id") == action_id and attempt.get("status") == "in_flight":
            attempt["status"] = status
            attempt["finished_at"] = utc_now_iso()
            if draft_id:
                attempt["draft_id"] = draft_id
            if stop_reason:
                attempt["stop_reason"] = str(stop_reason)[:100]
            return
    attempts.append(
        {
            "action_id": action_id,
            "status": status,
            "at": utc_now_iso(),
            **({"draft_id": draft_id} if draft_id else {}),
            **({"stop_reason": str(stop_reason)[:100]} if stop_reason else {}),
        }
    )


def _refresh(ledger: ReportLedger) -> None:
    pending = [item for item in write_requests(ledger) if item.get("status") in {"pending", "technical_failed"}]
    review_due = [str(item.get("draft_id")) for item in drafts(ledger) if item.get("review_status") == "open"]
    feedback_open = [str(item.get("feedback_id")) for item in feedback_entries(ledger) if item.get("status") == "open"]
    ledger.payload["writes_pending"] = [str(item.get("key")) for item in pending]
    ledger.payload["draft_review_due"] = review_due
    ledger.payload["feedback_open"] = feedback_open
    ledger.payload["outline_review_due"] = bool(review_due or feedback_open)


def control_entries(ledger: ReportLedger) -> dict[str, Any]:
    requests = write_requests(ledger)
    current_drafts = [item for item in drafts(ledger) if item.get("is_current") is True]
    return {
        "write_requests": [
            {
                "key": item.get("key"),
                "node_ids": item.get("node_ids") or [],
                "status": item.get("status"),
                "round": item.get("round"),
                "draft_id": item.get("draft_id"),
                "last_error": item.get("last_error"),
                "next_action": next_action(item),
            }
            for item in requests
            if item.get("status") != "superseded"
        ],
        "writes_pending": list(ledger.payload.get("writes_pending") or []),
        "current_drafts": [
            {
                "draft_id": item.get("draft_id"),
                "node_id": item.get("node_id"),
                "write_key": item.get("write_key"),
                "path": item.get("virtual_path"),
                "review_status": item.get("review_status"),
            }
            for item in current_drafts
        ],
        "draft_review_due": [item.get("draft_id") for item in current_drafts if item.get("review_status") == "open"],
        "feedback_open": [
            {
                "feedback_id": item.get("feedback_id"),
                "node_id": item.get("node_id"),
                "write_key": item.get("write_key"),
                "kind": item.get("kind"),
                "status": item.get("status"),
                "gap_id": item.get("gap_id"),
            }
            for item in feedback_entries(ledger)
            if item.get("status") in {"open", "handed_off"}
        ],
    }
