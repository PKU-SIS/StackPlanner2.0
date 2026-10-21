"""Report-mode orchestration hooks used by existing SP dispatch/acceptance paths.

This module is deliberately a thin host boundary: it reads the ledger, prepares
role input packets, validates Agent-declared structures, and records objective
attempts.  It does not decide evidence sufficiency or write report prose.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.thread_state import SP_CONCURRENT_MERGE_MODE_KEY
from deerflow.sp.actions.events import make_sp_event
from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.schema import HandlerResult, SPAction
from deerflow.sp.artifacts import SPArtifactAdapter
from deerflow.sp.memory import StackMemoryEntry
from deerflow.sp.report import assembly as report_assembly
from deerflow.sp.report import packets as report_packets
from deerflow.sp.report import store as report_store
from deerflow.sp.report import tree as report_tree
from deerflow.sp.report import writing as report_writing
from deerflow.sp.report.ledger import (
    ACTIVE_GAP_STATUSES,
    ReportLedger,
    ReportValidationError,
    gap_next_action,
    utc_now_iso,
)
from deerflow.sp.report.materials import build_fetch_material_tool
from deerflow.sp.report.prompts import (
    REPORT_INTEGRATE_REVIEW_PROMPT,
    REPORT_OUTLINE_PROMPT,
    REPORT_RESEARCH_PROMPT,
    REPORT_WRITER_PROMPT,
)
from deerflow.sp.report.tools import build_register_research_request_tool
from deerflow.sp.subagents import SPSubagentResult, SPSubagentTask
from deerflow.utils.messages import message_to_text

REPORT_DELEGATE_ROLES = frozenset({"outline", "researcher", "reporter", "writer"})
REPORT_READ_TOOLS = ("read_file", "grep", "glob", "ls")
REPORT_CONTEXT_MESSAGE_NAME = "sp_report_context"
CONTEXT_INSTRUCTIONS = """
<report-control>
This is derived control state from the active report ledger, injected before
your model call. Do not edit the ledger by writing files. Use gap ids exactly.
Follow each gap's next_action: open -> delegate researcher with
metadata.report_gap_ids; awaiting_outline -> delegate outline; awaiting_user ->
sp_ask_human with metadata.report_gap_ids; in_flight -> wait. After an Outline
result, inspect the returned tree version and gap changes. In P3, after drafts
are current, delegate Outline with report_task="integrate_review"; if local
edits are applied, dispatch another integrate_review rather than finishing.
sp_finish is allowed only for the current ready final and is blocked by any
unfinished research/write/feedback/gap/input state.
</report-control>
""".strip()


def report_context(state: Mapping[str, Any] | None) -> report_store.ReportContext:
    return report_store.load_report_context(state)


def report_mode_enabled(state: Mapping[str, Any] | None) -> bool:
    context = report_context(state)
    return context.is_active or context.status in {"error", "delivered", "abandoned"}


def active_report_required(state: Mapping[str, Any] | None) -> report_store.ReportContext:
    context = report_context(state)
    if context.status == "error":
        raise ReportValidationError(f"report ledger unavailable: {context.error}")
    if not context.is_active:
        raise ReportValidationError(f"report is not active (status={context.status})")
    return context


def ledger_for_state(state: Mapping[str, Any] | None) -> ReportLedger:
    return ReportLedger.load_for_context(active_report_required(state))


def _message_id(message: Any) -> str:
    explicit = getattr(message, "id", None)
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    text = message_to_text(message)
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:24]}"


def _visible_human_messages(state: Mapping[str, Any] | None) -> list[HumanMessage]:
    messages = state.get("messages") if isinstance(state, Mapping) else None
    if not isinstance(messages, list):
        return []
    result: list[HumanMessage] = []
    for message in messages:
        if not isinstance(message, HumanMessage):
            continue
        if getattr(message, "name", None) in {REPORT_CONTEXT_MESSAGE_NAME, "sp_task_memory_context"}:
            continue
        additional = getattr(message, "additional_kwargs", None)
        if isinstance(additional, Mapping) and additional.get("hide_from_ui") is True:
            continue
        if message_to_text(message).strip():
            result.append(message)
    return result


def sync_user_inputs(state: Mapping[str, Any] | None) -> None:
    """Persist visible human messages that are new to the active report."""
    context = report_context(state)
    if not context.is_active:
        return
    paths = _paths_for_context(context)
    with report_store.report_lock(paths):
        ledger = ReportLedger.load(paths)
        changed = False
        for message in _visible_human_messages(state):
            text = message_to_text(message).strip()
            if not text:
                continue
            before = len(ledger.inputs())
            ledger.add_user_input(
                text,
                source="user_message",
                source_message_id=_message_id(message),
            )
            if len(ledger.inputs()) != before:
                changed = True
        if changed:
            ledger.save()


def _paths_for_context(context: report_store.ReportContext) -> report_store.ReportPaths:
    if context.outputs_path is None or not context.report_id:
        raise ReportValidationError(f"report context is not usable: {context.status}")
    return report_store.ReportPaths(context.outputs_path, context.report_id)


def start_report(context: HandlerContext, action: SPAction) -> dict[str, Any]:
    existing = report_context(context.state)
    if existing.status == "error":
        raise ReportValidationError(f"report ledger unavailable: {existing.error}")
    try:
        outputs_path = report_store.outputs_path_from_state(context.state)
    except report_store.ReportStoreError as exc:
        raise ReportValidationError(str(exc)) from exc
    with report_store.report_root_lock(outputs_path):
        current = report_context(context.state)
        if current.is_active:
            return {
                "report_id": current.report_id,
                "status": current.status,
                "started": False,
                "message": "An active report already exists; resume or continue it instead of starting a new one.",
            }
        if current.status == "error":
            raise ReportValidationError(f"report ledger unavailable: {current.error}")
        messages = _visible_human_messages(context.state)
        if not messages:
            raise ReportValidationError("cannot start a report without a visible user request")
        sections: list[str] = []
        inputs: list[dict[str, Any]] = []
        for index, message in enumerate(messages):
            text = message_to_text(message).strip()
            sections.append(f"## User input {index + 1}\n\n{text}")
            inputs.append(
                {
                    "text": text,
                    "source": "initial_user_request" if index == 0 else "user_message",
                    "source_message_id": _message_id(message),
                }
            )
        request_text = "\n\n".join(["# Report request", "", *sections]).strip()
        report_id = report_store.allocate_report_id(outputs_path)
        paths = report_store.ReportPaths(outputs_path, report_id)
        ledger = ReportLedger.create(paths, request_text=request_text, inputs=inputs)
        report_store.write_json_atomic(report_store.pointer_path(outputs_path), {"report_id": report_id})
        return {
            "report_id": report_id,
            "status": "active",
            "started": True,
            "request_chars": len(request_text),
            "input_ids": [item.get("input_id") for item in ledger.inputs()],
            "request_path": paths.virtual(paths.request),
        }


def resume_report(context: HandlerContext, action: SPAction) -> dict[str, Any]:
    context_info = report_context(context.state)
    if context_info.status == "error":
        raise ReportValidationError(f"report ledger unavailable: {context_info.error}")
    if not context_info.is_enabled:
        raise ReportValidationError("no report exists to resume")
    if context_info.status == "abandoned":
        raise ReportValidationError("cannot resume an abandoned report; start a new report instead")
    paths = _paths_for_context(context_info)
    with report_store.report_lock(paths):
        ledger = ReportLedger.load(paths)
        if context_info.status == "delivered":
            delivered = ledger.payload.get("delivered") if isinstance(ledger.payload.get("delivered"), Mapping) else None
            ledger.payload.setdefault("delivery_history", []).append(dict(delivered) if delivered else None)
            ledger.payload["delivered"] = None
            ledger.payload["status"] = "active"
            ledger.payload["resumed_at"] = utc_now_iso()
            review = ledger.payload.get("review")
            if isinstance(review, dict):
                review["status"] = "stale"
                review["stale_reason"] = "delivered report resumed for revision"
                review["stale_at"] = utc_now_iso()
                for item in ledger.payload.get("reviews") or []:
                    if isinstance(item, dict) and item.get("review_id") == review.get("review_id"):
                        item["status"] = "stale"
                        item["stale_reason"] = "delivered report resumed for revision"
                        item["stale_at"] = utc_now_iso()
            final = ledger.payload.get("final_report")
            if isinstance(final, dict):
                final["status"] = "historical"
                final["is_current"] = False
                final["superseded_reason"] = "delivered report resumed for revision"
            for item in ledger.payload.get("final_reports") or []:
                if isinstance(item, dict) and item.get("status") == "current":
                    item["status"] = "historical"
                    item["is_current"] = False
                    item["superseded_reason"] = "delivered report resumed for revision"
            ledger.payload["final_report"] = None
            ledger.payload["resume"] = {
                "resumed_at": ledger.payload["resumed_at"],
                "resume_of_delivery_id": (delivered or {}).get("delivery_id"),
                "action_id": action.action_id,
            }
            ledger.save()
        return {
            "report_id": context_info.report_id,
            "status": ledger.payload.get("status"),
            "resumed": True,
            "tree_version": (ledger.payload.get("tree") or {}).get("version") if isinstance(ledger.payload.get("tree"), Mapping) else 0,
            "control": ledger.control_summary(),
        }


def close_report(context: HandlerContext, action: SPAction, *, reason: str) -> dict[str, Any]:
    context_info = report_context(context.state)
    if not context_info.is_enabled:
        raise ReportValidationError("no report exists to close")
    paths = _paths_for_context(context_info)
    with report_store.report_lock(paths):
        ledger = ReportLedger.load(paths)
        if ledger.payload.get("status") == "abandoned":
            return {"report_id": context_info.report_id, "status": "abandoned", "closed": False}
        input_id = str(action.metadata.get("report_input_id") or "").strip()
        if not input_id:
            raise ReportValidationError("closing a report requires metadata.report_input_id citing the user's cancellation input")
        input_record = ledger.input(input_id)
        if input_record is None:
            raise ReportValidationError(f"closing a report cites an unknown user input: {input_id}")
        clean_reason = str(reason or input_record.get("preview") or "").strip()
        if not clean_reason:
            raise ReportValidationError("closing a report requires an explicit reason")
        ledger.payload["status"] = "abandoned"
        ledger.payload["closed"] = {
            "reason": clean_reason,
            "input_id": input_id,
            "input_path": input_record.get("virtual_path"),
            "input_preview": input_record.get("preview"),
            "at": utc_now_iso(),
        }
        ledger.payload["outline_review_due"] = False
        ledger.save()
        return {"report_id": context_info.report_id, "status": "abandoned", "closed": True, "reason": clean_reason}


def prepare_delegation(
    action: SPAction,
    context: HandlerContext,
    task_metadata: dict[str, Any],
    context_refs: dict[str, Any],
) -> None:
    """Bind report identity and input packets before the executor runs."""
    role = str(action.target_agent or "")
    if role not in REPORT_DELEGATE_ROLES:
        return
    report = report_context(context.state)
    if report.status == "disabled":
        return
    if report.status == "error":
        raise ReportValidationError(f"report ledger unavailable; refusing unbound report delegation: {report.error}")
    if report.status in {"delivered", "abandoned"}:
        # Historical reports stay readable but do not bind new delegations.
        # Unrelated work proceeds through the ordinary role path; report work
        # must start a new report or explicitly resume the delivered one first.
        return
    paths = _paths_for_context(report)
    if role == "reporter":
        key = str(task_metadata.get("report_write_key") or action.metadata.get("report_write_key") or "").strip()
        if not key:
            raise ReportValidationError("report Writer delegation requires metadata.report_write_key")
        with report_store.report_lock(paths):
            ledger = ReportLedger.load(paths)
            request = report_writing.require_write_request(ledger, key)
            node_ids = list(request.get("node_ids") or [])
            if len(node_ids) != 1:
                raise ReportValidationError(f"write request {key} must target exactly one node")
            attempt = report_writing.begin_writer_attempt(ledger, key, action_id=action.action_id, node_id=str(node_ids[0]))
            basis = report_packets.writer_basis(ledger, str(node_ids[0]))
            packet = report_packets.build_writer_packet(
                ledger,
                write_key=key,
                node_id=str(node_ids[0]),
                draft_virtual_path=attempt["virtual_path"],
                basis=basis,
                prior_draft=attempt.get("prior_draft"),
            )
            ledger.save()
        task_metadata["report_id"] = report.report_id
        task_metadata["report_role"] = "writer"
        task_metadata["report_delegate_action_id"] = action.action_id
        task_metadata["report_write_key"] = key
        task_metadata["report_node_id"] = str(node_ids[0])
        task_metadata["report_draft_id"] = attempt["draft_id"]
        task_metadata["report_draft_path"] = attempt["virtual_path"]
        task_metadata["report_draft_basis"] = basis
        context_refs["report_packet"] = packet
        return
    if role == "writer":
        raise ReportValidationError("writer binding must come from a reporter delegation with report_write_key")
    if role == "researcher":
        raw_gap_ids = task_metadata.get("report_gap_ids", action.metadata.get("report_gap_ids"))
        gap_ids = _normalize_report_gap_ids(raw_gap_ids)
        with report_store.report_lock(paths):
            ledger = ReportLedger.load(paths)
            if not gap_ids:
                if ledger.payload.get("tree") or ledger.gaps():
                    raise ReportValidationError("pre-research is only available before the first outline; dispatch each registered gap with metadata.report_gap_ids")
                ledger.begin_research_action(action.action_id, [], allow_empty=True, kind="pre_research")
                ledger.save()
                packet = report_packets.build_pre_research_packet(ledger, action_id=action.action_id)
                task_metadata["report_pre_research"] = True
            else:
                ledger.begin_research_action(action.action_id, gap_ids)
                ledger.save()
                packet = report_packets.build_researcher_packet(ledger, gap_ids, action_id=action.action_id)
            task_metadata["report_id"] = report.report_id
            task_metadata["report_role"] = role
            task_metadata["report_delegate_action_id"] = action.action_id
            if gap_ids:
                task_metadata["report_gap_ids"] = gap_ids
            context_refs["report_packet"] = packet
        return
    with report_store.report_lock(paths):
        ledger = ReportLedger.load(paths)
        report_task = str(task_metadata.get("report_task") or action.metadata.get("report_task") or "plan").strip().lower()
        if report_task not in {"plan", "revise", "integrate_review"}:
            raise ReportValidationError("report_task must be plan, revise, or integrate_review")
        if report_task == "integrate_review":
            report_assembly.ensure_candidate(ledger)
            packet = report_packets.build_integrate_review_packet(ledger)
        else:
            packet = report_packets.build_outline_packet(ledger)
    task_metadata["report_id"] = report.report_id
    task_metadata["report_role"] = role
    task_metadata["report_task"] = report_task
    task_metadata["report_delegate_action_id"] = action.action_id
    context_refs["report_packet"] = packet


def _normalize_report_gap_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ReportValidationError("report_gap_ids must be a list of non-empty gap ids")
    return list(dict.fromkeys(item.strip() for item in value))


def configure_report_subagent(
    task: SPSubagentTask,
    *,
    state: Mapping[str, Any] | None,
    available_tools: list[Any],
    subagent_config: Any,
) -> tuple[Any, list[Any], str | None]:
    """Assemble one report role's prompt, Skill, and tool set."""
    role = str(task.metadata.get("report_role") or "")
    if role not in REPORT_DELEGATE_ROLES:
        return subagent_config, available_tools, None
    report = active_report_required(state)
    if task.metadata.get("report_id") != report.report_id:
        raise ReportValidationError("report delegation identity does not match the active report")
    if role == "reporter":
        raise ReportValidationError("reporter must be bound as a writer delegation with report_write_key")
    paths = report_store.ReportPaths(report.outputs_path, report.report_id)  # type: ignore[arg-type]
    tools_by_name = {str(getattr(tool, "name", "")): tool for tool in available_tools}
    if role == "outline":
        report_task = str(task.metadata.get("report_task") or "plan").strip().lower()
        selected = [tools_by_name[name] for name in REPORT_READ_TOOLS if name in tools_by_name]
        if "read_file" not in tools_by_name:
            raise ReportValidationError("report outline requires a configured read_file tool to read saved materials")
        register_tool = None
        if report_task != "integrate_review":
            register_tool = build_register_research_request_tool(paths=paths, action_id=task.action_id)
            selected.append(register_tool)
        prompt = REPORT_INTEGRATE_REVIEW_PROMPT if report_task == "integrate_review" else REPORT_OUTLINE_PROMPT
        config = replace(
            subagent_config,
            system_prompt=_join_prompt(subagent_config.system_prompt, prompt),
            tools=[str(getattr(tool, "name", "")) for tool in selected],
            skills=["report-outline"],
        )
        return config, ([*available_tools, register_tool] if register_tool is not None else list(available_tools)), role

    if role == "writer":
        if "write_file" not in tools_by_name:
            raise ReportValidationError("report Writer requires a configured write_file tool")
        selected = [tools_by_name[name] for name in [*REPORT_READ_TOOLS, "write_file"] if name in tools_by_name]
        config = replace(
            subagent_config,
            system_prompt=REPORT_WRITER_PROMPT,
            tools=[str(getattr(tool, "name", "")) for tool in selected],
            skills=["report-node-writing"],
        )
        return config, list(available_tools), role

    fetch_tool = tools_by_name.get("web_fetch")
    if fetch_tool is None:
        raise ReportValidationError("report research requires a configured web_fetch tool")
    gap_ids = _normalize_report_gap_ids(task.metadata.get("report_gap_ids"))
    material_tool = build_fetch_material_tool(
        paths=paths,
        action_id=task.action_id,
        gap_ids=gap_ids,
        fetch_tool=fetch_tool,
    )
    selected = [tools_by_name[name] for name in REPORT_READ_TOOLS if name in tools_by_name]
    if "web_search" in tools_by_name:
        selected.append(tools_by_name["web_search"])
    selected.append(material_tool)
    config = replace(
        subagent_config,
        system_prompt=_join_prompt(subagent_config.system_prompt, REPORT_RESEARCH_PROMPT),
        tools=[str(getattr(tool, "name", "")) for tool in selected],
        skills=["report-research"],
    )
    return config, [*available_tools, material_tool], role


