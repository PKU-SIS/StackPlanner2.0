"""Thin native lifecycle adapter. No routing model or independent chapter ledger."""

from __future__ import annotations

from collections.abc import Mapping
from functools import wraps
from pathlib import Path

from deerflow.runtime.user_context import get_effective_user_id
from deerflow.sp.report import store as native
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError, utc_now_iso
from deerflow.sp.report.turns.store import ReportTurnStore


def skill(name):
    # Kept outside public discovery so first-turn skill catalogs are unchanged.
    root = Path(__file__).resolve().parents[7]
    return (root / "skills/report-multiturn" / name / "SKILL.md").read_text()


def opted_request(state):
    from deerflow.sp.report.hooks import _visible_human_messages

    messages = _visible_human_messages(state)
    if not messages:
        return None
    message = messages[-1]
    binding = message.additional_kwargs.get("report_dialogue")
    if not isinstance(binding, Mapping) or binding.get("enabled") is not True:
        return None
    if not isinstance(binding.get("report_id"), str):
        return None
    return message, binding


def session(state, *, thread_id):
    """Require explicit current-message opt-in and a program-validated baseline."""
    request = opted_request(state)
    if request is None or not thread_id:
        return None
    message, binding = request
    from deerflow.sp.report.hooks import _message_id

    outputs = native.outputs_path_from_state(state)
    store = ReportTurnStore(outputs, user_id=get_effective_user_id(), thread_id=thread_id)
    index = store.index()
    for turn_id in index["requests"].values():
        prior = store.inspect(turn_id)
        if prior["status"] in {"preparing", "prepared", "executing"} and prior["message_id"] != _message_id(message):
            raise ReportValidationError("another report turn is still in progress")
    if index["head"]:
        report_ids = {ref["report_id"] for ref in index["versions"].values()}
        if binding["report_id"] not in report_ids:
            return None
        head = store.inspect(index.get("active_version") or index["head"])
        store.verify(head["turn_id"])
    else:
        paths = store._paths(binding["report_id"])
        if not paths.ledger.exists():
            return None
        ledger = ReportLedger.load(paths)
        if ledger.payload.get("status") != "delivered":
            return None
        # The initial input must differ from this follow-up's source identity.
        if any(item.get("source_message_id") == _message_id(message) for item in ledger.inputs()):
            return None
        store._accepted(paths)
        head = store.freeze(paths.report_id, resolved_query=native.read_text(paths.request))
    return store, message, head


def current_turn(state, *, thread_id):
    active = session(state, thread_id=thread_id)
    if active is None:
        return None
    store, message, head = active
    from deerflow.sp.report.hooks import _message_id

    turn_id = store.index()["requests"].get(_message_id(message))
    turn = store.inspect(turn_id) if turn_id else None
    if turn is not None:
        from deerflow.utils.messages import message_to_text

        if turn["raw_query"] != message_to_text(message):
            raise ReportValidationError("source message identity was reused with different content")
    return store, message, head, turn


