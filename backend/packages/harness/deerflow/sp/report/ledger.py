"""Authoritative report ledger: identities, gaps, research rounds, user inputs.

The ledger is one JSON file per report.  All mutations are read-modify-write on
that file and are persisted atomically by :mod:`deerflow.sp.report.store`.
Business authority stays here; Artifacts remain a registration view of valid
files.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deerflow.sp.report import store as report_store

GAP_STATUSES = frozenset({"open", "in_flight", "awaiting_outline", "awaiting_user", "resolved", "not_needed"})
GAP_OUTCOMES = frozenset({"resolved", "unresolved", "not_needed"})
GAP_ATTEMPT_EXECUTIONS = frozenset({"searched", "not_attempted", "technical_error"})
RESEARCH_EXECUTION_TOOLS = frozenset({"web_search", "web_fetch", "report_fetch_material"})
ACTIVE_GAP_STATUSES = frozenset({"open", "in_flight", "awaiting_outline", "awaiting_user"})
DEFAULT_ALLOWED_ROUNDS = 2
LEDGER_SCHEMA_VERSION = 1
INPUT_PREVIEW_MAX_CHARS = 2000


class ReportValidationError(ValueError):
    """Raised when an Agent-declared report structure cannot be accepted."""


class ReportLedgerError(RuntimeError):
    """Raised when ledger transitions are attempted on an invalid report."""


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReportValidationError(f"{field_name} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ReportValidationError(f"{field_name} must be a list of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ReportValidationError(f"{field_name} must contain non-empty strings")
        result.append(item.strip())
    return list(dict.fromkeys(result))


class ReportLedger:
    """Mutable in-memory view of one persisted report ledger."""

    def __init__(self, payload: dict[str, Any], paths: report_store.ReportPaths) -> None:
        self.payload = payload
        self.paths = paths

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        paths: report_store.ReportPaths,
        *,
        request_text: str,
        inputs: Iterable[Mapping[str, Any]] = (),
    ) -> ReportLedger:
        paths.directory.mkdir(parents=True, exist_ok=True)
        now = utc_now_iso()
        report_store.write_request(paths, request_text)
        ledger: dict[str, Any] = {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "report_id": paths.report_id,
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "request": {
                "path": str(paths.request),
                "virtual_path": paths.virtual(paths.request),
                "sha256": report_store.sha256_text(request_text),
                "chars": len(request_text),
            },
            "inputs": [],
            "tree": None,
            "nodes": {},
            "gaps": [],
            "sources": [],
            "materials": [],
            "actions": {},
            "summaries": [],
            "user_decisions": [],
            "write_requests": [],
            "writes_pending": [],
            "drafts": [],
            "feedback": [],
            "outline_review_due": False,
            "feedback_dispositions": [],
            "draft_dispositions": [],
            "candidates": [],
            "candidate": None,
            "reviews": [],
            "review": None,
            "final_reports": [],
            "final_report": None,
            "delivery_history": [],
            "delivered": None,
            "resumed_at": None,
            "closed": None,
        }
        instance = cls(ledger, paths)
        for item in inputs:
            instance.add_user_input(
                str(item.get("text") or ""),
                source=str(item.get("source") or "user_message"),
                source_message_id=item.get("source_message_id"),
                interaction_id=item.get("interaction_id"),
                created_at=item.get("created_at"),
                applied=bool(item.get("applied", True)),
            )
        instance.save()
        return instance

    @classmethod
    def load(cls, paths: report_store.ReportPaths) -> ReportLedger:
        try:
            payload = report_store.read_json(paths.ledger)
        except FileNotFoundError as exc:
            raise ReportLedgerError(f"report ledger does not exist: {paths.ledger}") from exc
        if not isinstance(payload, dict):
            raise ReportLedgerError(f"report ledger is not an object: {paths.ledger}")
        return cls(payload, paths)

    @classmethod
    def load_for_context(cls, context: report_store.ReportContext) -> ReportLedger:
        if not context.is_enabled or context.outputs_path is None or not context.report_id:
            raise ReportLedgerError(f"report context is not usable: {context.status}")
        return cls.load(report_store.ReportPaths(context.outputs_path, context.report_id))

    def save(self) -> None:
        self.payload["updated_at"] = utc_now_iso()
        report_store.write_json_atomic(self.paths.ledger, self.payload)
        report_store.write_catalog(self.paths, self.payload)

    # ------------------------------------------------------------------
    # Identity and lookups
    # ------------------------------------------------------------------
    def gaps(self) -> list[dict[str, Any]]:
        return [item for item in self.payload.get("gaps", []) if isinstance(item, dict)]

    def gap(self, gap_id: str) -> dict[str, Any] | None:
        return next((item for item in self.gaps() if item.get("gap_id") == gap_id), None)

    def require_gap(self, gap_id: str) -> dict[str, Any]:
        gap = self.gap(str(gap_id))
        if gap is None:
            raise ReportValidationError(f"unknown gap_id: {gap_id}")
        return gap

    def materials(self) -> list[dict[str, Any]]:
        return [item for item in self.payload.get("materials", []) if isinstance(item, dict)]

    def material(self, material_id: str) -> dict[str, Any] | None:
        return next((item for item in self.materials() if item.get("material_id") == material_id), None)

    def inputs(self) -> list[dict[str, Any]]:
        return [item for item in self.payload.get("inputs", []) if isinstance(item, dict)]

    def input(self, input_id: str) -> dict[str, Any] | None:
        return next((item for item in self.inputs() if item.get("input_id") == input_id), None)

    def next_gap_id(self) -> str:
        return report_store.next_identifier("g", [str(item.get("gap_id")) for item in self.gaps()])

    def next_input_id(self) -> str:
        return report_store.next_identifier("u", [str(item.get("input_id")) for item in self.inputs()])

    def next_material_id(self) -> str:
        return report_store.next_identifier("m", [str(item.get("material_id")) for item in self.materials()])

    def next_source_id(self) -> str:
        sources = self.payload.get("sources") if isinstance(self.payload.get("sources"), list) else []
        return report_store.next_identifier("S", [str(item.get("source_id")) for item in sources if isinstance(item, Mapping)])

    def next_tree_version(self) -> int:
        tree = self.payload.get("tree")
        current = tree.get("version") if isinstance(tree, Mapping) else 0
        try:
            return int(current or 0) + 1
        except (TypeError, ValueError):
            return 1

    # ------------------------------------------------------------------
    # User inputs
    # ------------------------------------------------------------------
    def add_user_input(
        self,
        text: str,
        *,
        source: str,
        source_message_id: str | None = None,
        interaction_id: str | None = None,
        created_at: str | None = None,
        applied: bool = False,
    ) -> str:
        clean = str(text or "").strip()
        if not clean:
            raise ReportValidationError("report user input must be non-empty")
        for item in self.inputs():
            matched = (source_message_id and str(item.get("source_message_id") or "") == str(source_message_id)) or (interaction_id and str(item.get("interaction_id") or "") == str(interaction_id))
            if matched:
                # One HITL answer can arrive through a hidden response message
                # and later through generic message synchronization. Keep one
                # input identity and link both host identities.
                if source_message_id and not item.get("source_message_id"):
                    item["source_message_id"] = source_message_id
                if interaction_id and not item.get("interaction_id"):
                    item["interaction_id"] = interaction_id
                if source == "hitl_feedback" and item.get("source") == "user_message":
                    item["source"] = "hitl_feedback"
                return str(item.get("input_id"))
        input_id = self.next_input_id()
        report_store.append_user_input_file(self.paths, input_id, clean)
        self.payload.setdefault("inputs", []).append(
            {
                "input_id": input_id,
                "path": str(self.paths.input_file(input_id)),
                "virtual_path": self.paths.virtual(self.paths.input_file(input_id)),
                "source": source,
                "source_message_id": source_message_id,
                "interaction_id": interaction_id,
                "preview": clean[:INPUT_PREVIEW_MAX_CHARS],
                "sha256": report_store.sha256_text(clean),
                "chars": len(clean),
                "created_at": created_at or utc_now_iso(),
                "applied": applied,
            }
        )
        return input_id

    def mark_input_applied(self, input_id: str) -> None:
        item = self.input(input_id)
        if item is None:
            raise ReportValidationError(f"unknown input_id: {input_id}")
        item["applied"] = True
        item["applied_at"] = utc_now_iso()

    def unapplied_input_ids(self) -> list[str]:
        return [str(item.get("input_id")) for item in self.inputs() if not item.get("applied")]

    # ------------------------------------------------------------------
    # Gaps
    # ------------------------------------------------------------------
    def register_gap(
        self,
        *,
        mode: str,
        question: str,
        constraints: Mapping[str, Any] | None,
        why_insufficient: str,
        stop_condition: str,
        requirement_refs: list[Mapping[str, Any]],
        node_ids: list[str],
        materials_checked: list[Mapping[str, Any]],
        continue_gap_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_mode = str(mode or "").strip().lower()
        if normalized_mode not in {"new", "continue"}:
            raise ReportValidationError("gap mode must be 'new' or 'continue'")
        if normalized_mode == "new" and continue_gap_id:
            raise ReportValidationError("mode=new must not include continue_gap_id")
        if normalized_mode == "continue":
            if not continue_gap_id:
                raise ReportValidationError("mode=continue requires continue_gap_id")
            gap = self.require_gap(continue_gap_id)
            if gap.get("status") in {"resolved", "not_needed"}:
                raise ReportValidationError(f"cannot continue a closed gap: {continue_gap_id}")
            gap["question"] = _text(question, "question")
            gap["constraints"] = dict(constraints or {})
            gap["why_insufficient"] = _text(why_insufficient, "why_insufficient")
            gap["stop_condition"] = _text(stop_condition, "stop_condition")
            gap["requirement_refs"] = _normalize_requirement_refs(requirement_refs, self)
            gap["node_ids"] = _validate_node_ids(node_ids, self)
            gap["materials_checked"] = _normalize_materials_checked(materials_checked, self)
            gap["updated_at"] = utc_now_iso()
            gap.setdefault("history", []).append(
                {
                    "at": gap["updated_at"],
                    "event": "continued",
                    "question": gap["question"],
                    "why_insufficient": gap["why_insufficient"],
                }
            )
            return gap

        gap_id = self.next_gap_id()
        now = utc_now_iso()
        gap = {
            "gap_id": gap_id,
            "question": _text(question, "question"),
            "status": "open",
            "rounds": 0,
            "allowed_rounds": DEFAULT_ALLOWED_ROUNDS,
            "additional_rounds": 0,
            "constraints": dict(constraints or {}),
            "why_insufficient": _text(why_insufficient, "why_insufficient"),
            "stop_condition": _text(stop_condition, "stop_condition"),
            "requirement_refs": _normalize_requirement_refs(requirement_refs, self),
            "node_ids": _validate_node_ids(node_ids, self),
            "materials_checked": _normalize_materials_checked(materials_checked, self),
            "created_at": now,
            "updated_at": now,
            "history": [{"at": now, "event": "registered"}],
            "outcome": None,
            "interaction_id": None,
            "pending_input_ids": [],
        }
        self.payload.setdefault("gaps", []).append(gap)
        return gap

    def attach_nodes_to_gaps(self, nodes: Iterable[Mapping[str, Any]]) -> None:
        for node in nodes:
            if not isinstance(node, Mapping):
                continue
            node_id = str(node.get("node_id") or "")
            raw_gap_ids = node.get("gap_ids")
            if not isinstance(raw_gap_ids, list):
                continue
            for gap_id in raw_gap_ids:
                gap = self.require_gap(str(gap_id))
                node_ids = gap.setdefault("node_ids", [])
                if node_id and node_id not in node_ids:
                    node_ids.append(node_id)
                gap["updated_at"] = utc_now_iso()

    def bind_interaction(self, gap_ids: Iterable[str], interaction_id: str) -> None:
        for gap_id in gap_ids:
            gap = self.require_gap(str(gap_id))
            if gap.get("status") != "awaiting_user":
                raise ReportValidationError(f"gap {gap_id} is not awaiting_user (status={gap.get('status')})")
            gap["interaction_id"] = interaction_id
            gap["updated_at"] = utc_now_iso()

    def mark_gaps_awaiting_outline(self, gap_ids: Iterable[str], *, input_id: str | None = None) -> None:
        for gap_id in gap_ids:
            gap = self.require_gap(str(gap_id))
            if gap.get("status") in {"resolved", "not_needed"}:
                continue
            gap["status"] = "awaiting_outline"
            gap["updated_at"] = utc_now_iso()
            if input_id:
                pending = gap.setdefault("pending_input_ids", [])
                if input_id not in pending:
                    pending.append(input_id)
            gap.setdefault("history", []).append(
                {
                    "at": gap["updated_at"],
                    "event": "awaiting_outline",
                    "input_id": input_id,
                }
            )

    # ------------------------------------------------------------------
    # Research actions and rounds
    # ------------------------------------------------------------------
    def begin_research_action(self, action_id: str, gap_ids: Iterable[str], *, allow_empty: bool = False, kind: str = "research") -> dict[str, Any]:
        existing = self.action(action_id)
        if existing is not None:
            return existing
        normalized_gap_ids = _string_list(list(gap_ids), "report_gap_ids")
        if not normalized_gap_ids and not allow_empty:
            raise ReportValidationError("a report research delegation requires at least one gap_id")
        for gap_id in normalized_gap_ids:
            gap = self.require_gap(gap_id)
            if gap.get("status") == "in_flight":
                raise ReportValidationError(f"gap {gap_id} already has an in-flight research action")
            if gap.get("status") == "awaiting_outline":
                raise ReportValidationError(f"gap {gap_id} is awaiting Outline judgement; it cannot be dispatched again")
            if gap.get("status") == "awaiting_user":
                raise ReportValidationError(f"gap {gap_id} is awaiting a user decision; it cannot be researched yet")
            if gap.get("status") in {"resolved", "not_needed"}:
                raise ReportValidationError(f"gap {gap_id} is already closed")
        now = utc_now_iso()
        action = {
            "action_id": action_id,
            "kind": kind,
            "gap_ids": normalized_gap_ids,
            "status": "in_flight",
            "created_at": now,
            "updated_at": now,
            "tool_executions": [],
            "gap_attempts": {},
            "summary_path": None,
            "artifact_id": None,
            "error": None,
        }
        self.payload.setdefault("actions", {})[action_id] = action
        for gap_id in normalized_gap_ids:
            gap = self.gap(gap_id)
            if gap is not None:
                gap["status"] = "in_flight"
                gap["updated_at"] = now
        return action

    def action(self, action_id: str) -> dict[str, Any] | None:
        actions = self.payload.get("actions")
        if not isinstance(actions, Mapping):
            return None
        value = actions.get(action_id)
        return dict(value) if isinstance(value, Mapping) else None

    def record_tool_execution(self, action_id: str, *, tool: str, ok: bool, detail: str | None = None) -> None:
        action = self.payload.setdefault("actions", {}).setdefault(
            action_id,
            {
                "action_id": action_id,
                "kind": "research",
                "gap_ids": [],
                "status": "in_flight",
                "created_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
                "tool_executions": [],
                "gap_attempts": {},
                "summary_path": None,
                "artifact_id": None,
                "error": None,
            },
        )
        action.setdefault("tool_executions", []).append({"tool": tool, "ok": bool(ok), "detail": (detail or "")[:500], "at": utc_now_iso()})
        action["updated_at"] = utc_now_iso()

    def finish_research_action(
        self,
        action_id: str,
        *,
        gap_attempts: Iterable[Mapping[str, Any]],
        summary_path: Path | None,
        artifact_id: str | None,
        host_tool_executions: Iterable[Mapping[str, Any]] = (),
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        action = self.payload.setdefault("actions", {}).get(action_id)
        if not isinstance(action, dict):
            raise ReportValidationError(f"research action was not begun: {action_id}")
        if action.get("status") == "completed":
            return action, []
        action_gap_ids = [str(item) for item in action.get("gap_ids") or []]
        attempts: dict[str, str] = {}
        for raw in gap_attempts:
            if not isinstance(raw, Mapping):
                raise ReportValidationError("gap_attempts items must be objects")
            gap_id = str(raw.get("gap_id") or "").strip()
            execution = str(raw.get("execution") or "").strip().lower()
            if gap_id not in action_gap_ids:
                raise ReportValidationError(f"gap_attempts references a gap outside this action: {gap_id}")
            if execution not in GAP_ATTEMPT_EXECUTIONS:
                raise ReportValidationError(f"invalid gap attempt execution for {gap_id}: {execution!r}")
            attempts[gap_id] = execution
        for gap_id in action_gap_ids:
            attempts.setdefault(gap_id, "not_attempted")

        # Only host-observed tool results count as execution evidence. The
        # model's own artifact_metadata.tool_execution_records is descriptive
        # and must not be able to manufacture a research round.
        executions = list(action.get("tool_executions") or []) + [
            {"tool": str(item.get("tool") or "unknown"), "ok": bool(item.get("ok")), "detail": str(item.get("detail") or "")[:500], "at": utc_now_iso()} for item in host_tool_executions if isinstance(item, Mapping)
        ]
        research_executions = [item for item in executions if str(item.get("tool") or "") in RESEARCH_EXECUTION_TOOLS]
        has_successful_execution = any(bool(item.get("ok")) for item in research_executions)
        has_research_attempt = bool(research_executions)
        changed: list[dict[str, Any]] = []
        for gap_id in action_gap_ids:
            gap = self.require_gap(gap_id)
            declared = attempts[gap_id]
            effective = declared
            note = ""
            if declared == "searched":
                if has_successful_execution:
                    effective = "searched"
                elif has_research_attempt:
                    effective = "technical_error"
                    note = "All host-recorded research tool executions for this action failed; no effective round was charged."
                else:
                    effective = "technical_error"
                    note = "Researcher declared a search but the host recorded no research tool execution; no effective round was charged."
            now = utc_now_iso()
            if effective == "searched":
                gap["rounds"] = int(gap.get("rounds") or 0) + 1
                gap["status"] = "awaiting_outline"
            elif effective == "technical_error":
                gap.setdefault("technical_attempts", 0)
                gap["technical_attempts"] = int(gap.get("technical_attempts") or 0) + 1
                gap["status"] = "awaiting_outline"
                note = note or "This research action reported a technical failure; no effective round was charged."
            else:
                gap["status"] = "open" if gap.get("status") == "in_flight" else gap.get("status")
                note = note or "This action did not attempt the gap; no effective round was charged."
            gap["updated_at"] = now
            gap.setdefault("history", []).append(
                {
                    "at": now,
                    "event": "research_result",
                    "action_id": action_id,
                    "execution": effective,
                    "declared_execution": declared,
                    "rounds_after": int(gap.get("rounds") or 0),
                    "note": note,
                }
            )
            changed.append({"gap_id": gap_id, "execution": effective, "rounds": int(gap.get("rounds") or 0), "status": gap.get("status")})
        action["status"] = "completed"
        action["updated_at"] = utc_now_iso()
        action["finished_at"] = action["updated_at"]
        action["gap_attempts"] = attempts
        action["host_tool_executions"] = executions
        action["summary_path"] = str(summary_path) if summary_path is not None else None
        action["summary_virtual_path"] = self.paths.virtual(summary_path) if summary_path is not None else None
        action["artifact_id"] = artifact_id
        if summary_path is not None:
            self.payload.setdefault("summaries", []).append(
                {
                    "action_id": action_id,
                    "gap_ids": action_gap_ids,
                    "path": str(summary_path),
                    "virtual_path": self.paths.virtual(summary_path),
                    "sha256": report_store.sha256_text(summary_path.read_text(encoding="utf-8")),
                    "artifact_id": artifact_id,
                    "created_at": utc_now_iso(),
                }
            )
        return action, changed

    def fail_research_action(self, action_id: str, error: str) -> None:
        action = self.payload.setdefault("actions", {}).get(action_id)
        if not isinstance(action, dict):
            return
        if action.get("status") == "completed":
            return
        action["status"] = "failed"
        action["updated_at"] = utc_now_iso()
        action["error"] = str(error)[:2000]
        for gap_id in action.get("gap_ids") or []:
            gap = self.gap(str(gap_id))
            if gap is None or gap.get("status") in {"resolved", "not_needed"}:
                continue
            gap["status"] = "open"
            gap["updated_at"] = utc_now_iso()
            gap.setdefault("technical_attempts", 0)
            gap["technical_attempts"] = int(gap.get("technical_attempts") or 0) + 1
            gap.setdefault("history", []).append(
                {
                    "at": gap["updated_at"],
                    "event": "research_failed",
                    "action_id": action_id,
                    "execution": "technical_error",
                    "note": str(error)[:500],
                }
            )

    # ------------------------------------------------------------------
    # Outline decisions
    # ------------------------------------------------------------------
    def apply_gap_outcomes(self, outcomes: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        normalized = [dict(item) for item in outcomes if isinstance(item, Mapping)]
        awaiting = [gap for gap in self.gaps() if gap.get("status") == "awaiting_outline"]
        by_gap: dict[str, dict[str, Any]] = {}
        for item in normalized:
            gap_id = str(item.get("gap_id") or "").strip()
            if not gap_id:
                raise ReportValidationError("gap_outcomes item requires gap_id")
            if gap_id in by_gap:
                raise ReportValidationError(f"duplicate gap_outcome for {gap_id}")
            outcome = str(item.get("outcome") or "").strip().lower()
            if outcome not in GAP_OUTCOMES:
                raise ReportValidationError(f"unsupported gap outcome for {gap_id}: {outcome!r}")
            reason = _text(item.get("reason"), f"gap_outcomes[{gap_id}].reason")
            gap = self.require_gap(gap_id)
            if gap.get("status") not in {"awaiting_outline", "awaiting_user", "open", "in_flight"}:
                raise ReportValidationError(f"gap {gap_id} is already closed (status={gap.get('status')})")
            entry: dict[str, Any] = {"outcome": outcome, "reason": reason, "at": utc_now_iso()}
            material_ids = _string_list(item.get("evidence_material_ids"), f"gap_outcomes[{gap_id}].evidence_material_ids")
            for material_id in material_ids:
                if self.material(material_id) is None:
                    raise ReportValidationError(f"gap_outcomes[{gap_id}] references unknown material: {material_id}")
            if material_ids:
                entry["evidence_material_ids"] = material_ids
            evidence_note = item.get("evidence_note")
            if evidence_note is not None:
                entry["evidence_note"] = _text(evidence_note, f"gap_outcomes[{gap_id}].evidence_note")
            user_input_id = item.get("user_input_id")
            if user_input_id is not None:
                user_input_id = _text(user_input_id, f"gap_outcomes[{gap_id}].user_input_id")
                if self.input(user_input_id) is None:
                    raise ReportValidationError(f"gap_outcomes[{gap_id}] references unknown input: {user_input_id}")
                entry["user_input_id"] = user_input_id
            if outcome == "not_needed" and item.get("optional") is not True and "user_input_id" not in entry:
                raise ReportValidationError(f"gap_outcomes[{gap_id}] not_needed must cite user_input_id unless optional=true (scope change requires user authority)")
            by_gap[gap_id] = entry

        missing = [gap["gap_id"] for gap in awaiting if gap.get("gap_id") not in by_gap]
        if missing:
            raise ReportValidationError(f"unjudged awaiting_outline gaps: {', '.join(missing)}")

        applied: list[dict[str, Any]] = []
        now = utc_now_iso()
        for gap_id, entry in by_gap.items():
            gap = self.require_gap(gap_id)
            outcome = entry["outcome"]
            gap["outcome"] = entry
            if outcome == "resolved":
                gap["status"] = "resolved"
            elif outcome == "not_needed":
                gap["status"] = "not_needed"
            else:
                allowed = int(gap.get("allowed_rounds") or DEFAULT_ALLOWED_ROUNDS) + int(gap.get("additional_rounds") or 0)
                gap["status"] = "awaiting_user" if int(gap.get("rounds") or 0) >= allowed else "open"
            gap["updated_at"] = now
            gap.setdefault("history", []).append(
                {
                    "at": now,
                    "event": "outline_judgement",
                    "outcome": outcome,
                    "reason": entry["reason"],
                    "rounds": int(gap.get("rounds") or 0),
                    "status_after": gap["status"],
                }
            )
            applied.append({"gap_id": gap_id, "outcome": outcome, "status": gap["status"], "rounds": gap.get("rounds"), "next_action": gap_next_action(gap)})
        return applied

    def apply_user_decisions(self, decisions: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        applied: list[dict[str, Any]] = []
        for raw in decisions:
            if not isinstance(raw, Mapping):
                raise ReportValidationError("user_decisions items must be objects")
            input_id = _text(raw.get("input_id"), "user_decisions[].input_id")
            if self.input(input_id) is None:
                raise ReportValidationError(f"user_decisions references unknown input: {input_id}")
            action = str(raw.get("action") or "").strip().lower()
            if action not in {"continue_research", "change_scope", "provide_material", "no_decision", "apply_to_plan"}:
                raise ReportValidationError(f"unsupported user decision action: {action!r}")
            reason = _text(raw.get("reason"), "user_decisions[].reason")
            gap_ids = _string_list(raw.get("gap_ids"), "user_decisions[].gap_ids")
            additional_rounds = raw.get("additional_rounds")
            if action == "continue_research":
                if isinstance(additional_rounds, bool) or not isinstance(additional_rounds, int) or additional_rounds <= 0:
                    raise ReportValidationError("continue_research requires a positive integer additional_rounds")
            elif additional_rounds is not None:
                raise ReportValidationError("additional_rounds is only valid for continue_research")

            if action == "apply_to_plan":
                if gap_ids:
                    raise ReportValidationError("apply_to_plan applies a user input without inventing a gap; gap_ids must be empty")
                duplicates = [item for item in self.payload.get("user_decisions", []) if isinstance(item, Mapping) and str(item.get("input_id")) == input_id and str(item.get("action")) == action and item.get("gap_id") in {None, ""}]
                if duplicates:
                    raise ReportValidationError(f"user decision already applied for input {input_id}, action {action}")
                now = utc_now_iso()
                record = {
                    "input_id": input_id,
                    "gap_id": None,
                    "action": action,
                    "reason": reason,
                    "additional_rounds": None,
                    "applied_at": now,
                }
                self.payload.setdefault("user_decisions", []).append(record)
                self.mark_input_applied(input_id)
                applied.append(record)
                continue

            if not gap_ids:
                raise ReportValidationError("user_decisions[].gap_ids must contain at least one gap for gap-scoped actions")
            for gap_id in gap_ids:
                gap = self.require_gap(gap_id)
                duplicates = [item for item in self.payload.get("user_decisions", []) if isinstance(item, Mapping) and str(item.get("input_id")) == input_id and str(item.get("gap_id")) == gap_id and str(item.get("action")) == action]
                if duplicates:
                    raise ReportValidationError(f"user decision already applied for input {input_id}, gap {gap_id}, action {action}")
                now = utc_now_iso()
                if action == "continue_research":
                    gap["additional_rounds"] = int(gap.get("additional_rounds") or 0) + int(additional_rounds)
                    gap["status"] = "open"
                elif action == "change_scope":
                    gap["status"] = "not_needed"
                    gap["outcome"] = {
                        "outcome": "not_needed",
                        "reason": reason,
                        "user_input_id": input_id,
                        "at": now,
                    }
                elif action == "provide_material":
                    gap["status"] = "awaiting_outline"
                else:
                    gap["status"] = "awaiting_user"
                gap["updated_at"] = now
                gap.setdefault("pending_input_ids", [])
                if input_id not in gap["pending_input_ids"]:
                    gap["pending_input_ids"].append(input_id)
                gap.setdefault("history", []).append(
                    {
                        "at": now,
                        "event": "user_decision",
                        "action": action,
                        "input_id": input_id,
                        "reason": reason,
                        "additional_rounds": additional_rounds,
                        "status_after": gap["status"],
                    }
                )
                record = {
                    "input_id": input_id,
                    "gap_id": gap_id,
                    "action": action,
                    "reason": reason,
                    "additional_rounds": additional_rounds,
                    "applied_at": now,
                }
                self.payload.setdefault("user_decisions", []).append(record)
                applied.append(record)
            self.mark_input_applied(input_id)
        return applied

    # ------------------------------------------------------------------
    # Derived control data
    # ------------------------------------------------------------------
    def control_summary(self) -> dict[str, Any]:
        tree = self.payload.get("tree") if isinstance(self.payload.get("tree"), Mapping) else {}
        open_gaps: list[dict[str, Any]] = []
        for gap in self.gaps():
            status = str(gap.get("status") or "")
            if status not in ACTIVE_GAP_STATUSES:
                continue
            history = gap.get("history") if isinstance(gap.get("history"), list) else []
            last = next((item for item in reversed(history) if isinstance(item, Mapping)), {})
            outcome = gap.get("outcome") if isinstance(gap.get("outcome"), Mapping) else {}
            reason = str(outcome.get("reason") or last.get("note") or last.get("reason") or gap.get("why_insufficient") or "")
            open_gaps.append(
                {
                    "gap_id": gap.get("gap_id"),
                    "question": gap.get("question"),
                    "status": status,
                    "rounds": int(gap.get("rounds") or 0),
                    "allowed_rounds": int(gap.get("allowed_rounds") or DEFAULT_ALLOWED_ROUNDS) + int(gap.get("additional_rounds") or 0),
                    "reason": reason[:1000],
                    "node_ids": list(gap.get("node_ids") or []),
                    "next_action": gap_next_action(gap),
                    "interaction_id": gap.get("interaction_id"),
                }
            )
        closed = [gap for gap in self.gaps() if gap.get("status") in {"resolved", "not_needed"}]
        from deerflow.sp.report import assembly as report_assembly
        from deerflow.sp.report import writing as report_writing

        writing = report_writing.control_entries(self)
        review = self.payload.get("review") if isinstance(self.payload.get("review"), Mapping) else None
        review_status = str((review or {}).get("status") or "none")
        if review_status == "ready":
            try:
                if report_assembly.review_binding_error(self):
                    review_status = "stale"
            except Exception:  # noqa: BLE001 - derived control state must stay readable
                review_status = "stale"
        candidate = self.payload.get("candidate") if isinstance(self.payload.get("candidate"), Mapping) else None
        final = self.payload.get("final_report") if isinstance(self.payload.get("final_report"), Mapping) else None
        return {
            "report_id": self.payload.get("report_id"),
            "status": self.payload.get("status"),
            "tree_version": int(tree.get("version") or 0) if tree else 0,
            "gaps": open_gaps,
            "closed_gap_count": len(closed),
            "writes_pending": writing["writes_pending"],
            "write_requests": writing["write_requests"],
            "current_drafts": writing["current_drafts"],
            "draft_review_due": writing["draft_review_due"],
            "feedback_open": writing["feedback_open"],
            "outline_review_due": bool(self.payload.get("outline_review_due")),
            "unapplied_input_ids": self.unapplied_input_ids(),
            "review_status": review_status,
            "review": review,
            "candidate": (
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "status": candidate.get("status"),
                    "path": candidate.get("virtual_path"),
                    "sha256": candidate.get("sha256"),
                    "inputs_digest": candidate.get("inputs_digest"),
                    "pending_review": candidate.get("status") == "pending_review",
                }
                if candidate is not None
                else None
            ),
            "final_report": final,
            "delivered": self.payload.get("delivered"),
            "research_actions_in_flight": [action.get("action_id") for action in (self.payload.get("actions") or {}).values() if isinstance(action, Mapping) and action.get("status") == "in_flight"],
        }

    def block_finish_reason(self) -> str | None:
        """Return a deterministic reason why report delivery is still blocked."""
        summary = self.control_summary()
        from deerflow.sp.report import assembly as report_assembly

        blockers: list[str] = []
        if summary["gaps"]:
            blockers.append("open gaps: " + ", ".join(f"{item['gap_id']}={item['status']}" for item in summary["gaps"]))
        if summary["unapplied_input_ids"]:
            blockers.append("unapplied user inputs: " + ", ".join(summary["unapplied_input_ids"]))
        if summary["research_actions_in_flight"]:
            blockers.append("research actions in flight: " + ", ".join(summary["research_actions_in_flight"]))
        unfinished = [item for item in summary["write_requests"] if item.get("status") not in {"completed", "superseded"}]
        if unfinished:
            blockers.append("unfinished write requests: " + ", ".join(f"{item.get('key')}={item.get('status')}" for item in unfinished))
        if summary["draft_review_due"]:
            blockers.append("draft review due: " + ", ".join(summary["draft_review_due"]))
        if summary["feedback_open"]:
            blockers.append("node feedback open: " + ", ".join(str(item.get("feedback_id")) for item in summary["feedback_open"]))
        if summary["outline_review_due"]:
            blockers.append("Outline review is due")
        if not self.payload.get("tree"):
            blockers.append("no accepted outline tree")
        try:
            review_error = report_assembly.review_binding_error(self)
            final_error = report_assembly.final_binding_error(self)
        except Exception as exc:  # noqa: BLE001 - unreadable assembly state blocks delivery
            blockers.append(f"assembly state cannot be verified: {exc}")
        else:
            if review_error:
                blockers.append(f"no current ready integration review: {review_error}")
            if final_error:
                blockers.append(f"no current final report: {final_error}")
        return "; ".join(blockers) if blockers else None


def gap_next_action(gap: Mapping[str, Any]) -> str:
    status = str(gap.get("status") or "")
    if status == "open":
        return "delegate_research"
    if status == "in_flight":
        return "wait"
    if status == "awaiting_outline":
        return "delegate_outline"
    if status == "awaiting_user":
        return "ask_human"
    return "closed"


def _normalize_requirement_refs(refs: Iterable[Mapping[str, Any]], ledger: ReportLedger) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for raw in refs:
        if not isinstance(raw, Mapping):
            raise ReportValidationError("requirement_refs items must be objects")
        input_id = _text(raw.get("input_id"), "requirement_refs[].input_id")
        if ledger.input(input_id) is None:
            raise ReportValidationError(f"requirement_refs references unknown input: {input_id}")
        quote = _text(raw.get("quote"), "requirement_refs[].quote")
        normalized.append({"input_id": input_id, "quote": quote})
    if not normalized:
        raise ReportValidationError("requirement_refs must contain at least one original user requirement reference")
    return normalized


def _normalize_materials_checked(items: Iterable[Mapping[str, Any]], ledger: ReportLedger) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, Mapping):
            raise ReportValidationError("materials_checked items must be objects")
        material_id = _text(raw.get("material_id"), "materials_checked[].material_id")
        material = ledger.material(material_id)
        if material is None:
            raise ReportValidationError(f"materials_checked references unknown material: {material_id}")
        entry: dict[str, Any] = {"material_id": material_id}
        lines = raw.get("lines")
        if lines is not None:
            entry["lines"] = _validate_line_range(lines, int(material.get("lines") or 0), f"materials_checked[{material_id}].lines")
        normalized.append(entry)
    return normalized


def _validate_line_range(value: Any, total_lines: int, field_name: str) -> list[int]:
    if not isinstance(value, list) or len(value) != 2:
        raise ReportValidationError(f"{field_name} must be [start, end]")
    start, end = value
    if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
        raise ReportValidationError(f"{field_name} must contain integers")
    if start < 1 or end < start or (total_lines and end > total_lines):
        raise ReportValidationError(f"{field_name} is outside the material line range 1..{total_lines}")
    return [start, end]


def _validate_node_ids(node_ids: Iterable[str], ledger: ReportLedger) -> list[str]:
    normalized = _string_list(list(node_ids), "node_ids")
    tree_nodes = ledger.payload.get("nodes") if isinstance(ledger.payload.get("nodes"), Mapping) else {}
    for node_id in normalized:
        if node_id not in tree_nodes:
            raise ReportValidationError(f"unknown node_id in accepted tree: {node_id}")
    return normalized