def _join_prompt(base: str | None, section: str) -> str:
    return "\n\n".join(part for part in (str(base or "").strip(), section.strip()) if part)


def handle_success(
    action: SPAction,
    context: HandlerContext,
    result: SPSubagentResult,
    task: SPSubagentTask,
    *,
    artifact_adapter: SPArtifactAdapter,
    delegate_entry: StackMemoryEntry,
) -> HandlerResult | None:
    role = str(task.metadata.get("report_role") or "")
    if role == "outline":
        if str(task.metadata.get("report_task") or "").strip().lower() == "integrate_review":
            return _accept_integrate_review(action, context, result, task, artifact_adapter=artifact_adapter, delegate_entry=delegate_entry)
        return _accept_outline(action, context, result, task, artifact_adapter=artifact_adapter, delegate_entry=delegate_entry)
    if role == "researcher":
        return _accept_research(action, context, result, task, artifact_adapter=artifact_adapter, delegate_entry=delegate_entry)
    if role == "writer":
        return _accept_writer(action, context, result, task, artifact_adapter=artifact_adapter, delegate_entry=delegate_entry)
    return None


def handle_failure(
    action: SPAction,
    context: HandlerContext,
    result: SPSubagentResult,
    task: SPSubagentTask,
) -> None:
    if str(task.metadata.get("report_role") or "") == "writer":
        key = str(task.metadata.get("report_write_key") or "")
        report = report_context(context.state)
        if report.is_active:
            try:
                paths = _paths_for_context(report)
                with report_store.report_lock(paths):
                    ledger = ReportLedger.load(paths)
                    report_writing.fail_writer_attempt(
                        ledger,
                        key=key,
                        action_id=action.action_id,
                        error=result.error or f"writer delegation failed: {result.status.value}",
                    )
                    ledger.save()
            except Exception:  # noqa: BLE001 - preserve the real execution error
                pass
        return
    if str(task.metadata.get("report_role") or "") != "researcher":
        return
    report = report_context(context.state)
    if not report.is_active:
        return
    try:
        paths = _paths_for_context(report)
        with report_store.report_lock(paths):
            ledger = ReportLedger.load(paths)
            ledger.fail_research_action(action.action_id, result.error or f"research delegation failed: {result.status.value}")
            ledger.save()
    except Exception:  # noqa: BLE001 - failure bookkeeping must not replace the real error
        return