def activate(store, turn, context):
    """Bind an independent copy, then invoke the existing native resume."""
    with store.locked():
        turn = store.inspect(turn["turn_id"])
        if turn["status"] == "executing":
            return turn
        if turn["status"] != "prepared":
            raise ReportValidationError("only a prepared report turn may execute")
        pointer = native.pointer_path(store.outputs)
        previous = native.read_json(pointer) if pointer.exists() else None
        paths = store._paths(turn["report_id"])
        try:
            native.write_json_atomic(pointer, {"report_id": paths.report_id})
            from deerflow.sp.actions.schema import ActionType, SPAction
            from deerflow.sp.report.hooks import resume_report

            resume_report(context, SPAction.create(ActionType.REPLAN, reason="Resume isolated report turn", task="resume", metadata={"report_action": "resume"}))
            ledger = ReportLedger.load(paths)
            ledger.payload["dialogue_turn"] = {"turn_id": turn["turn_id"], "resolved_query": turn["resolved_query"], "message_id": turn["message_id"], "owner": store.owner, "run_id": context.run_id}
            # First-report size bounds are historical for post-delivery edits.
            # Apply only a contract explicitly supplied for this user turn.
            from deerflow.sp.report.length import normalize_contract

            message, _ = opted_request(context.state)
            baseline_length = ledger.payload.pop("length_contract", None)
            ledger.payload["dialogue_turn"]["baseline_length_contract"] = baseline_length
            current_length = normalize_contract(message.additional_kwargs.get("report_length"))
            if current_length is not None:
                ledger.payload["length_contract"] = current_length
            # Preserve raw inputs and their files for provenance; project only
            # the complete derived requirement into the native execution inputs.
            ledger.payload["dialogue_historical_inputs"] = ledger.payload["inputs"]
            next_input = ledger.next_input_id()
            ledger.payload["inputs"] = []
            # Prevent collision with a historical u1 without rewriting it.
            text = turn["resolved_query"].strip()
            native.append_user_input_file(paths, next_input, text)
            ledger.payload["inputs"] = [
                {
                    "input_id": next_input,
                    "path": str(paths.input_file(next_input)),
                    "virtual_path": paths.virtual(paths.input_file(next_input)),
                    "sha256": native.sha256_text(text),
                    "chars": len(text),
                    "preview": text[:500],
                    "applied": False,
                    "source": "resolved_query",
                    "source_message_id": turn["message_id"],
                    "provenance": turn["resolved_query_provenance"],
                }
            ]
            native.write_request(paths, text)
            ledger.payload["request"] = {"path": str(paths.request), "virtual_path": paths.virtual(paths.request), "sha256": native.sha256_text(text), "chars": len(text), "source": "resolved_query"}
            ledger.payload["acceptance"] = {"status": "idle", "turn_namespace": f"{paths.report_id}-{turn['turn_id']}"}
            ledger.payload["outline_review_due"] = True
            ledger.payload["writer_pipeline"] = {"managed": False}
            from deerflow.sp.report.turns.uploads import ingest_message_uploads

            with native.report_lock(paths):
                ledger.payload["dialogue_turn"]["uploaded_materials"] = ingest_message_uploads(ledger, message, store.owner)
                ledger.save()
            turn["status"] = "executing"
            store._record(turn)
            return turn
        except Exception as exc:
            if previous is not None:
                native.write_json_atomic(pointer, previous)
            else:
                pointer.unlink(missing_ok=True)
            turn.update(status="failed", diagnostic={"phase": "activation", "error": str(exc)}, ended_at=utc_now_iso())
            store._record(turn)
            raise


def execution_binding(state):
    if opted_request(state) is None:
        return None
    context = native.load_report_context(state)
    if not context.is_active:
        return None
    ledger = ReportLedger.load_for_context(context)
    binding = ledger.payload.get("dialogue_turn")
    from deerflow.sp.report.hooks import _message_id

    request = opted_request(state)
    return binding if isinstance(binding, dict) and request is not None and binding.get("message_id") == _message_id(request[0]) else None


def scoped_refs(state, refs):
    """Exclude historical user messages and task memory from execution packets."""
    binding = execution_binding(state)
    if not binding:
        return refs
    # Native report packets contain evidence, current drafts and derived inputs.
    selected = {k: v for k, v in refs.items() if k.startswith("report_")}
    selected["original_query"] = binding["resolved_query"]
    selected["latest_user_input"] = binding["resolved_query"]
    selected["report_dialogue"] = binding
    return selected


def publish_delivered(context, ledger):
    binding = ledger.payload.get("dialogue_turn")
    if not isinstance(binding, dict):
        return
    store = ReportTurnStore(ledger.paths.outputs_path, **binding["owner"])
    store.publish(binding["turn_id"])


def requirement_state(version):
    result = {"version_id": version["turn_id"], "resolved_query": version["resolved_query"], "requirements_status": version.get("requirements_status", "resolved")}
    if version.get("native_requirement_context") is not None:
        result["native_requirement_context"] = version["native_requirement_context"]
    return result


