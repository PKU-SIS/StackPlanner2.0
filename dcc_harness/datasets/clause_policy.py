"""CLAUSE file-mode review stages and the strict native artifact boundary."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from .clause import parse_answer

PREFIX = "/mnt/user-data/outputs/"
PHASES = ("evidence", "verification", "delivery")
SCOPE = {
    "evidence": "Read the source section by section. Map clauses, cross-references, coverage gaps and candidate conflicting passages. Return a compact evidence map in artifact_content, with section/line anchors and short candidate descriptions. You need not reproduce long passages or resolve them in this stage.",
    "verification": "Check the prior evidence against the original passages, including nonadjacent clauses. Distinguish true contradictions from qualifications, identify gaps and give verified source locations and concise reasons in your intermediate artifact. Request more evidence when needed.",
    "delivery": "Produce the final file from the verified analysis. Recheck any concrete gap; keep earlier evidence and do not start the whole review over.",
}


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validated_answer(row):
    """Single offline-safe predicate for inference, export/monitoring and scoring."""
    if (
        row.get("status") != "completed"
        or row.get("native_returned") is not True
        or row.get("delivery_source") != "finish_artifact"
    ):
        raise ValueError("CLAUSE requires native FINISH selecting a file")
    ref = row.get("final_artifact") or {}
    md = ref.get("metadata") or {}
    path = ref.get("virtual_path") or ""
    if (
        not path.startswith(PREFIX)
        or not path.endswith(".json")
        or any(part in {"", ".", ".."} for part in path[len(PREFIX) :].split("/"))
    ):
        raise ValueError("CLAUSE requires an output .json file")
    if (
        not ref.get("artifact_id")
        or ref.get("is_current") is not True
        or md.get("registered_existing_file") is not True
        or md.get("completion_status", "complete") != "complete"
    ):
        raise ValueError("CLAUSE requires a complete, current registered file")
    if any(
        not row.get(key) or ref.get(key) != row[key] for key in ("thread_id", "run_id")
    ):
        raise ValueError("foreign CLAUSE artifact")
    text = row.get("delivery")
    hashes = [ref[k] for k in ("content_hash", "observed_sha256") if ref.get(k)]
    if md.get("clause_file_sha256"):
        hashes.append(md["clause_file_sha256"])
    if (
        not isinstance(text, str)
        or not hashes
        or any(h != _digest(text) for h in [row.get("delivery_sha256"), *hashes])
    ):
        raise ValueError("CLAUSE delivery and artifact digest disagree")
    return parse_answer(None, text)


def _name(action_id):
    if not isinstance(action_id, str) or not re.fullmatch(r"[\w-]{1,180}", action_id):
        raise ValueError("invalid native action identifier")
    return f"review-{action_id}.json"


def _output(context, name):
    root = Path(context.state["thread_data"]["outputs_path"])
    return root / name


def _read(path):
    limit = 16 * 1024 * 1024
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise ValueError("unsafe or oversized CLAUSE output")
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("oversized CLAUSE output")
        text = raw.decode("utf-8")
    except OSError as exc:
        raise ValueError("missing or unsafe CLAUSE output") from exc
    parse_answer(None, text)
    return text


def _persist_new(path, text):
    # Publish once; never replace another action's output.
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        tmp = Path(temp.name)
        try:
            temp.write(text.encode("utf-8"))
            temp.flush()
            os.link(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def _completed(context):
    actions = {}
    latest = {}
    completed = {}
    for e in context.stack.entries:
        if e.run_id != context.run_id:
            continue
        aid = e.metadata.get("action_id")
        if (
            e.action == "delegate"
            and (phase := e.metadata.get("review_phase")) in PHASES
        ):
            actions[aid] = phase
            for downstream in PHASES[PHASES.index(phase) :]:
                completed.pop(downstream, None)
                latest.pop(downstream, None)
            latest[phase] = aid
        if (
            e.action == "observe"
            and (phase := actions.get(aid))
            and latest.get(phase) == aid
        ):
            if e.metadata.get("completion_status") == "complete" and not e.failure_note:
                completed[phase] = e
    return completed


class ClausePolicy:
    def __init__(self):
        self._assigned_files = {}

    def before_delegate(self, action, context):
        completed = _completed(context)
        next_phase = next((p for p in PHASES if p not in completed), "delivery")
        phase = action.metadata.get("review_phase", next_phase)
        if phase not in PHASES or PHASES.index(phase) > PHASES.index(next_phase):
            previous = next(
                (
                    e.failure_note
                    for e in reversed(context.stack.entries)
                    if e.run_id == context.run_id
                    and e.action == "observe"
                    and e.failure_note
                ),
                None,
            )
            gap = f" Last gap: {previous[:300]}." if previous else ""
            return f"Complete the {next_phase} delegation first.{gap} Choose a role and assign only this scope with metadata.review_phase={next_phase} and a short review_focus. Return findings in artifact_content."
        if phase == "delivery" and "review_phase" not in action.metadata:
            return "Read the verification result and explicitly select evidence or verification for further checks, or delivery when ready. State why in your reason."
        if phase == "delivery":
            accepted = completed.get("delivery")
            if accepted is not None:
                refs = context.state.get("sp_current_artifact_refs") or {}
                ref = next(
                    (
                        r
                        for key, r in refs.items()
                        if key != "_history"
                        and isinstance(r, dict)
                        and r.get("artifact_id") == accepted.result_ref
                        and r.get("run_id") == context.run_id
                    ),
                    {},
                )
                if (
                    not action.metadata.get("revision_reason")
                    or accepted.result_ref not in action.input_refs
                ):
                    return (
                        f"Delivery is already accepted: final_artifact_ref={accepted.result_ref}, "
                        f"required_artifact_type={ref.get('type')}, path={ref.get('virtual_path')}. "
                        "You can FINISH with that receipt. To revise it, supply the accepted ref in input_refs and an explicit revision_reason; reopen verification for unresolved content."
                    )
            from deerflow.sp.runtime import SP_SUBAGENT_REGISTRY_NAMES
            from deerflow.subagents import get_subagent_config

            registry = SP_SUBAGENT_REGISTRY_NAMES.get(action.target_agent)
            role = get_subagent_config(registry) if registry else None
            if (
                not role
                or "write_file" in (role.disallowed_tools or [])
                or (role.tools is not None and "write_file" not in role.tools)
            ):
                return "This delegation requires a role allowed to write the final file. Select a suitable role or choose verification for a read-only review."
        action.metadata["review_phase"] = phase
        if action.target_agent == "coder":
            action.metadata.update(
                coder_task_kind="data_processing", requires_implementation=False
            )
        return None

    def configure_task(self, task, context):
        phase = task.metadata["review_phase"]
        tools = ["read_file", "grep"]
        # The executed assignment is stage-specific. Keep Central's chosen
        # focus and the registered prior observations, never a global file
        # assignment smuggled into a read-only phase.
        task.task = SCOPE[phase] + " The source is /mnt/user-data/uploads/document.txt."
        if phase == "verification":
            task.task += (
                " Check whether an express exception applies to the same circumstances, or whether "
                "different words name the same thing with the same effect. For alternative names of a document, identify the incompatible referents or duties supported by the text. A document can use several names for itself. Set aside the candidate "
                "only when the source resolves it. For each surviving finding record the specific "
                "clause or passage at issue, its counterpart or missing target, and the conflict. "
                "Merge only repeated descriptions of that same relationship and conflict. "
                "Keep distinct clause relationships separate even when they share a topic or passage. "
                "Pass the source location and verbatim issue sentence with its original clause label to delivery."
            )
        elif phase == "delivery":
            task.task += (
                " For each verified conflict, put its own verbatim issue sentence or passage in text "
                "and identify its counterpart or missing target in explanation. Preserve separate "
                "findings for distinct clause relationships; check each quotation against its "
                "verification entry before submitting. Copy the printed clause number, subsection marker and punctuation when they introduce the quoted passage. Keep the continuous source text; never prepend a distant heading or a tool line number. If the quotation checker reports missing_clause_prefix, restore those exact characters."
            )
        focus = task.metadata.get("review_focus")
        if isinstance(focus, str) and focus.strip():
            task.task += " Focus within this stage: " + focus[:750]
        task.metadata["review_inputs"] = {}
        prior = _completed(context)
        refs = context.state.get("sp_current_artifact_refs") or {}
        inventory = [
            r for k, r in refs.items() if k != "_history" and isinstance(r, dict)
        ]
        inventory.extend(r for r in refs.get("_history", []) if isinstance(r, dict))
        for earlier in PHASES[: PHASES.index(phase)]:
            entry = prior.get(earlier)
            if entry is None:
                raise ValueError(f"Complete {earlier} before {phase}")
            ref = next(
                (
                    r
                    for r in inventory
                    if r.get("run_id") == context.run_id
                    and r.get("artifact_id") == entry.result_ref
                ),
                None,
            )
            if not ref or not ref.get("virtual_path"):
                raise ValueError(
                    f"Missing registered {earlier} evidence; repeat only that stage"
                )
            task.metadata["review_inputs"][earlier] = ref["virtual_path"]
            task.input_refs = list(
                dict.fromkeys([*task.input_refs, ref["artifact_id"]])
            )
            task.task += f" Read the {earlier} artifact at {ref['virtual_path']} (ref {ref['artifact_id']}) before proceeding."
        task.task += " Examine exact source lines using read_file or short grep patterns. Do not copy a whole source or quotation list into tool arguments."
        if phase == "delivery":
            name = _name(task.action_id)
            if os.path.lexists(_output(context, name)):
                raise ValueError(
                    "Output for this action already exists; use a fresh delegation"
                )
            if uploads := context.state["thread_data"].get("uploads_path"):
                self._assigned_files[task.action_id] = (
                    _output(context, name),
                    Path(uploads) / "document.txt",
                )
            previous = next(
                (
                    e
                    for e in reversed(context.stack.entries)
                    if e.run_id == context.run_id
                    and e.action == "delegate"
                    and e.metadata.get("review_phase") == "delivery"
                    and e.metadata.get("action_id") != task.action_id
                ),
                None,
            )
            if previous:
                prior_name = _name(previous.metadata["action_id"])
                try:
                    _read(_output(context, prior_name))
                except ValueError:
                    pass
                else:
                    task.task += f" An unaccepted previous draft exists at {PREFIX}{prior_name}. You may read and revise it against verified evidence. Write your own action's file."
            tools.extend(["write_file", "clause_check_passages"])
            task.task += f" Write exactly {PREFIX}{name} via write_file. Verify the last written version via clause_check_passages(answer_path) using its path alone. Read the current file before overwriting. Distinguish whitespace variation from missing quotations and disclose outstanding gaps to Central. Never embed the JSON in a tool command for checking. If writing is unavailable, return the complete unwrapped JSON array in artifact_content. Declare only files actually created."
            task.expected_output = f"One pure UTF-8 JSON array at {PREFIX}{name}."
        else:
            task.task += " Use compact line references and combine related searches. This read-only pass has a 14-read working budget once prerequisite reads are present. Return remaining gaps for a focused follow-up. Return the intermediate evidence in the native artifact_content; SP registers it. No candidate list needs to be written with Bash. Keep the map concise and include source locations. The final answer belongs to a later delegation."
            task.expected_output = SCOPE[phase]
        task.acceptance_criteria = [task.expected_output]
        task.allowed_tools = tools
        task.metadata["tool_names"] = tools

    def prepare_result(self, task, result, context):
        md = result.artifact_metadata
        # Model-supplied metadata cannot grant the final-file marker.
        md.pop("clause_file_sha256", None)
        if task.metadata["review_phase"] != "delivery":
            # A read-only delegation can return observations but cannot own a written file.
            md["created_paths"] = []
        if (
            task.metadata["review_phase"] != "delivery"
            and md.get("completion_status") == "complete"
            and not result.artifact_content
        ):
            md.update(
                completion_status="partial",
                evidence_gaps=[
                    "Return the intermediate stage findings in artifact_content so the next delegation can read them."
                ],
            )

        if (
            task.metadata["review_phase"] != "delivery"
            or result.stop_reason
            or md.get("completion_status") != "complete"
            or md.get("evidence_gaps")
        ):
            return
        name = _name(task.action_id)
        path = _output(context, name)
        virtual = PREFIX + name
        try:
            if virtual in (md.get("created_paths") or []):
                text = _read(path)
            else:
                raw = result.artifact_content
                if isinstance(raw, list):
                    raw = json.dumps(raw, ensure_ascii=False)
                if not isinstance(raw, str):
                    raise ValueError("No owned file or complete JSON artifact_content")
                parse_answer(None, raw)
                text = raw
            if task.action_id in self._assigned_files:
                from .clause_tools import locate_passages

                _, source = self._assigned_files[task.action_id]
                doc = source.read_text(encoding="utf-8")
                items = parse_answer(None, text)
                for start in range(0, len(items), 128):
                    rows = locate_passages(
                        doc, [i["text"] for i in items[start : start + 128]]
                    )
                    for row in rows:
                        if prefix := row.get("missing_clause_prefix"):
                            raise ValueError(
                                f"Restore source clause prefix {prefix!r} on text at index {start + row['index']}."
                            )
            if virtual not in (md.get("created_paths") or []):
                _persist_new(path, text)
            checked = (
                (md.get("execution_verification") or {}).get("postwrite_file_check")
                or {}
            ).get("sha256")
            if checked and checked != _digest(text):
                raise ValueError("Output changed after its post-write check")
            md.update(created_paths=[virtual], clause_file_sha256=_digest(text))
            result.artifact_content = None
        except (ValueError, OSError) as exc:
            md.update(
                completion_status="partial",
                created_paths=[],
                evidence_gaps=[f"Final JSON delivery failed: {exc}"],
            )
            result.artifact_content = None

    def validate_written_file(self, task, messages, metadata):
        """A real post-write data check; never advance partial, timed-out or unrelated outputs."""
        if (
            task.metadata.get("review_phase") != "delivery"
            or task.action_id not in self._assigned_files
        ):
            return None
        path, source = self._assigned_files[task.action_id]
        virtual = PREFIX + path.name
        if virtual not in (metadata.get("created_paths") or []):
            return None
        writes = set()
        successful = False
        for message in messages or []:
            if message.get("type") == "ai":
                for call in message.get("tool_calls") or []:
                    if (
                        call.get("name") == "write_file"
                        and isinstance(call.get("args"), dict)
                        and call["args"].get("path") == virtual
                    ):
                        if call.get("id"):
                            writes.add(call["id"])
            if message.get("type") == "tool" and message.get("tool_call_id") in writes:
                successful = (
                    message.get("status") != "error" and message.get("content") == "OK"
                )
        if not successful:
            return None
        try:
            text = _read(path)
            items = parse_answer(None, text)
            doc = source.read_text(encoding="utf-8")
        except (ValueError, OSError):
            return None
        from .clause_tools import locate_passages

        locations = []
        for i in range(0, len(items), 128):
            locations.extend(
                locate_passages(doc, [item["text"] for item in items[i : i + 128]])
            )
        found = sum(item["found"] for item in locations)
        whitespace = sum(
            item.get("comparison") == "whitespace_only" for item in locations
        )
        check = {
            "path": virtual,
            "sha256": _digest(text),
            "items": len(items),
            "exact_quotations": found,
            "whitespace_only": whitespace,
            "unlocated": len(items) - found - whitespace,
        }
        if found != len(items):
            check["limitation"] = (
                f"Quote check: {found} exact, {whitespace} whitespace variations, {len(items) - found - whitespace} unlocated. Consider targeted revision only where needed."
            )
        return check

    def delegation_stage(self, target, metadata):
        from deerflow.sp.runtime import SP_SUBAGENT_REGISTRY_NAMES

        if target not in SP_SUBAGENT_REGISTRY_NAMES or target == "memory_recaller":
            return None
        phase = metadata.get("review_phase")
        if phase == "delivery":
            return "reporting" if target == "reporter" else "implementation"
        return (
            {"evidence": "research", "verification": "verification"}.get(phase)
            if isinstance(phase, str)
            else None
        )

    def intermediate_artifact_type(self, action):
        return {
            "evidence": "perception_observation",
            "verification": "verification_observation",
        }.get(action.metadata.get("review_phase"))

    def is_document_review(self, task):
        return (
            task.subagent_type == "coder"
            and task.metadata.get("review_phase") in PHASES
            and bool(task.allowed_tools)
            and set(task.allowed_tools)
            <= {"read_file", "grep", "write_file", "clause_check_passages"}
        )

    def checked_execution(self, task, name, args, output):
        if name not in task.allowed_tools:
            return False
        try:
            proof = json.loads(output)
        except (ValueError, TypeError):
            return False
        if not isinstance(proof, dict) or proof.get("status") != "success":
            return False
        if task.metadata.get("review_phase") in ("evidence", "verification"):
            return (
                name == "read_file"
                and args.get("path") == "/mnt/user-data/uploads/document.txt"
                and proof.get("format") == "clause_lines_v1"
                and bool(proof.get("lines"))
            )
        return (
            name == "clause_check_passages"
            and args.get("answer_path") == PREFIX + _name(task.action_id)
            and isinstance(proof.get("locations"), list)
            and all(
                isinstance(item, dict) and isinstance(item.get("found"), bool)
                for item in proof["locations"]
            )
        )

    def review_gap(self, task, messages):
        phase = task.metadata.get("review_phase")
        if phase not in ("evidence", "verification"):
            return None
        calls = {}
        read_paths = set()
        for m in messages or []:
            if m.get("type") == "ai":
                calls.update(
                    {c["id"]: c for c in m.get("tool_calls", []) if c.get("id")}
                )
            if m.get("type") != "tool" or m.get("status") == "error":
                continue
            call = calls.get(m.get("tool_call_id"), {})
            if call.get("name") != "read_file":
                continue
            args = call.get("args") or {}
            content = m.get("content")
            if (
                not isinstance(args, dict)
                or not isinstance(content, str)
                or not content.strip()
                or content.startswith(("Error:", "Error invoking tool"))
            ):
                continue
            path = args.get("path")
            if (
                path == "/mnt/user-data/uploads/document.txt"
                and not self.checked_execution(task, "read_file", args, content)
            ):
                continue
            read_paths.add(path)
        if "/mnt/user-data/uploads/document.txt" not in read_paths:
            return "Read the source document before completing this stage."
        if (
            phase == "verification"
            and task.metadata["review_inputs"]["evidence"] not in read_paths
        ):
            return "Read the registered evidence map and compare its candidates with the source before completing verification."
        return None

    def check_finish(self, ref, context):
        done = _completed(context)
        missing = next((phase for phase in PHASES if phase not in done), None)
        if missing:
            gap = next(
                (
                    e.failure_note
                    for e in reversed(context.stack.entries)
                    if e.run_id == context.run_id
                    and e.action == "observe"
                    and e.failure_note
                ),
                None,
            )
            raise ValueError(
                f"FINISH blocked. Completed review stages: {', '.join(done) or 'none'}. Next incomplete stage: {missing}. Keep accepted evidence; address only the outstanding gap. Last observed gap: {str(gap or 'stage has not completed')[:300]}"
            )
        ref = ref or {}
        accepted_id = done["delivery"].result_ref
        if not accepted_id or ref.get("artifact_id") != accepted_id:
            raise ValueError(
                f"Select the latest accepted delivery artifact {accepted_id} at FINISH; earlier stage artifacts do not complete this review."
            )
        md = ref.get("metadata") or {}
        name = _name(md.get("delegate_action_id"))
        if ref.get("virtual_path") != PREFIX + name:
            raise ValueError("Select the registered JSON from the delivery delegation")
        try:
            text = _read(_output(context, name))
        except OSError as exc:
            raise ValueError("Unable to read registered JSON") from exc
        if _digest(text) != md.get("clause_file_sha256"):
            raise ValueError("Selected file differs from the validated delivery")
        validated_answer(
            dict(
                status="completed",
                native_returned=True,
                delivery_source="finish_artifact",
                run_id=context.run_id,
                thread_id=context.thread_id,
                delivery=text,
                delivery_sha256=_digest(text),
                final_artifact={**ref, "observed_sha256": _digest(text)},
            )
        )
