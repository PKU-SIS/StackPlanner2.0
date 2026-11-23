"""Version tools bound to host-authenticated report/session/message identities."""

from __future__ import annotations

import json
from pathlib import Path

from langchain.tools import tool

from deerflow.sp.report import store as native
from deerflow.sp.report.ledger import ReportValidationError
from deerflow.sp.report.turns.adapter import activate, current_turn, requirement_state, skill


def build_tools(store, message, *, operation="auto"):
    from deerflow.sp.report.hooks import _message_id
    from deerflow.utils.messages import message_to_text

    if not isinstance(operation, str) or operation not in {"auto", "patch", "exact_reuse", "read_only"}:
        raise ReportValidationError("invalid host report operation")
    message_id = _message_id(message)
    raw = message_to_text(message)

    def previous_decision():
        existing = store.index()["requests"].get(message_id)
        if existing:
            return store.inspect(existing)
        return None

    @tool("report_inspect_version")
    def inspect(version_id: str | None = None, view: str = "requirements", start_line: int = 1, end_line: int | None = None) -> str:
        """Read accepted snapshots: requirements, manifest, report, draft:<node_id>, tree,
        sources, provenance[:<node_id>], evidence:<node_id>, material:<material_id>.
        Provenance uses saved evidence lines; material accepts line ranges.
        """
        if version_id is None:
            return json.dumps(store.inspect(), ensure_ascii=False)
        if version_id not in store.index()["versions"]:
            raise ReportValidationError("only accepted versions can be inspected")
        if view == "provenance" or view.startswith("provenance:"):
            from deerflow.sp.report.turns.provenance import read_provenance

            return json.dumps(read_provenance(store, version_id, view.partition(":")[2] or None), ensure_ascii=False)
        manifest = store.verify(version_id)
        turn = store.inspect(version_id)
        result = {"version": turn}
        if view == "manifest":
            result["manifest"] = manifest
        elif view != "requirements":
            directory = store.directory(version_id) / "artifacts"
            ledger = native.read_json(directory / "ledger.json")
            if view.startswith("evidence:"):
                from deerflow.sp.report.citations import CITATION_RE

                node_id = view.removeprefix("evidence:")
                draft = next((d for d in ledger.get("drafts", []) if d.get("is_current") and d.get("node_id") == node_id), None)
                if not draft:
                    raise ReportValidationError("unknown snapshot evidence node")

                def saved_text(ref):
                    relative = Path(ref["path"]).relative_to(manifest["origin_directory"]).as_posix()
                    if relative not in manifest["files"] or manifest["files"][relative] != ref["sha256"]:
                        raise ReportValidationError("unbound snapshot evidence reference")
                    return native.read_text(directory / relative)

                content = saved_text(draft)
                ids = list(dict.fromkeys(m.group("source_id") for m in CITATION_RE.finditer(content)))
                evidence = []
                for identity in ids[:8]:
                    source = next((r for r in ledger.get("sources", []) if r.get("source_id") == identity), {})
                    item = {k: source.get(k) for k in ("source_id", "url", "title")}
                    item["source_id"] = identity
                    material = next((r for r in reversed(ledger.get("materials", [])) if r.get("source_id") == identity), None)
                    if material:
                        text = saved_text(material)
                        excerpt = text[:4000]
                        item.update(material_id=material["material_id"], material_sha256=material["sha256"], content_excerpt=excerpt, char_range=[1, len(excerpt)], total_chars=len(text), truncated=len(excerpt) < len(text))
                    else:
                        item.update(content_excerpt=None, missing_saved_material=True)
                    evidence.append(item)
                result = {
                    "version": {k: turn.get(k) for k in ("turn_id", "report_id", "status", "requirements_status")},
                    "node_id": node_id,
                    "content": content[:16000],
                    "draft_sha256": draft["sha256"],
                    "truncated": len(content) > 16000,
                    "evidence_sources": evidence,
                    "omitted_sources": ids[8:],
                    "scope": "verified saved snapshot excerpts; not fresh research or factual verification",
                }
                return json.dumps(result, ensure_ascii=False)
            if view == "sources":
                result["sources"] = [{k: row.get(k) for k in ("source_id", "url", "title")} for row in ledger.get("sources", [])]
                result["materials"] = [{k: row.get(k) for k in ("material_id", "source_id", "sha256", "lines", "title")} for row in ledger.get("materials", [])]
                return json.dumps(result, ensure_ascii=False)
            if view == "tree" or view.startswith("material:"):
                ref = ledger.get("tree") if view == "tree" else next((m for m in ledger.get("materials", []) if m.get("material_id") == view.removeprefix("material:")), None)
                if not ref:
                    raise ReportValidationError("unknown snapshot material/view")
                relative = Path(ref["path"]).relative_to(manifest["origin_directory"]).as_posix()
                if relative not in manifest["files"]:
                    raise ReportValidationError("snapshot material/view is not bound")
                saved = directory / relative
                if view == "tree":
                    result["tree"] = native.read_json(saved)
                    return json.dumps(result, ensure_ascii=False)
                lines = native.read_text(saved).splitlines()
                end = min(len(lines), start_line + 119) if end_line is None else end_line
                if type(start_line) is not int or type(end) is not int or start_line < 1 or end < start_line or end > len(lines) or end - start_line >= 800:
                    raise ReportValidationError("invalid saved material line range")
                content = "\n".join(lines[start_line - 1 : end])
                if len(content) > 50000:
                    raise ReportValidationError("saved material excerpt too large; request fewer lines")
                result.update(
                    content=content,
                    material_id=ref["material_id"],
                    source_id=ref["source_id"],
                    material_sha256=ref["sha256"],
                    lines=[start_line, end],
                    total_lines=len(lines),
                    source=next(({k: source.get(k) for k in ("source_id", "url", "title")} for source in ledger.get("sources", []) if source.get("source_id") == ref["source_id"]), None),
                )
                return json.dumps(result, ensure_ascii=False)
            ref = ledger.get("final_report") if view == "report" else next((d for d in ledger["drafts"] if d.get("is_current") and view == "draft:" + d["node_id"]), None)
            if not ref:
                raise ReportValidationError("unknown snapshot view")
            relative = Path(ref["path"]).relative_to(manifest["origin_directory"]).as_posix()
            if relative not in manifest["files"]:
                raise ReportValidationError("snapshot reference is not bound")
            text = native.read_text(directory / relative)
            result["content"] = text[:50000]
            result["truncated"] = len(text) > 50000
        return json.dumps(result, ensure_ascii=False)

    @tool("report_prepare_turn", return_direct=True)
    def prepare(base_version_id: str, resolved_query: str, change_hint: str = "") -> str:
        """PATCH ONLY: fork a report for requirements different from the selected base. Never use for unchanged whole-version restoration; use report_reuse_version instead."""
        previous = previous_decision()
        if previous is not None:
            return json.dumps(previous, ensure_ascii=False)
        return json.dumps(store.prepare(base_version_id, message_id=message_id, raw_query=raw, resolved_query=resolved_query, change_hint=change_hint), ensure_ascii=False)

    @tool("report_reuse_version", return_direct=True)
    def reuse(version_id: str, mode: str = "read_only" if operation == "read_only" else "exact_reuse", response: str = "") -> str:
        """EXACT_REUSE restores an accepted whole version by switching active_version only; accepted head does not advance. READ_ONLY archives an answer without switching versions. Neither mode starts Outline, Researcher or Writer."""
        if operation in {"exact_reuse", "read_only"} and mode != operation:
            raise ReportValidationError("reuse mode differs from Central's bound operation")
        previous = previous_decision()
        if previous is not None:
            return json.dumps(previous, ensure_ascii=False)
        return json.dumps(store.reuse(version_id, message_id=message_id, raw_query=raw, response=response, mode=mode), ensure_ascii=False)

    if operation == "patch":
        return [inspect, prepare]
    if operation in {"exact_reuse", "read_only"}:
        return [inspect, reuse]
    return [inspect, prepare, reuse]


