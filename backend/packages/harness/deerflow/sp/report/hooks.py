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
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.thread_state import SP_CONCURRENT_MERGE_MODE_KEY
from deerflow.sp.actions.events import make_sp_event
from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.schema import HandlerResult, SPAction
from deerflow.sp.artifacts import SPArtifactAdapter
from deerflow.sp.memory import StackMemoryEntry
from deerflow.sp.report import packets as report_packets
from deerflow.sp.report import store as report_store
from deerflow.sp.report import tree as report_tree
from deerflow.sp.report.ledger import (
    ACTIVE_GAP_STATUSES,
    ReportLedger,
    ReportLedgerError,
    ReportValidationError,
    gap_next_action,
    utc_now_iso,
)
from deerflow.sp.report.materials import build_fetch_material_tool
from deerflow.sp.report.prompts import REPORT_OUTLINE_PROMPT, REPORT_RESEARCH_PROMPT
from deerflow.sp.report.tools import build_register_research_request_tool
from deerflow.sp.subagents import SPSubagentResult, SPSubagentTask
from deerflow.utils.messages import message_to_text

REPORT_DELEGATE_ROLES = frozenset({"outline", "researcher", "reporter"})
REPORT_READ_TOOLS = ("read_file", "grep", "glob", "ls")
REPORT_CONTEXT_MESSAGE_NAME = "sp_report_context"
CONTEXT_INSTRUCTIONS = """
<report-control>
This is derived control state from the active report ledger, injected before
your model call. Do not edit the ledger by writing files. Use gap ids exactly.
Follow each gap's next_action: open -> delegate researcher with
metadata.report_gap_ids; awaiting_outline -> delegate outline; awaiting_user ->
sp_ask_human with metadata.report_gap_ids; in_flight -> wait. After an Outline
result, inspect the returned tree version and gap changes. P1 has no final report;
sp_finish is blocked while a report is active.
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
    try:
        ledger = ReportLedger.load_for_context(context)
    except ReportLedgerError:
        return
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


def start_report(context: HandlerContext, action: SPAction) -> dict[str, Any]:
    existing = report_context(context.state)
    if existing.status == "error":
        raise ReportValidationError(f"report ledger unavailable: {existing.error}")
    if existing.is_enabled:
        return {
            "report_id": existing.report_id,
            "status": existing.status,
            "started": False,
            "message": "A report already exists; resume or continue it instead of starting a new one.",
        }

    try:
        outputs_path = report_store.outputs_path_from_state(context.state)
    except report_store.ReportStoreError as exc:
        raise ReportValidationError(str(exc)) from exc
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
    ledger = ReportLedger.load_for_context(context_info)
    if context_info.status == "abandoned":
        raise ReportValidationError("cannot resume an abandoned report; start a new report instead")
    if context_info.status == "delivered":
        ledger.payload.setdefault("delivery_history", []).append(ledger.payload.get("delivered"))
        ledger.payload["delivered"] = None
        ledger.payload["status"] = "active"
        ledger.payload["resumed_at"] = utc_now_iso()
        ledger.payload["review"] = None
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
    clean_reason = str(reason or "").strip()
    if not clean_reason:
        raise ReportValidationError("closing a report requires an explicit reason")
    ledger = ReportLedger.load_for_context(context_info)
    if ledger.payload.get("status") == "abandoned":
        return {"report_id": context_info.report_id, "status": "abandoned", "closed": False}
    ledger.payload["status"] = "abandoned"
    ledger.payload["closed"] = {
        "reason": clean_reason,
        "input_id": action.metadata.get("report_input_id"),
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
        raise ReportValidationError(f"report is {report.status}; resume it before delegating to {role}")
    if role == "reporter":
        raise ReportValidationError("P1 report mode has no Writer/reporter delegation; delegate to outline or researcher")
    ledger = ReportLedger.load_for_context(report)
    task_metadata["report_id"] = report.report_id
    task_metadata["report_role"] = role
    task_metadata["report_delegate_action_id"] = action.action_id
    if role == "researcher":
        raw_gap_ids = task_metadata.get("report_gap_ids", action.metadata.get("report_gap_ids"))
        gap_ids = _normalize_report_gap_ids(raw_gap_ids)
        if not gap_ids:
            if ledger.payload.get("tree") or ledger.gaps():
                raise ReportValidationError("pre-research is only available before the first outline; dispatch each registered gap with metadata.report_gap_ids")
            ledger.begin_research_action(action.action_id, [], allow_empty=True, kind="pre_research")
            ledger.save()
            packet = report_packets.build_pre_research_packet(ledger, action_id=action.action_id)
            task_metadata["report_pre_research"] = True
            context_refs["report_packet"] = packet
            return
        ledger.begin_research_action(action.action_id, gap_ids)
        ledger.save()
        packet = report_packets.build_researcher_packet(ledger, gap_ids, action_id=action.action_id)
        context_refs["report_packet"] = packet
        task_metadata["report_gap_ids"] = gap_ids
        return
    packet = report_packets.build_outline_packet(ledger)
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
        raise ReportValidationError("P1 report mode has no Writer/reporter configuration")
    paths = report_store.ReportPaths(report.outputs_path, report.report_id)  # type: ignore[arg-type]
    tools_by_name = {str(getattr(tool, "name", "")): tool for tool in available_tools}
    if role == "outline":
        register_tool = build_register_research_request_tool(paths=paths, action_id=task.action_id)
        selected = [tools_by_name[name] for name in REPORT_READ_TOOLS if name in tools_by_name]
        if "read_file" not in tools_by_name:
            raise ReportValidationError("report outline requires a configured read_file tool to read saved materials")
        selected.append(register_tool)
        config = replace(
            subagent_config,
            system_prompt=_join_prompt(subagent_config.system_prompt, REPORT_OUTLINE_PROMPT),
            tools=[str(getattr(tool, "name", "")) for tool in selected],
            skills=["report-outline"],
        )
        return config, [*available_tools, register_tool], role

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
        return _accept_outline(action, context, result, task, artifact_adapter=artifact_adapter, delegate_entry=delegate_entry)
    if role == "researcher":
        return _accept_research(action, context, result, task, artifact_adapter=artifact_adapter, delegate_entry=delegate_entry)
    return None


def handle_failure(
    action: SPAction,
    context: HandlerContext,
    result: SPSubagentResult,
    task: SPSubagentTask,
) -> None:
    if str(task.metadata.get("report_role") or "") != "researcher":
        return
    report = report_context(context.state)
    if not report.is_active:
        return
    try:
        ledger = ReportLedger.load_for_context(report)
        ledger.fail_research_action(action.action_id, result.error or f"research delegation failed: {result.status.value}")
        ledger.save()
    except Exception:  # noqa: BLE001 - failure bookkeeping must not replace the real error
        return


def _accept_outline(
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
        declared_executions = _validate_declared_executions(result.artifact_metadata.get("tool_execution_records"))
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
            declared_tool_executions=declared_executions,
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


def _validate_declared_executions(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ReportValidationError("tool_execution_records must be a list")
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
        ledger = ReportLedger.load_for_context(report)
        ledger.fail_research_action(action_id, message)
        ledger.save()
    except Exception:  # noqa: BLE001 - preserving the original rejection is more useful
        return


def record_human_feedback_input(
    state: Mapping[str, Any],
    feedback: str,
    *,
    pending: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Persist a HITL answer and return related gaps to awaiting_outline."""
    context = report_context(state)
    if not context.is_active:
        return None
    try:
        ledger = ReportLedger.load_for_context(context)
        pending = pending or {}
        interaction_id = str(pending.get("interaction_id") or "") or None
        metadata = pending.get("metadata") if isinstance(pending.get("metadata"), Mapping) else {}
        gap_ids = _normalize_report_gap_ids(metadata.get("report_gap_ids"))
        input_id = ledger.add_user_input(
            feedback,
            source="hitl_feedback",
            interaction_id=interaction_id,
            created_at=str(pending.get("created_at") or "") or None,
        )
        if gap_ids:
            ledger.mark_gaps_awaiting_outline(gap_ids, input_id=input_id)
        ledger.save()
        return {"input_id": input_id, "gap_ids": gap_ids}
    except Exception:  # noqa: BLE001 - HITL persistence must not block the answer itself
        return None


def prepare_ask_human(action: SPAction, context: HandlerContext, interaction: Any) -> None:
    gap_ids = _normalize_report_gap_ids(action.metadata.get("report_gap_ids")) if action.metadata.get("report_gap_ids") else []
    if not gap_ids:
        return
    context_info = active_report_required(context.state)
    ledger = ReportLedger.load_for_context(context_info)
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
    if reason:
        return f"Active report cannot be delivered in P1: {reason}"
    return "Active report has no accepted final report path in P1; sp_finish is not available."


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
