"""Writer-first editing of an accepted report; native ledger remains authoritative."""

from __future__ import annotations

import json
from dataclasses import replace

from langchain.tools import tool

from deerflow.sp.actions.events import make_sp_event
from deerflow.sp.actions.schema import HandlerResult
from deerflow.sp.report import assembly, packets, writing
from deerflow.sp.report import store as native
from deerflow.sp.report.citations import CITATION_RE
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError, utc_now_iso
from deerflow.sp.report.turns import adapter


def bind(action, context, metadata, refs):
    if action.target_agent != "reporter" or action.metadata.get("report_task") != "local_edit":
        return False
    active = adapter.current_turn(context.state, thread_id=context.thread_id)
    if active is None:
        raise ReportValidationError("local_edit requires an opted-in accepted report")
    store, message, head, turn = active
    if turn is not None:
        raise ReportValidationError("This message already has a report turn; continue or read its archive instead of dispatching another Writer")
    base, base_source = adapter.select_edit_base(message, head)
    store.verify(base)
    from deerflow.sp.report.hooks import _message_id
    from deerflow.utils.messages import message_to_text

    raw = message_to_text(message)
    # A bound editing contract, not an LLM reconstruction of old chat. The selected
    # accepted body defines what is retained; later instructions take precedence.
    resolved = json.dumps(
        {
            "mode": "local_edit",
            "base_version_id": base,
            "baseline": "Verified accepted snapshot; preserve its content except for the following edit.",
            "current_edit": raw,
            "precedence": (
                "Current edit overrides baseline. Historical chat is provenance, never a pending task list. Preserve evidence and citation constraints. Baseline word limits are historical; honor only the current user request for length."
            ),
        },
        ensure_ascii=False,
    )
    turn = store.prepare(base, message_id=_message_id(message), raw_query=raw, resolved_query=resolved, change_hint="Writer-first local edit")
    adapter.activate(store, turn, context)
    paths = store._paths(turn["report_id"])
    with native.report_lock(paths):
        ledger = ReportLedger.load(paths)
        ledger.register_run(context.run_id)
        # Previous Outline keep/rewrite decisions describe the old turn only.
        ledger.payload["dialogue_historical_draft_dispositions"] = ledger.payload.pop("draft_dispositions", [])
        ledger.payload["writer_first"] = {"action_id": action.action_id, "run_id": context.run_id, "base_version_id": base, "baseline_selection_source": base_source, "edits": []}
        ledger.save()
    metadata.pop("tool_names", None)
    metadata.update(report_id=paths.report_id, base_version_id=base, report_role="writer", report_writer_first=True, report_delegate_action_id=action.action_id)
    refs.clear()
    refs["report_local_edit"] = {
        "current_edit": raw,
        "base_version_id": base,
        "baseline_selection_source": base_source,
        "uploaded_materials": ledger.payload["dialogue_turn"].get("uploaded_materials", []),
        "section_catalog": [{"node_id": n["node_id"], "title": n.get("title", "")} for n in (packets.load_current_tree(ledger) or {}).get("nodes", []) if n.get("kind") == "writable"],
        "instruction": "Locate the section, read it, and replace only the requested text. Other sections remain byte-identical. Explain ambiguity or missing evidence rather than inventing facts.",
    }
    return True


def _body_chars(content):
    return len("\n".join(line for line in content.splitlines() if line.strip() and not line.startswith("#")))


def _length_guidance(ledger):
    """Preview the native rendered count without creating assembly files or gates."""
    contract = ledger.payload.get("length_contract")
    if not contract:
        return None
    from deerflow.sp.report.citation_display import render_final_citations
    from deerflow.sp.report.length import COUNTER_VERSION, estimate_words

    tree = packets.load_current_tree(ledger)
    sections = assembly._sections(ledger, tree)
    sources, _ = assembly._source_table(ledger, sections)
    rendered, _ = render_final_citations(assembly._candidate_text(tree, sections, sources), sources)
    count = estimate_words(rendered)
    return {
        "estimated_words": count,
        "min_words": contract["min_words"],
        "max_words": contract["max_words"],
        "additional_words_available": contract["max_words"] - count,
        "direction": "long" if count > contract["max_words"] else "short" if count < contract["min_words"] else "within",
        "counter_version": COUNTER_VERSION,
        "word_verified": False,
    }