def fail_delegation_action(state: Mapping[str, Any] | None, action_id: str, error: str, *, task: SPSubagentTask | None = None) -> None:
    """Release an in-flight report action after an executor exception."""
    report = report_context(state)
    if not report.is_active:
        return
    try:
        paths = _paths_for_context(report)
        with report_store.report_lock(paths):
            ledger = ReportLedger.load(paths)
            key = ""
            if task is not None and str(task.metadata.get("report_role") or "") == "writer":
                key = str(task.metadata.get("report_write_key") or "").strip()
            if not key:
                request = next(
                    (item for item in report_writing.write_requests(ledger) if action_id in {attempt.get("action_id") for attempt in item.get("attempts") or [] if isinstance(attempt, Mapping)}),
                    None,
                )
                key = str((request or {}).get("key") or "")
            if key:
                report_writing.fail_writer_attempt(ledger, key=key, action_id=action_id, error=error)
            else:
                ledger.fail_research_action(action_id, error)
            ledger.save()
    except Exception:  # noqa: BLE001 - the original execution error is more useful
        return


def _accept_integrate_review(
    action: SPAction,
    context: HandlerContext,
    result: SPSubagentResult,
    task: SPSubagentTask,
    *,
    artifact_adapter: SPArtifactAdapter,
    delegate_entry: StackMemoryEntry,
) -> HandlerResult:
    report = active_report_required(context.state)
    paths = _paths_for_context(report)
    with report_store.report_lock(paths):
        return _accept_integrate_review_locked(
            action,
            context,
            result,
            task,
            artifact_adapter=artifact_adapter,
            delegate_entry=delegate_entry,
            paths=paths,
        )


def _parse_integration_review(result: SPSubagentResult) -> dict[str, Any]:
    metadata = result.artifact_metadata if isinstance(result.artifact_metadata, Mapping) else {}
    if str(metadata.get("report_task") or "") != "integrate_review":
        raise ReportValidationError("integration review result must declare artifact_metadata.report_task=integrate_review")
    raw = result.artifact_content
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ReportValidationError("integration review artifact_content must be a JSON object") from exc
    elif isinstance(raw, Mapping):
        payload = dict(raw)
    else:
        raise ReportValidationError("integration review artifact_content must be a JSON object")
    review = payload.get("review")
    if not isinstance(review, Mapping):
        raise ReportValidationError("integration review artifact_content.review is required")
    status = str(review.get("status") or "").strip().lower()
    outcome = str(review.get("outcome") or "").strip().lower()
    if status not in {"ready", "issues"}:
        raise ReportValidationError("review.status must be ready or issues")
    if outcome not in {"deliver", "edit", "revise"}:
        raise ReportValidationError("review.outcome must be deliver, edit, or revise")
    findings = review.get("findings") or []
    edits = review.get("edits") or []
    if not isinstance(findings, list) or not isinstance(edits, list):
        raise ReportValidationError("review.findings and review.edits must be lists")
    if any(not isinstance(item, Mapping) for item in findings):
        raise ReportValidationError("review.findings must contain objects")
    if any(not isinstance(item, Mapping) for item in edits):
        raise ReportValidationError("review.edits must contain objects")
    covered = review.get("covered_requirement_ids") or []
    if not isinstance(covered, list) or any(not isinstance(item, str) for item in covered):
        raise ReportValidationError("review.covered_requirement_ids must be a list of strings")
    if outcome == "deliver":
        if status != "ready" or edits:
            raise ReportValidationError("deliver review must be ready and contain no edits")
        if any(str(item.get("severity") or "").lower() == "blocking" for item in findings):
            raise ReportValidationError("ready review cannot contain blocking findings")
    if outcome == "edit":
        if status != "issues" or not edits:
            raise ReportValidationError("edit review must be issues with at least one edit")
    if outcome == "revise" and edits:
        raise ReportValidationError("revise review cannot carry local edits; return them separately after revision")
    inputs_digest = payload.get("inputs_digest")
    candidate_sha = payload.get("reviewed_candidate_sha256")
    if not isinstance(inputs_digest, str) or not inputs_digest:
        raise ReportValidationError("integration review must echo inputs_digest")
    if not isinstance(candidate_sha, str) or not candidate_sha:
        raise ReportValidationError("integration review must echo reviewed_candidate_sha256")
    return {
        "inputs_digest": inputs_digest,
        "reviewed_candidate_sha256": candidate_sha,
        "review": {
            "status": status,
            "outcome": outcome,
            "findings": [dict(item) for item in findings],
            "edits": [dict(item) for item in edits],
            "covered_requirement_ids": covered,
        },
    }


def _write_review_record(ledger: ReportLedger, record: dict[str, Any]) -> None:
    path = ledger.paths.review_file(str(record["review_id"]))
    ledger.paths.reviews.mkdir(parents=True, exist_ok=True)
    report_store.write_json_atomic(path, record)
    record["path"] = str(path)
    record["virtual_path"] = ledger.paths.virtual(path)


