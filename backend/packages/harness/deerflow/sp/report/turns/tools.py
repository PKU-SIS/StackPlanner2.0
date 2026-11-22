"""Version tools bound to host-authenticated report/session/message identities."""

from __future__ import annotations

import json
from pathlib import Path

from langchain.tools import tool

from deerflow.sp.report import store as native
from deerflow.sp.report.ledger import ReportValidationError
from deerflow.sp.report.turns.adapter import activate, current_turn, requirement_state, skill


def build_tools(store, message):
    from deerflow.sp.report.hooks import _message_id
    from deerflow.utils.messages import message_to_text

    message_id = _message_id(message)
    raw = message_to_text(message)

    def previous_decision():
        existing = store.index()["requests"].get(message_id)
        if existing:
            return store.inspect(existing)
        return None

    @tool("report_inspect_version")
    def inspect(version_id: str | None = None, view: str = "requirements") -> str:
        """Read accepted report history. Views: requirements, manifest, report, draft:<node_id>."""
        if version_id is None:
            return json.dumps(store.inspect(), ensure_ascii=False)
        if version_id not in store.index()["versions"]:
            raise ReportValidationError("only accepted versions can be inspected")
        manifest = store.verify(version_id)
        turn = store.inspect(version_id)
        result = {"version": turn}
        if view == "manifest":
            result["manifest"] = manifest
        elif view != "requirements":
            directory = store.directory(version_id) / "artifacts"
            ledger = native.read_json(directory / "ledger.json")
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
        """Prepare an isolated native report copy using the complete current requirement; return to Central."""
        previous = previous_decision()
        if previous is not None:
            return json.dumps(previous, ensure_ascii=False)
        return json.dumps(store.prepare(base_version_id, message_id=message_id, raw_query=raw, resolved_query=resolved_query, change_hint=change_hint), ensure_ascii=False)

    @tool("report_reuse_version", return_direct=True)
    def reuse(version_id: str, mode: str = "exact_reuse", response: str = "") -> str:
        """Archive ExactReuse or a read_only answer without Researcher/Writer; return to Central."""
        previous = previous_decision()
        if previous is not None:
            return json.dumps(previous, ensure_ascii=False)
        return json.dumps(store.reuse(version_id, message_id=message_id, raw_query=raw, response=response, mode=mode), ensure_ascii=False)

    return [inspect, prepare, reuse]


def bind_profile(action, context, metadata, refs):
    if action.metadata.get("report_profile") != "git_agent":
        return False
    if action.target_agent != "perception":
        raise ReportValidationError("GitAgent must use the perception executor")
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
        attempts[identity] = {"status": "dispatched", "action_id": action.action_id, "run_id": context.run_id}
        store._save(index)
    metadata.update(report_role="git_agent", report_profile="git_agent")
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
    selected = build_tools(store, message)
    config = replace(config, system_prompt=skill("report-version-management"), tools=[t.name for t in selected], disallowed_tools=[], skills=[], max_turns=min(config.max_turns, 50), tool_call_limit=8, finalize_after_tool_calls=None)
    return config, selected, "git_agent"


def accept(action, context, result):
    from deerflow.sp.actions.schema import HandlerResult

    active = current_turn(context.state, thread_id=context.thread_id)
    if active is None or active[3] is None:
        fail_profile(context, result)
        raise ReportValidationError("GitAgent returned without a version-tool terminal decision")
    store, _, _, turn = active
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