def build_tools(paths, task, state):
    def load():
        ledger = ReportLedger.load(paths)
        session = ledger.payload.get("writer_first") or {}
        if session.get("action_id") != task.action_id or ledger.payload.get("status") != "active":
            raise ReportValidationError("local edit is no longer active for this Writer")
        return ledger

    def body(ledger, node_id):
        draft = writing.current_draft(ledger, node_id)
        if not draft or not draft.get("is_current"):
            return None, None
        return draft, assembly._bound_text(draft, f"draft {node_id}")

    @tool("report_find_sections")
    def find(query: str = "") -> str:
        """Find existing section IDs by title or body text. Empty query lists all titles."""
        ledger = load()
        tree = packets.load_current_tree(ledger)
        rows = []
        for node in tree.get("nodes", []):
            if node.get("kind") != "writable":
                continue
            _, content = body(ledger, node["node_id"])
            text = content or ""
            position = text.casefold().find(query.casefold()) if query else 0
            if not query or query.casefold() in node.get("title", "").casefold() or position >= 0:
                rows.append({"node_id": node["node_id"], "title": node.get("title"), "excerpt": text[max(0, position - 120) : max(0, position - 120) + 500]})
        return json.dumps({"sections": rows, "hint": "Use an empty query for the section catalog if no match."}, ensure_ascii=False)

    @tool("report_read_section")
    def read(node_id: str, version_id: str | None = None) -> str:
        """Read a current section and saved evidence IDs; optional accepted version_id reads historical text for selective undo."""
        ledger = load()
        if version_id:
            from deerflow.sp.report.turns.tools import build_tools as version_tools

            store, message, _, _ = adapter.current_turn(state, thread_id=task.thread_id)
            return version_tools(store, message)[0].invoke({"version_id": version_id, "view": "evidence:" + node_id})
        draft, content = body(ledger, node_id)
        if draft is None:
            return json.dumps({"status": "needs_adjustment", "hint": "Unknown section; use report_find_sections."})
        node = next(n for n in packets.load_current_tree(ledger)["nodes"] if n["node_id"] == node_id)
        cited = {m.group("source_id") for m in CITATION_RE.finditer(content)}
        cited.update(item.get("source_id") for item in node.get("evidence", []))
        return json.dumps(
            {
                "node_id": node_id,
                "title": node.get("title", ""),
                "content": content,
                "sha256": draft["sha256"],
                "section_metrics": {"body_chars": _body_chars(content)},
                "length_guidance": _length_guidance(ledger),
                "evidence": node.get("evidence", []),
                "sources": [{k: s.get(k) for k in ("source_id", "title", "url")} for s in ledger.payload.get("sources", []) if s.get("source_id") in cited],
            },
            ensure_ascii=False,
        )

    @tool("report_replace_text")
    def edit(node_id: str, old_text: str, new_text: str) -> str:
        """Replace one exact, unique span in an existing section. Read first. Unmatched/ambiguous text returns a hint without changing files. Replayed edits are idempotent."""
        with native.report_lock(paths):
            ledger = load()
            request_hash = native.sha256_text(json.dumps([node_id, old_text, new_text], ensure_ascii=False))
            edits = ledger.payload["writer_first"]["edits"]
            for saved in edits:
                if saved["request_hash"] == request_hash:
                    return json.dumps(saved["receipt"], ensure_ascii=False)
            draft, content = body(ledger, node_id)

            def adjust(hint):
                return json.dumps({"status": "needs_adjustment", "hint": hint}, ensure_ascii=False)

            if draft is None:
                return adjust("Unknown section. Find/read the correct node first.")
            if not old_text or content.count(old_text) != 1:
                return adjust("The old_text must match exactly once. Read the section again and use a longer unique span copied verbatim.")
            if old_text == new_text:
                return adjust("No text changed. Explain if the requested content already exists.")
            changed = content.replace(old_text, new_text, 1)
            if not changed.strip():
                return adjust("This removes the whole section. Ask Central for a structural Outline revision.")
            known = {s["source_id"] for s in ledger.payload.get("sources", [])}
            if any(m.group("source_id") not in known for m in CITATION_RE.finditer(changed)):
                return adjust("Unknown source marker. Use saved evidence IDs; ask Central for research if evidence is missing.")
            tree = packets.load_current_tree(ledger)
            # Imported baselines can already contain local keys from a turn
            # with the same number. Allocate from the authoritative native ledger.
            key = native.next_identifier("local", [str(item.get("key")) for item in writing.write_requests(ledger)])
            writing.apply_write_requests(ledger, {**tree, "write_requests": [{"key": key, "node_ids": [node_id], "feedback_wanted": False}]})
            attempt = writing.begin_writer_attempt(ledger, key, action_id=task.action_id, node_id=node_id, run_id=ledger.payload["writer_first"]["run_id"])
            basis = packets.writer_basis(ledger, node_id)
            native.write_text_atomic(attempt["draft_path"], changed)
            writing.accept_draft(
                ledger, key=key, action_id=task.action_id, node_id=node_id, draft_id=attempt["draft_id"], virtual_path=attempt["virtual_path"], host_path=str(attempt["draft_path"]), content=changed, basis=basis, feedback_wanted=False
            )
            assembly.invalidate_review(ledger, reason="Writer local edit")
            receipt = {
                "status": "saved",
                "node_id": node_id,
                "sha256": native.sha256_text(changed),
                "old_span_sha256": native.sha256_text(old_text),
                "new_span_sha256": native.sha256_text(new_text),
                "section_metrics": {"previous_body_chars": _body_chars(content), "body_chars": _body_chars(changed), "body_ratio_to_previous": _body_chars(changed) / max(1, _body_chars(content))},
                "length_guidance": _length_guidance(ledger),
            }
            edits.append({"request_hash": request_hash, "receipt": receipt})
            ledger.save()
            return json.dumps(receipt, ensure_ascii=False)

    from deerflow.sp.report.read_material import build_read_material_tool

    return [find, read, edit, build_read_material_tool(paths=paths)]