def _finalize_integration_review(
    ledger: ReportLedger,
    *,
    candidate: Mapping[str, Any],
    review: dict[str, Any],
    artifact_adapter: SPArtifactAdapter,
    context: HandlerContext,
    action: SPAction,
    delegate_entry: StackMemoryEntry,
) -> tuple[dict[str, Any], Any]:
    candidate_path = Path(str(candidate.get("path") or ""))
    try:
        content = candidate_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReportValidationError(f"candidate file is unreadable: {exc}") from exc
    if report_store.sha256_text(content) != str(candidate.get("sha256") or ""):
        raise ReportValidationError("candidate content hash does not match the ledger")
    final_reports = ledger.payload.get("final_reports") if isinstance(ledger.payload.get("final_reports"), list) else []
    for item in final_reports:
        if isinstance(item, dict) and item.get("status") == "current":
            item["status"] = "superseded"
            item["is_current"] = False
            item["superseded_reason"] = "replaced by a newer reviewed final"
            item["superseded_at"] = utc_now_iso()
    versions = [int(item.get("version") or 0) for item in final_reports if isinstance(item, Mapping)]
    version = max(versions, default=0) + 1
    final_path = ledger.paths.final_file(version)
    report_store.write_text_atomic(final_path, content)
    if report_store.sha256_text(final_path.read_text(encoding="utf-8")) != str(candidate.get("sha256") or ""):
        raise ReportValidationError("final report file verification failed")
    virtual_path = ledger.paths.virtual(final_path)
    artifact = artifact_adapter.register_existing_artifact(
        virtual_path,
        artifact_type="final_report",
        state=context.state,
        thread_id=context.thread_id,
        run_id=context.run_id,
        created_by="outline",
        stage=action.stage,
        source_entry_id=delegate_entry.id,
        summary=f"Reviewed final report v{version}",
        metadata={
            "report_id": ledger.paths.report_id,
            "candidate_id": candidate.get("candidate_id"),
            "review_id": review.get("review_id"),
            "inputs_digest": review.get("inputs_digest"),
            "tree_version": candidate.get("tree_version"),
        },
    )
    final_record = {
        "version": version,
        "status": "current",
        "is_current": True,
        "path": str(final_path),
        "virtual_path": virtual_path,
        "sha256": str(candidate.get("sha256")),
        "chars": int(candidate.get("chars") or len(content)),
        "candidate_id": candidate.get("candidate_id"),
        "review_id": review.get("review_id"),
        "inputs_digest": review.get("inputs_digest"),
        "base_digest": review.get("base_digest"),
        "tree_version": candidate.get("tree_version"),
        "created_at": utc_now_iso(),
        "artifact_id": artifact.metadata.artifact_id,
    }
    report_store.write_json_atomic(ledger.paths.final_manifest(version), final_record)
    final_reports.append(final_record)
    ledger.payload["final_reports"] = final_reports
    ledger.payload["final_report"] = final_record
    ledger.payload["candidate"] = dict(candidate)
    candidate_record = ledger.payload["candidate"]
    candidate_record["status"] = "finalized"
    ledger.payload["review"] = review
    return final_record, artifact