def guard_sealed_report(context, report):
    """Sealing protects a native version even if a later request omits opt-in."""
    if not (report.outputs_path / "report_dialogue/index.json").exists():
        return
    if not context.thread_id:
        raise ReportValidationError("sealed report mutation requires a bound runtime thread")
    store = ReportTurnStore(report.outputs_path, user_id=get_effective_user_id(), thread_id=context.thread_id)
    index = store.index()
    protected = any(ref["report_id"] == report.report_id for ref in index["versions"].values())
    protected = protected or f"origin:{report.report_id}" in index["requests"]
    protected = protected or any((turn := store.inspect(turn_id)).get("report_id") == report.report_id and turn["status"] == "accepted" for turn_id in index["requests"].values())
    if protected:
        raise ReportValidationError("sealed report is immutable; fork a new dialogue turn before mutation")


def terminal_failure_update(state, runtime):
    """Finish an already ended dialogue turn; never start a recovery model loop."""
    if opted_request(state) is None:
        return None
    context = getattr(runtime, "context", None)
    thread_id = context.get("thread_id") if isinstance(context, Mapping) else None
    active = current_turn(state, thread_id=thread_id)
    if active is None or active[3] is None or active[3]["status"] not in {"failed", "cancelled"}:
        return None
    store, _, _, turn = active
    from langchain_core.messages import AIMessage

    diagnostic = turn.get("diagnostic") or {}
    if turn["status"] == "cancelled":
        outcome = "本轮已取消"
    elif diagnostic.get("reason") == "No changes saved":
        outcome = "本轮修改未完成：Writer未保存任何修改"
    elif diagnostic.get("reason") == "truncated Writer response" or diagnostic.get("stop_reason") in {"token_capped", "loop_capped", "turn_capped"}:
        outcome = "本轮修改未完成：执行已停止"
    else:
        outcome = "本轮修改未完成：执行失败"
    notice = f"{outcome}。本轮已结束，不会自动重派agent；已接受报告保持原样。诊断和用量记录已保留（{turn['turn_id']}）。"
    path = store.directory(turn["turn_id"]) / "response.md"
    if not path.exists():
        native.write_text_atomic(path, notice)
    return {
        "jump_to": "end",
        "sp_active_delegate_id": None,
        "sp_last_run_summary": notice,
        "messages": [AIMessage(id=f"report-terminal-{turn['turn_id']}", content=notice, additional_kwargs={"deerflow_error_fallback": True, "report_dialogue_failure": turn["turn_id"], "report_turn_status": turn["status"]})],
    }


def central_context(state, runtime):
    context = getattr(runtime, "context", None)
    thread_id = context.get("thread_id") if isinstance(context, Mapping) else None
    active = current_turn(state, thread_id=thread_id)
    if active is None:
        return None
    store, message, head, turn = active
    from deerflow.sp.report.hooks import _message_id

    return {
        "skill": skill("report-dialogue-routing"),
        "active_state": {"version_id": head["turn_id"], "resolved_query": turn["resolved_query"], "requirements_status": "resolved"} if turn and turn["status"] == "executing" else requirement_state(head),
        "history_index": store.inspect(),
        "current_request": {"message_id": _message_id(message), "text": str(message.content)},
        "length_policy": {
            "inherit_baseline_word_limits": False,
            "current_report_length": message.additional_kwargs.get("report_length"),
            "instruction": "First-report word bounds are historical, not current follow-up constraints. Apply only the latest user request for length; do not add inherited total/chapter word budgets to delegations.",
        },
        "turn": turn,
        "execution": execution_binding(state),
    }