def configure(task, state, config):
    from deerflow.sp.report.hooks import active_report_required

    report = active_report_required(state)
    if report.report_id != task.metadata.get("report_id"):
        raise ReportValidationError("Writer report identity mismatch")
    paths = native.ReportPaths(report.outputs_path, report.report_id)
    tools = build_tools(paths, task, state)
    prompt = adapter.skill("report-local-edit")
    return (
        replace(
            config,
            system_prompt=prompt,
            tools=[t.name for t in tools],
            skills=[],
            output_format="markdown",
            # Reuse native report Writer capacity rather than perception's small
            # loop allowance; locating and editing several sections stays one task.
            max_turns=max(350, config.max_turns),
            tool_call_limit=50,
            loop_warning_instruction="Finish the requested edits with report_replace_text, then summarize saved changes and any unresolved request. Do not claim a change without a saved receipt.",
        ),
        tools,
        "writer",
    )


def fail(context, diagnostic, *, cancelled=False, action_id=None):
    active = adapter.current_turn(context.state, thread_id=context.thread_id)
    if active and active[3] and active[3]["status"] == "executing":
        ledger = ReportLedger.load(active[0]._paths(active[3]["report_id"]))
        if action_id and (ledger.payload.get("writer_first") or {}).get("action_id") != action_id:
            return
        active[0].end(active[3]["turn_id"], status="cancelled" if cancelled else "failed", diagnostic=diagnostic)