def _accept_integrate_review_locked(
    action: SPAction,
    context: HandlerContext,
    result: SPSubagentResult,
    task: SPSubagentTask,
    *,
    artifact_adapter: SPArtifactAdapter,
    delegate_entry: StackMemoryEntry,
    paths: report_store.ReportPaths,
) -> HandlerResult:
    ledger = ReportLedger.load(paths)
    candidate = ledger.payload.get("candidate")
    if not isinstance(candidate, Mapping):
        return _rejection(action, context, "Integration review rejected: no current assembly candidate.", reason="review_candidate_missing")
    if candidate.get("status") not in report_assembly.ACTIVE_CANDIDATE_STATUSES:
        return _rejection(action, context, f"Integration review rejected: candidate is {candidate.get('status')}.", reason="review_candidate_not_current")
    try:
        parsed = _parse_integration_review(result)
    except ReportValidationError as exc:
        return _rejection(action, context, f"Integration review rejected: {exc}", reason="invalid_integration_review")
    if parsed["inputs_digest"] != candidate.get("inputs_digest"):
        return _rejection(
            action,
            context,
            "Integration review rejected: inputs_digest is stale; review the current candidate again.",
            reason="stale_review_digest",
        )
    if parsed["reviewed_candidate_sha256"] != candidate.get("sha256"):
        return _rejection(
            action,
            context,
            "Integration review rejected: reviewed_candidate_sha256 does not match the current candidate.",
            reason="stale_review_candidate",
        )
    try:
        report_assembly.candidate_file_content(candidate)
        if report_assembly.base_inputs_digest(ledger) != candidate.get("base_digest"):
            raise ReportValidationError("candidate inputs changed after assembly")
    except ReportValidationError as exc:
        return _rejection(action, context, f"Integration review rejected: {exc}", reason="stale_review_inputs")
    review_body = parsed["review"]
    reviews = ledger.payload.get("reviews") if isinstance(ledger.payload.get("reviews"), list) else []
    review_id = report_store.next_identifier("rv", [str(item.get("review_id")) for item in reviews if isinstance(item, Mapping)])
    review_record = {
        "review_id": review_id,
        "candidate_id": candidate.get("candidate_id"),
        "candidate_sha256": candidate.get("sha256"),
        "base_digest": candidate.get("base_digest"),
        "inputs_digest": parsed["inputs_digest"],
        "reviewed_candidate_sha256": parsed["reviewed_candidate_sha256"],
        "tree_version": candidate.get("tree_version"),
        "status": review_body["status"],
        "outcome": review_body["outcome"],
        "findings": review_body["findings"],
        "covered_requirement_ids": review_body["covered_requirement_ids"],
        "edits": review_body["edits"],
        "action_id": action.action_id,
        "created_at": utc_now_iso(),
    }
    try:
        if review_body["edits"]:
            new_candidate = report_assembly.apply_edits(ledger, review_body["edits"])
            review_record["status"] = "edits_applied"
            review_record["result_candidate_id"] = new_candidate.get("candidate_id")
            _write_review_record(ledger, review_record)
            reviews.append(review_record)
            ledger.payload["reviews"] = reviews
            ledger.payload["review"] = review_record
            ledger.payload["outline_review_due"] = False
            ledger.save()
            observe_content = json.dumps(
                {
                    "report_id": ledger.paths.report_id,
                    "review_id": review_id,
                    "candidate_id": new_candidate.get("candidate_id"),
                    "status": "edits_applied",
                    "pending_review": True,
                    "edits_applied": len(review_body["edits"]),
                    "next_action": "delegate_outline_integrate_review",
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            entry = context.stack.append_observe(
                observe_content,
                actor="outline",
                thread_id=context.thread_id,
                run_id=context.run_id,
                stage=action.stage,
                priority="high",
                metadata={"action_id": action.action_id, "target_agent": "outline", "report_task": "integrate_review", "completion_status": "partial"},
            )
            return HandlerResult(
                next_step="continue",
                state_update={"sp_active_delegate_id": None, "sp_current_stage": action.stage or "verification"},
                memory_entries=[delegate_entry, entry],
                idempotency_key=action.idempotency_key,
                run_events=[
                    make_sp_event("sp.delegate.completed", action_id=action.action_id, run_id=context.run_id, target_agent="outline", completion_status="partial"),
                    make_sp_event(
                        "sp.report.integration_edits_applied",
                        action_id=action.action_id,
                        run_id=context.run_id,
                        report_id=ledger.paths.report_id,
                        review_id=review_id,
                        candidate_id=new_candidate.get("candidate_id"),
                    ),
                ],
            )
        if review_body["status"] != "ready" or review_body["outcome"] != "deliver":
            report_assembly.invalidate_review(ledger, "integration review reported issues")
            _write_review_record(ledger, review_record)
            reviews.append(review_record)
            ledger.payload["reviews"] = reviews
            ledger.payload["review"] = review_record
            ledger.payload["outline_review_due"] = False
            ledger.save()
            entry = context.stack.append_observe(
                json.dumps(
                    {
                        "report_id": ledger.paths.report_id,
                        "review_id": review_id,
                        "status": review_body["status"],
                        "outcome": review_body["outcome"],
                        "blocking_findings": sum(1 for item in review_body["findings"] if str(item.get("severity") or "").lower() == "blocking"),
                        "next_action": "revise",
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                actor="outline",
                thread_id=context.thread_id,
                run_id=context.run_id,
                stage=action.stage,
                priority="high",
                metadata={"action_id": action.action_id, "target_agent": "outline", "report_task": "integrate_review", "completion_status": "complete"},
            )
            return HandlerResult(
                next_step="continue",
                state_update={"sp_active_delegate_id": None, "sp_current_stage": action.stage or "revision"},
                memory_entries=[delegate_entry, entry],
                idempotency_key=action.idempotency_key,
                run_events=[
                    make_sp_event("sp.delegate.completed", action_id=action.action_id, run_id=context.run_id, target_agent="outline", completion_status="complete"),
                    make_sp_event("sp.report.integration_review_issues", action_id=action.action_id, run_id=context.run_id, report_id=ledger.paths.report_id, review_id=review_id),
                ],
            )
        _write_review_record(ledger, review_record)
        final_record, artifact = _finalize_integration_review(
            ledger,
            candidate=candidate,
            review=review_record,
            artifact_adapter=artifact_adapter,
            context=context,
            action=action,
            delegate_entry=delegate_entry,
        )
        reviews.append(review_record)
        ledger.payload["reviews"] = reviews
        ledger.payload["outline_review_due"] = False
        ledger.save()
    except (ReportValidationError, OSError, ValueError) as exc:
        return _rejection(action, context, f"Integration review could not be applied: {exc}", reason="integration_review_apply_failed")
    except Exception as exc:  # noqa: BLE001 - acceptance must fail closed
        return _rejection(action, context, f"Integration review acceptance failed: {type(exc).__name__}: {exc}", reason="integration_review_acceptance_failed")

    entry = context.stack.append_observe(
        json.dumps(
            {
                "report_id": ledger.paths.report_id,
                "review_id": review_id,
                "candidate_id": candidate.get("candidate_id"),
                "final_report": final_record.get("virtual_path"),
                "inputs_digest": final_record.get("inputs_digest"),
                "status": "ready",
                "next_action": "sp_finish",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        actor="outline",
        thread_id=context.thread_id,
        run_id=context.run_id,
        stage=action.stage,
        priority="high",
        result_ref=artifact.metadata.artifact_id,
        metadata={
            "action_id": action.action_id,
            "target_agent": "outline",
            "report_task": "integrate_review",
            "completion_status": "complete",
            "final_report": True,
            "review_id": review_id,
        },
    )
    state_update = _artifact_state_update(artifact.state_update)
    state_update.update({"sp_active_delegate_id": None, "sp_current_stage": action.stage or "verification"})
    return HandlerResult(
        next_step="continue",
        state_update=state_update,
        memory_entries=[delegate_entry, entry],
        artifact_refs=state_update.get("sp_current_artifact_refs") or {},
        idempotency_key=action.idempotency_key,
        run_events=[
            make_sp_event("sp.delegate.completed", action_id=action.action_id, run_id=context.run_id, target_agent="outline", completion_status="complete"),
            make_sp_event(
                "sp.report.integration_review_ready",
                action_id=action.action_id,
                run_id=context.run_id,
                report_id=ledger.paths.report_id,
                review_id=review_id,
                final_report=final_record.get("virtual_path"),
            ),
            make_sp_event(
                "sp.artifact.created",
                action_id=action.action_id,
                run_id=context.run_id,
                artifact_id=artifact.metadata.artifact_id,
                artifact_type="final_report",
                virtual_path=artifact.metadata.virtual_path,
            ),
        ],
    )


def _accept_writer(
    action: SPAction,
    context: HandlerContext,
    result: SPSubagentResult,
    task: SPSubagentTask,
    *,
    artifact_adapter: SPArtifactAdapter,
    delegate_entry: StackMemoryEntry,
) -> HandlerResult:
    context_info = active_report_required(context.state)
    paths = _paths_for_context(context_info)
    with report_store.report_lock(paths):
        return _accept_writer_locked(
            action,
            context,
            result,
            task,
            artifact_adapter=artifact_adapter,
            delegate_entry=delegate_entry,
        )


def _accept_writer_locked(
    action: SPAction,
    context: HandlerContext,
    result: SPSubagentResult,
    task: SPSubagentTask,
    *,
    artifact_adapter: SPArtifactAdapter,
    delegate_entry: StackMemoryEntry,
) -> HandlerResult:
    ledger = ledger_for_state(context.state)
    key = str(task.metadata.get("report_write_key") or "")
    node_id = str(task.metadata.get("report_node_id") or "")
    draft_id = str(task.metadata.get("report_draft_id") or "")
    virtual_path = str(task.metadata.get("report_draft_path") or "")
    basis = task.metadata.get("report_draft_basis") if isinstance(task.metadata.get("report_draft_basis"), Mapping) else {}
    if not key or not node_id or not draft_id or not virtual_path:
        return _rejection(action, context, "Writer delegation is missing host draft identity.", reason="writer_binding_missing")
    feedback_items = result.artifact_metadata.get("node_feedback")
    try:
        if feedback_items:
            if not isinstance(feedback_items, list):
                raise ReportValidationError("node_feedback must be a list")
            records = report_writing.record_writer_feedback(
                ledger,
                key=key,
                action_id=action.action_id,
                node_id=node_id,
                feedback_items=feedback_items,
            )
            ledger.save()
            observe_content = json.dumps(
                {
                    "report_id": ledger.paths.report_id,
                    "write_key": key,
                    "node_id": node_id,
                    "draft_accepted": False,
                    "feedback": [{"feedback_id": item["feedback_id"], "kind": item["kind"]} for item in records],
                    "next_action": "delegate_outline",
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            entry = context.stack.append_observe(
                observe_content,
                actor="writer",
                thread_id=context.thread_id,
                run_id=context.run_id,
                stage=action.stage,
                priority="high",
                metadata={
                    "action_id": action.action_id,
                    "target_agent": "reporter",
                    "completion_status": "partial",
                    "report_id": ledger.paths.report_id,
                    "report_role": "writer",
                    "report_write_key": key,
                },
            )
            return HandlerResult(
                next_step="continue",
                state_update={"sp_active_delegate_id": None, "sp_current_stage": action.stage or "reporting"},
                memory_entries=[delegate_entry, entry],
                idempotency_key=action.idempotency_key,
                run_events=[
                    make_sp_event("sp.delegate.completed", action_id=action.action_id, run_id=context.run_id, target_agent="reporter", completion_status="partial"),
                    make_sp_event("sp.report.node_feedback_recorded", action_id=action.action_id, run_id=context.run_id, report_id=ledger.paths.report_id, write_key=key, feedback_ids=[item["feedback_id"] for item in records]),
                ],
            )

        completion_status = str(result.artifact_metadata.get("completion_status") or "complete").strip().lower()
        if completion_status != "complete":
            raise ReportValidationError(f"Writer returned completion_status={completion_status} without node_feedback")
        declared_path = str(result.artifact_metadata.get("draft_path") or "").strip()
        if declared_path and declared_path != virtual_path:
            raise ReportValidationError("Writer returned a draft_path that does not match the host-assigned path")
        host_path = ledger.paths.draft_file(node_id, draft_id)
        if not host_path.exists():
            raise ReportValidationError(f"Writer did not create the host-assigned draft: {virtual_path}")
        content = host_path.read_text(encoding="utf-8")
        if not content.strip():
            raise ReportValidationError("Writer draft is empty")
        if len(content) > 2_000_000:
            raise ReportValidationError("Writer draft exceeds the P2 draft size bound")
        artifact = artifact_adapter.register_existing_artifact(
            virtual_path,
            artifact_type="node_draft",
            state=context.state,
            thread_id=context.thread_id,
            run_id=context.run_id,
            created_by="writer",
            stage=action.stage,
            source_entry_id=delegate_entry.id,
            summary=f"Node draft {draft_id} for {node_id}",
            metadata={
                "report_id": ledger.paths.report_id,
                "write_key": key,
                "node_id": node_id,
                "draft_id": draft_id,
                "report_delegate_action_id": action.action_id,
            },
        )
        draft = report_writing.accept_draft(
            ledger,
            key=key,
            action_id=action.action_id,
            node_id=node_id,
            draft_id=draft_id,
            virtual_path=virtual_path,
            host_path=str(host_path),
            content=content,
            basis=basis,
            feedback_wanted=bool(report_writing.require_write_request(ledger, key).get("feedback_wanted")),
        )
        report_assembly.invalidate_review(ledger, "accepted draft changed")
        ledger.save()
    except ReportValidationError as exc:
        report_writing.fail_writer_attempt(ledger, key=key, action_id=action.action_id, error=str(exc))
        try:
            ledger.save()
        except Exception:  # noqa: BLE001 - preserve the rejection
            pass
        return _rejection(action, context, f"Writer result rejected: {exc}", reason="invalid_writer_result")
    except Exception as exc:  # noqa: BLE001 - persistence boundary must surface a technical fault
        report_writing.fail_writer_attempt(ledger, key=key, action_id=action.action_id, error=f"{type(exc).__name__}: {exc}")
        try:
            ledger.save()
        except Exception:  # noqa: BLE001 - preserve the original fault
            pass
        return _rejection(action, context, f"Writer acceptance failed: {type(exc).__name__}: {exc}", reason="writer_acceptance_failed")

    observe_content = json.dumps(
        {
            "report_id": ledger.paths.report_id,
            "write_key": key,
            "node_id": node_id,
            "draft_id": draft["draft_id"],
            "draft_path": draft["virtual_path"],
            "review_required": bool(draft.get("review_status") == "open"),
            "writes_pending": list(ledger.payload.get("writes_pending") or []),
            "next_action": "delegate_outline" if draft.get("review_status") == "open" else "continue",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    entry = context.stack.append_observe(
        observe_content,
        actor="writer",
        thread_id=context.thread_id,
        run_id=context.run_id,
        stage=action.stage,
        priority="high" if draft.get("review_status") == "open" else action.priority,
        result_ref=artifact.metadata.artifact_id,
        metadata={
            "action_id": action.action_id,
            "target_agent": "reporter",
            "completion_status": "complete",
            "report_id": ledger.paths.report_id,
            "report_role": "writer",
            "report_write_key": key,
            "draft_id": draft["draft_id"],
            "draft_review_due": draft.get("review_status") == "open",
        },
    )
    state_update = _artifact_state_update(artifact.state_update)
    state_update.update({"sp_active_delegate_id": None, "sp_current_stage": action.stage or "reporting"})
    return HandlerResult(
        next_step="continue",
        state_update=state_update,
        memory_entries=[delegate_entry, entry],
        artifact_refs=state_update.get("sp_current_artifact_refs") or {},
        idempotency_key=action.idempotency_key,
        run_events=[
            make_sp_event("sp.delegate.completed", action_id=action.action_id, run_id=context.run_id, target_agent="reporter", completion_status="complete"),
            make_sp_event(
                "sp.report.draft_accepted",
                action_id=action.action_id,
                run_id=context.run_id,
                report_id=ledger.paths.report_id,
                write_key=key,
                node_id=node_id,
                draft_id=draft["draft_id"],
                review_required=draft.get("review_status") == "open",
            ),
            make_sp_event("sp.artifact.created", action_id=action.action_id, run_id=context.run_id, artifact_id=artifact.metadata.artifact_id, artifact_type="node_draft", virtual_path=artifact.metadata.virtual_path),
        ],
    )


def _accept_outline(
    action: SPAction,
    context: HandlerContext,
    result: SPSubagentResult,
    task: SPSubagentTask,
    *,
    artifact_adapter: SPArtifactAdapter,
    delegate_entry: StackMemoryEntry,
) -> HandlerResult:
    context_info = active_report_required(context.state)
    paths = _paths_for_context(context_info)
    with report_store.report_lock(paths):
        return _accept_outline_locked(
            action,
            context,
            result,
            task,
            artifact_adapter=artifact_adapter,
            delegate_entry=delegate_entry,
        )


def _accept_outline_locked(
    action: SPAction,
    context: HandlerContext,
    result: SPSubagentResult,
    task: SPSubagentTask,
    *,
    artifact_adapter: SPArtifactAdapter,
    delegate_entry: StackMemoryEntry,
) -> HandlerResult:
    ledger = ledger_for_state(context.state)
    if ledger.action(action.action_id) is not None and ledger.action(action.action_id).get("status") == "completed":
        return _replay_result(action, context, "outline", ledger.action(action.action_id))
    try:
        payload = report_tree.parse_tree_payload(result.artifact_content)
        tree = report_tree.validate_tree(payload, ledger)
        user_decisions = ledger.apply_user_decisions(tree.get("user_decisions") or [])
        gap_outcomes = ledger.apply_gap_outcomes(tree.get("gap_outcomes") or [])
        fingerprints = report_tree.node_fingerprints(tree)
        feedback_disposed = report_writing.apply_feedback_dispositions(ledger, tree)
        draft_disposed = report_writing.apply_draft_dispositions(ledger, tree, fingerprints)
        write_requests = report_writing.apply_write_requests(ledger, tree)
        candidate, version, tree_path = report_tree.write_tree_candidate(tree, ledger)
        artifact = artifact_adapter.write_text_artifact(
            json.dumps(candidate, ensure_ascii=False, indent=2),
            artifact_type="outline",
            state=context.state,
            thread_id=context.thread_id,
            run_id=context.run_id,
            created_by="outline",
            stage=action.stage,
            source_entry_id=delegate_entry.id,
            filename_hint=f"report-{ledger.paths.report_id}-outline-v{version}.json",
            summary=f"Report outline v{version}: {candidate.get('report_title')}",
            metadata={
                "report_id": ledger.paths.report_id,
                "tree_version": version,
                "tree_path": str(tree_path),
                "report_delegate_action_id": action.action_id,
            },
        )
        report_tree.commit_tree(candidate, version=version, path=tree_path, ledger=ledger)
        report_assembly.invalidate_review(ledger, "accepted tree changed")
        ledger.save()
    except ReportValidationError as exc:
        return _rejection(action, context, f"Outline result rejected: {exc}", reason="invalid_report_tree")
    except Exception as exc:  # noqa: BLE001 - persistence boundary must surface a technical fault
        return _rejection(action, context, f"Report outline acceptance failed: {type(exc).__name__}: {exc}", reason="report_outline_acceptance_failed")

    gap_summary = [{"gap_id": gap.get("gap_id"), "status": gap.get("status"), "rounds": gap.get("rounds"), "next_action": gap_next_action(gap)} for gap in ledger.gaps() if gap.get("status") not in {"resolved", "not_needed"}]
    observe_content = json.dumps(
        {
            "report_id": ledger.paths.report_id,
            "tree_version": version,
            "tree_path": artifact.metadata.virtual_path,
            "nodes": [node.get("node_id") for node in candidate.get("nodes") or []],
            "gap_outcomes": gap_outcomes,
            "user_decisions": user_decisions,
            "feedback_disposed": feedback_disposed,
            "draft_disposed": draft_disposed,
            "write_requests": write_requests,
            "open_gaps": gap_summary,
            "material_catalog_path": ledger.paths.virtual(ledger.paths.catalog),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    observe_entry = context.stack.append_observe(
        observe_content,
        actor="outline",
        thread_id=context.thread_id,
        run_id=context.run_id,
        stage=action.stage,
        priority=action.priority,
        result_ref=artifact.metadata.artifact_id,
        metadata={
            "action_id": action.action_id,
            "target_agent": "outline",
            "completion_status": "complete",
            "report_id": ledger.paths.report_id,
            "tree_version": version,
            "report_role": "outline",
        },
    )
    state_update = _artifact_state_update(artifact.state_update)
    state_update.update({"sp_active_delegate_id": None, "sp_current_stage": action.stage or "planning"})
    return HandlerResult(
        next_step="continue",
        state_update=state_update,
        memory_entries=[delegate_entry, observe_entry],
        artifact_refs=state_update.get("sp_current_artifact_refs") or {},
        idempotency_key=action.idempotency_key,
        run_events=[
            make_sp_event("sp.delegate.completed", action_id=action.action_id, run_id=context.run_id, target_agent="outline", completion_status="complete"),
            make_sp_event(
                "sp.report.outline_accepted",
                action_id=action.action_id,
                run_id=context.run_id,
                report_id=ledger.paths.report_id,
                tree_version=version,
                artifact_id=artifact.metadata.artifact_id,
            ),
            make_sp_event("sp.artifact.created", action_id=action.action_id, run_id=context.run_id, artifact_id=artifact.metadata.artifact_id, artifact_type="outline", virtual_path=artifact.metadata.virtual_path),
        ],
    )


def _accept_research(
    action: SPAction,
    context: HandlerContext,
    result: SPSubagentResult,
    task: SPSubagentTask,
    *,
    artifact_adapter: SPArtifactAdapter,
    delegate_entry: StackMemoryEntry,
) -> HandlerResult:
    context_info = active_report_required(context.state)
    paths = _paths_for_context(context_info)
    with report_store.report_lock(paths):
        return _accept_research_locked(
            action,
            context,
            result,
            task,
            artifact_adapter=artifact_adapter,
            delegate_entry=delegate_entry,
        )


def _accept_research_locked(
    action: SPAction,
    context: HandlerContext,
    result: SPSubagentResult,
    task: SPSubagentTask,
    *,
    artifact_adapter: SPArtifactAdapter,
    delegate_entry: StackMemoryEntry,
) -> HandlerResult:
    ledger = ledger_for_state(context.state)
    previous = ledger.action(action.action_id)
    if previous is not None and previous.get("status") == "completed":
        return _replay_result(action, context, "researcher", previous)
    summary = result.artifact_content if isinstance(result.artifact_content, str) else None
    if not summary or not summary.strip():
        summary = result.result if isinstance(result.result, str) and result.result.strip() else None
    if not summary:
        ledger.fail_research_action(action.action_id, "Researcher returned no usable summary")
        ledger.save()
        return _rejection(action, context, "Report research returned no usable summary; no round was charged.", reason="empty_research_summary")
    try:
        gap_attempts = _validate_gap_attempts(result.artifact_metadata.get("gap_attempts"), ledger, task)
        host_executions = _validate_host_executions(result.artifact_metadata.get("host_tool_execution_records"))
        summary_path = report_store.write_research_summary(ledger.paths, action.action_id, summary)
        artifact = artifact_adapter.write_text_artifact(
            summary,
            artifact_type="research_observation",
            state=context.state,
            thread_id=context.thread_id,
            run_id=context.run_id,
            created_by="researcher",
            stage=action.stage,
            source_entry_id=delegate_entry.id,
            filename_hint=f"report-{ledger.paths.report_id}-research-{action.action_id}.md",
            summary=f"Report research for {', '.join(str(item.get('gap_id')) for item in gap_attempts) or 'pre-research'}",
            metadata={
                "report_id": ledger.paths.report_id,
                "report_delegate_action_id": action.action_id,
                "summary_path": str(summary_path),
            },
        )
        research_action, changed = ledger.finish_research_action(
            action.action_id,
            gap_attempts=gap_attempts,
            summary_path=summary_path,
            artifact_id=artifact.metadata.artifact_id,
            host_tool_executions=host_executions,
        )
        ledger.save()
    except ReportValidationError as exc:
        _fail_action_quietly(context, action.action_id, str(exc))
        return _rejection(action, context, f"Research result rejected: {exc}", reason="invalid_research_result")
    except Exception as exc:  # noqa: BLE001 - persistence boundary must surface a technical fault
        _fail_action_quietly(context, action.action_id, f"{type(exc).__name__}: {exc}")
        return _rejection(action, context, f"Report research acceptance failed: {type(exc).__name__}: {exc}", reason="report_research_acceptance_failed")

    observe_content = json.dumps(
        {
            "report_id": ledger.paths.report_id,
            "action_id": action.action_id,
            "summary_path": artifact.metadata.virtual_path,
            "gap_attempts": changed,
            "next_action": "delegate_outline",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    observe_entry = context.stack.append_observe(
        observe_content,
        actor="researcher",
        thread_id=context.thread_id,
        run_id=context.run_id,
        stage=action.stage,
        priority=action.priority,
        result_ref=artifact.metadata.artifact_id,
        metadata={
            "action_id": action.action_id,
            "target_agent": "researcher",
            "completion_status": "complete",
            "report_id": ledger.paths.report_id,
            "report_gap_ids": list(research_action.get("gap_ids") or []),
            "report_role": "researcher",
        },
    )
    state_update = _artifact_state_update(artifact.state_update)
    state_update.update({"sp_active_delegate_id": None, "sp_current_stage": action.stage or "research"})
    return HandlerResult(
        next_step="continue",
        state_update=state_update,
        memory_entries=[delegate_entry, observe_entry],
        artifact_refs=state_update.get("sp_current_artifact_refs") or {},
        idempotency_key=action.idempotency_key,
        run_events=[
            make_sp_event("sp.delegate.completed", action_id=action.action_id, run_id=context.run_id, target_agent="researcher", completion_status="complete"),
            make_sp_event(
                "sp.report.research_accepted",
                action_id=action.action_id,
                run_id=context.run_id,
                report_id=ledger.paths.report_id,
                artifact_id=artifact.metadata.artifact_id,
                gap_attempts=changed,
            ),
            make_sp_event("sp.artifact.created", action_id=action.action_id, run_id=context.run_id, artifact_id=artifact.metadata.artifact_id, artifact_type="research_observation", virtual_path=artifact.metadata.virtual_path),
        ],
    )


def _validate_gap_attempts(value: Any, ledger: ReportLedger, task: SPSubagentTask) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ReportValidationError("artifact_metadata.gap_attempts must be a list")
    gap_ids = _normalize_report_gap_ids(task.metadata.get("report_gap_ids"))
    result: list[dict[str, Any]] = []
    seen_gap_ids: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ReportValidationError(f"gap_attempts[{index}] must be an object")
        gap_id = str(item.get("gap_id") or "").strip()
        if gap_id not in gap_ids:
            raise ReportValidationError(f"gap_attempts[{index}] references an unassigned gap: {gap_id}")
        if gap_id in seen_gap_ids:
            raise ReportValidationError(f"gap_attempts contains a duplicate entry for {gap_id}")
        seen_gap_ids.add(gap_id)
        execution = str(item.get("execution") or "").strip().lower()
        if execution not in {"searched", "not_attempted", "technical_error"}:
            raise ReportValidationError(f"gap_attempts[{index}].execution is invalid")
        material_ids = item.get("material_ids") or []
        if not isinstance(material_ids, list):
            raise ReportValidationError(f"gap_attempts[{index}].material_ids must be a list")
        for material_id in material_ids:
            if ledger.material(str(material_id)) is None:
                raise ReportValidationError(f"gap_attempts[{index}] references unknown material: {material_id}")
        result.append(
            {
                "gap_id": gap_id,
                "execution": execution,
                "material_ids": [str(material_id) for material_id in material_ids],
                "note": str(item.get("note") or "").strip(),
            }
        )
    return result


def _validate_host_executions(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ReportValidationError("host_tool_execution_records must be a list")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        result.append({"tool": str(item.get("tool") or "unknown"), "ok": bool(item.get("ok")), "detail": str(item.get("detail") or "")})
    return result


def _artifact_state_update(adapter_update: Mapping[str, Any]) -> dict[str, Any]:
    update = dict(adapter_update)
    refs = update.get("sp_current_artifact_refs")
    if isinstance(refs, Mapping):
        update["sp_current_artifact_refs"] = {**dict(refs), SP_CONCURRENT_MERGE_MODE_KEY: True}
    return update


def _replay_result(action: SPAction, context: HandlerContext, role: str, record: Mapping[str, Any]) -> HandlerResult:
    entry = context.stack.append_observe(
        json.dumps({"report_id": record.get("report_id"), "role": role, "already_accepted": True, "action_id": action.action_id}, ensure_ascii=False),
        actor=role,
        thread_id=context.thread_id,
        run_id=context.run_id,
        stage=action.stage,
        priority=action.priority,
        metadata={"action_id": action.action_id, "target_agent": role, "completion_status": "complete", "report_role": role},
    )
    return HandlerResult(
        next_step="continue",
        state_update={"sp_active_delegate_id": None},
        memory_entries=[entry],
        idempotency_key=action.idempotency_key,
        run_events=[make_sp_event("sp.report.result_replayed", action_id=action.action_id, run_id=context.run_id, report_role=role)],
    )


def _rejection(action: SPAction, context: HandlerContext, message: str, *, reason: str) -> HandlerResult:
    return HandlerResult(
        next_step="error_recoverable",
        state_update={"sp_active_delegate_id": None},
        idempotency_key=action.idempotency_key,
        error=message,
        run_events=[make_sp_event("sp.report.result_rejected", action_id=action.action_id, run_id=context.run_id, reason=reason, error=message)],
    )


def _fail_action_quietly(context: HandlerContext, action_id: str, message: str) -> None:
    try:
        report = report_context(context.state)
        if not report.is_active:
            return
        paths = _paths_for_context(report)
        with report_store.report_lock(paths):
            ledger = ReportLedger.load(paths)
            ledger.fail_research_action(action_id, message)
            ledger.save()
    except Exception:  # noqa: BLE001 - preserving the original rejection is more useful
        return


def record_human_feedback_input(
    state: Mapping[str, Any],
    feedback: str,
    *,
    pending: Mapping[str, Any] | None,
    source_message_id: str | None = None,
) -> dict[str, Any] | None:
    """Persist a HITL answer and return related gaps to awaiting_outline."""
    context = report_context(state)
    if not context.is_active:
        return None
    paths = _paths_for_context(context)
    with report_store.report_lock(paths):
        ledger = ReportLedger.load(paths)
        pending = pending or {}
        interaction_id = str(pending.get("interaction_id") or "") or None
        metadata = pending.get("metadata") if isinstance(pending.get("metadata"), Mapping) else {}
        gap_ids = _normalize_report_gap_ids(metadata.get("report_gap_ids"))
        input_id = ledger.add_user_input(
            feedback,
            source="hitl_feedback",
            interaction_id=interaction_id,
            source_message_id=source_message_id,
        )
        if gap_ids:
            ledger.mark_gaps_awaiting_outline(gap_ids, input_id=input_id)
        ledger.save()
        return {"input_id": input_id, "gap_ids": gap_ids}


def prepare_ask_human(action: SPAction, context: HandlerContext, interaction: Any) -> None:
    gap_ids = _normalize_report_gap_ids(action.metadata.get("report_gap_ids")) if action.metadata.get("report_gap_ids") else []
    if not gap_ids:
        return
    context_info = active_report_required(context.state)
    paths = _paths_for_context(context_info)
    with report_store.report_lock(paths):
        ledger = ReportLedger.load(paths)
        ledger.bind_interaction(gap_ids, interaction.interaction_id)
        ledger.save()


def finish_block_reason(context: HandlerContext) -> str | None:
    context_info = report_context(context.state)
    if context_info.status == "error":
        return f"Report state is unavailable: {context_info.error}"
    if context_info.status != "active":
        return None
    try:
        ledger = ReportLedger.load_for_context(context_info)
    except Exception as exc:  # noqa: BLE001 - delivery must not proceed on unreadable state
        return f"Report ledger cannot be read: {exc}"
    reason = ledger.block_finish_reason()
    return f"Active report cannot be delivered in P1/P2/P3: {reason}" if reason else None


def report_finish_target(context: HandlerContext) -> dict[str, Any] | None:
    """Return the ledger's current final refs or a historical report's refs."""
    context_info = report_context(context.state)
    if not context_info.is_enabled:
        return None
    try:
        ledger = ReportLedger.load_for_context(context_info)
    except Exception as exc:  # noqa: BLE001 - finish will surface the state error separately
        return {"status": "error", "error": str(exc), "refs": []}
    status = str(ledger.payload.get("status") or context_info.status)
    final = ledger.payload.get("final_report") if isinstance(ledger.payload.get("final_report"), Mapping) else None
    if status == "active" and final is not None:
        refs = {str(value) for value in (final.get("artifact_id"), final.get("virtual_path"), final.get("path")) if value}
        return {
            "status": "active",
            "final_artifact_id": final.get("artifact_id"),
            "final_virtual_path": final.get("virtual_path"),
            "final_path": final.get("path"),
            "refs": refs,
        }
    historical_refs: set[str] = set()
    for item in ledger.payload.get("final_reports") or []:
        if isinstance(item, Mapping):
            historical_refs.update(str(value) for value in (item.get("artifact_id"), item.get("virtual_path"), item.get("path")) if value)
    delivered = ledger.payload.get("delivered") if isinstance(ledger.payload.get("delivered"), Mapping) else None
    for item in [ledger.payload.get("final_report"), delivered, *(ledger.payload.get("delivery_history") or [])]:
        if isinstance(item, Mapping):
            historical_refs.update(str(value) for value in (item.get("artifact_id"), item.get("final_artifact_id"), item.get("final_path"), item.get("virtual_path"), item.get("path")) if value)
    target = {"status": status, "refs": historical_refs}
    if status == "delivered" and delivered is not None:
        target.update(
            {
                "delivered": dict(delivered),
                "delivery_id": delivered.get("delivery_id"),
                "action_id": delivered.get("action_id"),
                "run_id": delivered.get("run_id"),
                "final_artifact_id": delivered.get("final_artifact_id") or (final or {}).get("artifact_id"),
                "final_virtual_path": delivered.get("final_path") or (final or {}).get("virtual_path"),
            }
        )
    return target


def deliver_report(context: HandlerContext, action: SPAction) -> dict[str, Any] | None:
    """Perform the one-way delivery transaction only for the active report."""
    context_info = report_context(context.state)
    if context_info.status == "error":
        raise ReportValidationError(f"report state is unavailable: {context_info.error}")
    if not context_info.is_enabled or context_info.status != "active":
        return None
    paths = _paths_for_context(context_info)
    with report_store.report_lock(paths):
        ledger = ReportLedger.load(paths)
        if ledger.payload.get("status") != "active":
            return None
        existing = ledger.payload.get("delivered") if isinstance(ledger.payload.get("delivered"), Mapping) else None
        final = ledger.payload.get("final_report") if isinstance(ledger.payload.get("final_report"), Mapping) else None
        if existing is not None:
            if final is not None and existing.get("final_report_id") == final.get("candidate_id") and existing.get("sha256") == final.get("sha256"):
                return dict(existing)
            raise ReportValidationError("report is already delivered with a different final report")
        reason = ledger.block_finish_reason()
        if reason:
            raise ReportValidationError(reason)
        final_error = report_assembly.final_binding_error(ledger)
        if final_error:
            raise ReportValidationError(final_error)
        if final is None:
            raise ReportValidationError("no current final report")
        delivery_id = report_store.next_identifier(
            "dlv",
            [str(item.get("delivery_id")) for item in ledger.payload.get("delivery_history") or [] if isinstance(item, Mapping)] + [str((ledger.payload.get("delivered") or {}).get("delivery_id"))],
        )
        review = ledger.payload.get("review") if isinstance(ledger.payload.get("review"), Mapping) else {}
        delivered = {
            "delivery_id": delivery_id,
            "status": "delivered",
            "final_report_id": final.get("candidate_id"),
            "final_version": final.get("version"),
            "final_path": final.get("virtual_path"),
            "final_artifact_id": final.get("artifact_id"),
            "review_id": review.get("review_id"),
            "candidate_id": final.get("candidate_id"),
            "inputs_digest": final.get("inputs_digest"),
            "sha256": final.get("sha256"),
            "delivered_at": utc_now_iso(),
            "action_id": action.action_id,
            "run_id": context.run_id,
        }
        delivery_path = ledger.paths.delivery_file(delivery_id)
        report_store.write_json_atomic(delivery_path, delivered)
        ledger.payload["delivered"] = delivered
        ledger.payload["status"] = "delivered"
        ledger.save()
        return delivered


def control_context(state: Mapping[str, Any] | None) -> dict[str, Any] | None:
    context_info = report_context(state)
    if context_info.status == "disabled":
        return None
    if context_info.status == "error":
        return {"status": "error", "error": context_info.error, "instructions": CONTEXT_INSTRUCTIONS}
    try:
        ledger = ReportLedger.load_for_context(context_info)
    except Exception as exc:  # noqa: BLE001 - expose the technical fault, not a silent disable
        return {"status": "error", "error": str(exc), "instructions": CONTEXT_INSTRUCTIONS}
    return {"report": ledger.control_summary(), "instructions": CONTEXT_INSTRUCTIONS}


def report_delegate_is_serial(state: Mapping[str, Any] | None, tool_call_id: str) -> bool:
    """Return False for a report delegation that must wait for its sibling."""
    context_info = report_context(state)
    if not context_info.is_active:
        return True
    messages = state.get("messages") if isinstance(state, Mapping) else None
    if not isinstance(messages, list):
        return True
    latest = next((message for message in reversed(messages) if isinstance(message, AIMessage)), None)
    if latest is None or not isinstance(latest.tool_calls, list):
        return True
    report_call_ids: list[str] = []
    for call in latest.tool_calls:
        if not isinstance(call, Mapping) or str(call.get("name") or "") != "sp_delegate":
            continue
        args = call.get("args") if isinstance(call.get("args"), Mapping) else {}
        if str(args.get("target_agent") or "") in REPORT_DELEGATE_ROLES:
            report_call_ids.append(str(call.get("id") or ""))
    return not report_call_ids or report_call_ids[0] == tool_call_id


def gap_dispatch_summary(ledger: ReportLedger) -> dict[str, Any]:
    return {
        "report_id": ledger.payload.get("report_id"),
        "gaps": [{"gap_id": gap.get("gap_id"), "status": gap.get("status"), "next_action": gap_next_action(gap)} for gap in ledger.gaps() if gap.get("status") in ACTIVE_GAP_STATUSES],
    }