def finalize_run(state, runtime, *, cancelled=False, error=None):
    binding = execution_binding(state)
    if not binding:
        context = getattr(runtime, "context", None)
        thread_id = context.get("thread_id") if isinstance(context, Mapping) else None
        active = current_turn(state, thread_id=thread_id)
        if active is not None and active[3] is not None and active[3]["status"] in {"read_only", "exact_reuse"}:
            # Keep the model-supplied archive response and the actual final UI
            # answer separately; they need not be identical after sp_replan.
            from langchain_core.messages import AIMessage

            from deerflow.utils.messages import message_to_text

            store, message, _, turn = active
            messages = list(state.get("messages") or [])
            position = next((i for i in range(len(messages) - 1, -1, -1) if messages[i] is message), None)
            final = next((m for m in reversed(messages[position + 1 :]) if isinstance(m, AIMessage)), None) if position is not None else None
            path = store.directory(turn["turn_id"]) / "visible_response.md"
            if final is not None and not final.tool_calls and message_to_text(final).strip() and not path.exists():
                native.write_text_atomic(path, message_to_text(final))
        if active is not None and active[3] is None:
            store, message, head, _ = active
            from deerflow.sp.report.hooks import _message_id

            if _message_id(message) in store.index().get("git_attempts", {}):
                store.archive_resolution_failure(
                    message_id=_message_id(message), raw_query=str(message.content), base_version_id=head["turn_id"], diagnostic={"error": str(error) if error else "Resolver did not prepare a turn", "cancelled": cancelled}
                )
            elif error is None:
                from langchain_core.messages import AIMessage

                from deerflow.utils.messages import message_to_text

                report = native.load_report_context(state)
                messages = list(state.get("messages") or [])
                position = next((i for i in range(len(messages) - 1, -1, -1) if messages[i] is message), None)
                final = next((m for m in reversed(messages[position + 1 :]) if isinstance(m, AIMessage)), None) if position is not None else None
                if report.status != "delivered" or report.report_id != head["report_id"]:
                    return None
                finish_reason = final.response_metadata.get("finish_reason") if final is not None else None
                visible = message_to_text(final).strip() if final is not None else ""
                incomplete = final is not None and not final.tool_calls and (not visible or finish_reason in {"length", "max_tokens"} or final.additional_kwargs.get("deerflow_error_fallback"))
                if cancelled or incomplete:
                    # No model retry and no success archive for an empty/partial
                    # terminal answer. Retain a full baseline and numeric diagnostics.
                    turn = store.archive_resolution_failure(
                        message_id=_message_id(message),
                        raw_query=message_to_text(message),
                        base_version_id=head["turn_id"],
                        diagnostic={"phase": "dialogue_completion", "finish_reason": finish_reason, "visible_chars": len(visible), "cancelled": cancelled, "run_id": context.get("run_id"), "usage": state.get("sp_usage")},
                    )
                    turn.update(native_delivery_created=False)
                    if cancelled:
                        turn["status"] = "cancelled"
                    store._record(turn)
                    if cancelled:
                        return None
                    reason = "模型输出达到长度上限" if finish_reason in {"length", "max_tokens"} else "模型没有返回完整回答"
                    notice = f"本轮未能完成回答：{reason}。报告未改动；失败记录与用量已保留。"
                    text = (visible + "\n\n" if visible else "") + notice
                    native.write_text_atomic(store.directory(turn["turn_id"]) / "response.md", text)
                    return {
                        "messages": [
                            AIMessage(
                                id=final.id, content=text, response_metadata=final.response_metadata, usage_metadata=final.usage_metadata, additional_kwargs={"deerflow_error_fallback": True, "report_dialogue_failure": turn["turn_id"]}
                            )
                        ]
                    }
                if final is not None and not final.tool_calls and visible:
                    turn = store.reuse(head["turn_id"], message_id=_message_id(message), raw_query=message_to_text(message), response=visible, mode="read_only")
                    turn.update(response_only=True, native_delivery_created=False, user_intent_fulfilment="not_programmatically_verified")
                    store._record(turn)
        return
    store = ReportTurnStore(native.outputs_path_from_state(state), **binding["owner"])
    diagnostic = {"error": str(error) if error else "Run ended without native delivery", "last_handler_result": state.get("sp_last_handler_result"), "run_id": binding.get("run_id"), "usage": state.get("sp_usage")}
    store.end(binding["turn_id"], status="cancelled" if cancelled else "failed", diagnostic=diagnostic)