def bind_profile(action, context, metadata, refs):
    if action.metadata.get("report_profile") != "git_agent":
        return False
    if action.target_agent != "perception":
        raise ReportValidationError("GitAgent must use the perception executor")
    operation = action.metadata.get("report_operation", "auto")
    if not isinstance(operation, str) or operation not in {"auto", "patch", "exact_reuse", "read_only"}:
        raise ReportValidationError("invalid Central report_operation")
    active = current_turn(context.state, thread_id=context.thread_id)
    if active is None:
        raise ReportValidationError("GitAgent requires explicit opt-in and accepted report baseline")
    store, message, head, turn = active
    if turn is not None:
        raise ReportValidationError("source message already decided; no duplicate GitAgent dispatch")
    from deerflow.sp.report.hooks import _message_id

    with store.locked():
        index = store.index()
        attempts = index.setdefault("git_attempts", {})
        identity = _message_id(message)
        if identity in attempts:
            raise ReportValidationError("GitAgent already dispatched for this source message")
        attempts[identity] = {"operation": operation, "status": "dispatched", "action_id": action.action_id, "run_id": context.run_id}
        store._save(index)
    metadata.update(report_role="git_agent", report_profile="git_agent", report_operation=operation)
    refs["report_dialogue"] = {"active_state": requirement_state(head), "history_index": store.inspect(), "current_request": str(message.content)}
    return True