def accept(action, context, result, task, *, artifact_adapter, delegate_entry):
    from deerflow.sp.report import hooks
    from deerflow.sp.report.length import candidate_length, length_finding

    technical_reason = hooks._integration_review_technical_reason(result)
    if technical_reason or result.finish_reason == "length":
        fail(context, {"phase": "local_edit", "reason": technical_reason or "truncated Writer response"}, action_id=action.action_id)
        return hooks._rejection(action, context, "Writer execution stopped before completion; edits were retained without publishing.", reason="local_edit_incomplete")
    ledger = ReportLedger.load_for_context(hooks.report_context(context.state))
    with native.report_lock(ledger.paths):
        ledger = ReportLedger.load(ledger.paths)
        session = ledger.payload.get("writer_first") or {}
        if session.get("action_id") != task.action_id:
            raise ReportValidationError("local Writer result belongs to another action")
        if not session.get("edits"):
            fail(context, {"phase": "local_edit", "reason": "No changes saved", "writer_response": result.result}, action_id=action.action_id)
            entry = context.stack.append_observe(
                "No changes saved; this turn is failed and ended. Prior accepted report remains active. Do not re-delegate this message. Writer response: " + str(result.result),
                actor="writer",
                thread_id=context.thread_id,
                run_id=context.run_id,
            )
            return HandlerResult(
                next_step="continue",
                state_update={"sp_active_delegate_id": None},
                memory_entries=[delegate_entry, entry],
                idempotency_key=action.idempotency_key,
                run_events=[make_sp_event("sp.delegate.completed", action_id=action.action_id, run_id=context.run_id, target_agent="reporter", completion_status="blocked")],
            )
        # The inherited tree is a baseline layout, not a regenerated requirement
        # plan. Applying this editorial input does not certify semantic compliance.
        for item in ledger.inputs():
            ledger.mark_input_applied(item["input_id"])
        candidate = assembly.ensure_candidate(ledger)
        measurement = candidate_length(ledger, candidate)
        if measurement is not None and length_finding(measurement):
            ledger.save()
            return hooks._rejection(action, context, "Local edit is saved, but the native report length contract is not met. Adjust the edit or escalate to Outline.", reason="local_edit_length")
        review_id = native.next_identifier("rv", [r["review_id"] for r in ledger.payload.get("reviews", [])])
        review = {
            "review_id": review_id,
            "candidate_id": candidate["candidate_id"],
            "candidate_sha256": candidate["sha256"],
            "reviewed_candidate_sha256": candidate["sha256"],
            "base_digest": candidate["base_digest"],
            "inputs_digest": candidate["inputs_digest"],
            "tree_version": candidate["tree_version"],
            "status": "ready",
            "outcome": "deliver",
            "findings": [],
            "covered_requirement_ids": [],
            "edits": [],
            "action_id": action.action_id,
            "created_at": utc_now_iso(),
            "review_origin": "programmatic_local_edit",
            "semantic_review_performed": False,
            "factual_correctness_verified": False,
        }
        if measurement is not None:
            review["length"] = measurement
        hooks._write_review_record(ledger, review)
        final, artifact = hooks._finalize_integration_review(ledger, candidate=candidate, review=review, artifact_adapter=artifact_adapter, context=context, action=action, delegate_entry=delegate_entry)
        ledger.payload.setdefault("reviews", []).append(review)
        ledger.payload["outline_review_due"] = False
        hooks._mark_acceptance_review_completed(ledger, review_id)
        ledger.save()
        error = ledger.block_finish_reason()
        if error:
            return hooks._rejection(action, context, error, reason="local_edit_native_validation")
    entry = context.stack.append_observe(
        json.dumps({"status": "ready", "final_report": final["virtual_path"], "review_origin": "programmatic_local_edit", "semantic_review_performed": False, "next_action": "sp_finish"}, ensure_ascii=False),
        actor="writer",
        thread_id=context.thread_id,
        run_id=context.run_id,
        stage=action.stage,
        result_ref=artifact.metadata.artifact_id,
        metadata={"action_id": action.action_id, "target_agent": "reporter", "report_role": "writer", "completion_status": "complete", "report_id": ledger.paths.report_id, "review_origin": "programmatic_local_edit"},
    )
    update = hooks._artifact_state_update(artifact.state_update)
    update["sp_active_delegate_id"] = None
    return HandlerResult(
        next_step="continue",
        state_update=update,
        memory_entries=[delegate_entry, entry],
        artifact_refs=update.get("sp_current_artifact_refs") or {},
        idempotency_key=action.idempotency_key,
        run_events=[
            make_sp_event("sp.delegate.completed", action_id=action.action_id, run_id=context.run_id, target_agent="reporter", completion_status="complete"),
            make_sp_event("sp.report.local_edit_ready", action_id=action.action_id, run_id=context.run_id, report_id=ledger.paths.report_id, review_origin="programmatic_local_edit", semantic_review_performed=False),
            make_sp_event("sp.artifact.created", action_id=action.action_id, run_id=context.run_id, artifact_id=artifact.metadata.artifact_id, artifact_type="final_report", virtual_path=final["virtual_path"]),
        ],
    )