def scoped_write_tool(tool, paths, assigned_path):
    """Preserve the native tool schema/runtime injection; restrict its destination."""
    allowed = {str(assigned_path), paths.virtual(assigned_path)}
    original = tool.func

    @wraps(original)
    def guarded(*args, **kwargs):
        if kwargs.get("path") not in allowed:
            raise ReportValidationError("revision writes must use the host-assigned path")
        return original(*args, **kwargs)

    return tool.model_copy(update={"func": guarded})


def archive_finish(context, action):
    """Complete a read/reuse turn from its verified archive, without native execution."""
    active = current_turn(context.state, thread_id=context.thread_id)
    if active is None or active[3] is None or active[3]["status"] not in {"exact_reuse", "read_only"}:
        return None
    if context.state.get("sp_pending_human_interaction") or action.metadata.get("__sp_parallel_nonterminal_sibling"):
        raise ReportValidationError("archive finish cannot bypass pending work")
    store, _, _, turn = active
    manifest = store.verify(turn["turn_id"])
    directory = store.directory(turn["turn_id"])
    response = native.read_text(directory / "response.md")
    if turn["status"] == "exact_reuse":
        ledger = native.read_json(directory / "artifacts/ledger.json")
        relative = Path(ledger["final_report"]["path"]).relative_to(manifest["origin_directory"])
        response += "\n\n[Accepted report snapshot](" + native.virtual_path_for(directory / "artifacts" / relative, store.outputs) + ")"
    from deerflow.sp.actions.schema import HandlerResult

    entry = context.stack.append_finish(response, thread_id=context.thread_id, run_id=context.run_id, metadata={"report_dialogue_turn": turn["turn_id"], "mode": turn["status"]})
    return HandlerResult(next_step="finish", state_update={"sp_active_delegate_id": None, "sp_current_stage": "finished", "sp_last_run_summary": response}, memory_entries=[entry], idempotency_key=action.idempotency_key)


def record_result(context, task, result):
    """Keep actual per-action usage/diagnostics beside the turn, outside the ledger."""
    active = current_turn(context.state, thread_id=context.thread_id)
    if active is None or active[3] is None:
        return
    import hashlib

    store, _, _, turn = active
    identity = hashlib.sha256(task.action_id.encode()).hexdigest()
    record = {
        "action_id": task.action_id,
        "role": task.metadata.get("report_role"),
        "run_id": context.run_id,
        "executor_status": result.status.value,
        "error": result.error,
        "stop_reason": result.stop_reason,
        "token_usage_records": result.token_usage_records,
    }
    native.write_json_atomic(store.directory(turn["turn_id"]) / "runs" / f"{identity}.json", record)


def record_response(context, response):
    active = current_turn(context.state, thread_id=context.thread_id)
    if active is None or active[3] is None or active[3]["status"] != "accepted":
        return
    store, _, _, turn = active
    path = store.directory(turn["turn_id"]) / "response.md"
    if not path.exists():
        native.write_text_atomic(path, response)


def direct_version_action(action, context):
    """Execute Central's version read/reuse decision without an agent loop."""
    import json

    from deerflow.sp.report.turns.tools import build_tools

    active = current_turn(context.state, thread_id=context.thread_id)
    if active is None:
        raise ReportValidationError("version actions require an opted-in accepted report")
    store, message, head, turn = active
    selected = str(action.metadata.get("version_id") or head["turn_id"])
    kind = str(action.metadata["report_action"]).strip().lower()
    if kind == "inspect_version":
        return json.loads(
            build_tools(store, message)[0].invoke({"version_id": selected, "view": str(action.metadata.get("view") or "provenance"), "start_line": action.metadata.get("start_line", 1), "end_line": action.metadata.get("end_line")})
        )
    mode = str(action.metadata.get("mode") or "exact_reuse")
    if mode not in {"exact_reuse", "read_only"}:
        raise ReportValidationError("reuse mode must be exact_reuse or read_only")
    if turn is not None and turn["status"] != mode:
        raise ReportValidationError("this message already has a different execution/archive; do not fork or reuse it again")
    return json.loads(build_tools(store, message, operation=mode)[1].invoke({"version_id": selected, "mode": mode, "response": str(action.metadata.get("response") or "")}))