def configure(task, state, config):
    from dataclasses import replace

    active = current_turn(state, thread_id=task.thread_id)
    if active is None:
        raise ReportValidationError("unbound GitAgent profile")
    store, message, _, turn = active
    if turn is not None:
        raise ReportValidationError("duplicate GitAgent execution")
    from deerflow.sp.report.hooks import _message_id

    operation = (store.index().get("git_attempts", {}).get(_message_id(message)) or {}).get("operation", "auto")
    selected = build_tools(store, message, operation=operation)
    contract = (
        "Legacy operation is not narrowed; choose the terminal operation from the user intent."
        if operation == "auto"
        else f"Host-bound report operation: {operation}. The available terminal tools and reuse mode enforce this Central decision. Do not switch operations."
    )
    prompt = skill("report-version-management") + "\n" + contract
    config = replace(
        config,
        system_prompt=prompt,
        tools=[t.name for t in selected],
        disallowed_tools=[],
        skills=[],
        max_turns=60 if operation != "auto" else min(config.max_turns, 50),
        tool_call_limit=8,
        loop_warning_instruction=(
            "Use the history and active state already supplied; avoid duplicate inspection. Keep at most six inspection calls and reserve one terminal version-tool call. When resolved, finish with "
            + ("report_prepare_turn" if operation == "patch" else "report_reuse_version")
            + "; do not return findings prose without the terminal handoff. Do not invent missing state if resolution is impossible."
            if operation != "auto"
            else config.loop_warning_instruction
        ),
        finalize_after_tool_calls=None,
    )
    return config, selected, "git_agent"


def accept(action, context, result):
    from deerflow.sp.actions.schema import HandlerResult

    active = current_turn(context.state, thread_id=context.thread_id)
    if active is None or active[3] is None:
        fail_profile(context, result)
        raise ReportValidationError("GitAgent returned without a version-tool terminal decision")
    store, message, _, turn = active
    from deerflow.sp.report.hooks import _message_id

    operation = (store.index().get("git_attempts", {}).get(_message_id(message)) or {}).get("operation", "auto")
    expected = {"patch": {"prepared", "executing"}, "exact_reuse": {"exact_reuse"}, "read_only": {"read_only"}}.get(operation)
    if expected is not None and turn["status"] not in expected:
        raise ReportValidationError("version-tool handoff violates Central operation contract")
    if turn["status"] == "prepared":
        turn = activate(store, turn, context)
    if turn["status"] not in {"executing", "exact_reuse", "read_only"}:
        raise ReportValidationError("GitAgent decision did not prepare a usable turn")
    with store.locked():
        index = store.index()
        attempt = index.setdefault("git_attempts", {}).get(turn["message_id"])
        if attempt is not None:
            attempt.update(status="terminal", turn_id=turn["turn_id"], token_usage_records=result.token_usage_records)
            store._save(index)
    entry = context.stack.append_observe(
        json.dumps({"report_turn_handoff": turn}, ensure_ascii=False),
        actor="perception",
        thread_id=context.thread_id,
        run_id=context.run_id,
        stage=action.stage,
        metadata={"report_role": "git_agent", "action_id": action.action_id, "completion_status": "complete"},
    )
    return HandlerResult(next_step="continue", state_update={"sp_active_delegate_id": None}, memory_entries=[entry], idempotency_key=action.idempotency_key)


def fail_profile(context, result):
    active = current_turn(context.state, thread_id=context.thread_id)
    if active is None:
        return
    store, message, _, turn = active
    from deerflow.sp.report.hooks import _message_id

    diagnostic = {"error": result.error, "stop_reason": result.stop_reason, "usage_records": result.token_usage_records}
    with store.locked():
        index = store.index()
        attempt = index.setdefault("git_attempts", {}).get(_message_id(message))
        if attempt is not None:
            attempt.update(status="failed", diagnostic=diagnostic)
            store._save(index)
    if turn is None:
        store.archive_resolution_failure(message_id=_message_id(message), raw_query=str(message.content), base_version_id=active[2]["turn_id"], diagnostic=diagnostic)
    if turn and turn["status"] in {"prepared", "executing"}:
        store.end(turn["turn_id"], status="cancelled" if result.status.value == "cancelled" else "failed", diagnostic=diagnostic)
