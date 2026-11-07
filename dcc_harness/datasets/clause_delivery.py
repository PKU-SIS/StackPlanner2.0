"""CLAUSE file-mode policy. Central owns the task and the choice of workflow."""

import json
import os
import re
import threading
from pathlib import Path

from .clause_policy import (
    PREFIX,
    _digest,
    _name,
    _output,
    _persist_new,
    _read,
    validated_answer,
)

GUIDANCE = (
    "Report substantive internal problems within the five requested categories. Explain the incompatible "
    "requirements or plausible readings, or the missing provision and its effect. Ignore capitalization, "
    "synonyms, and alternative names for the same instrument when the meaning and effect agree. "
    "Quote the affected passage verbatim with its printed leading clause number or list marker."
)


# Match the deliverable requested by Central before adding any plugin text.
# For example, asking to verify an existing JSON file requests a status, not a file.
_JSON_TARGET = r"(?:[\w-]+\.json\b|\.?json\b(?:\s+(?:array|file|answer|output))?)"
_EXPECTED_ANSWER = re.compile(
    r"^(?:(?:a|an|the|one|single|complete|final|valid|pure|structured|registered|UTF-8)\s+)*"
    + _JSON_TARGET,
    re.I,
)
_WRITE_ANSWER = re.compile(
    r"(?:^|[.!?;:\n]\s*|\band\s+)(?:please\s+)?(?:write|save|deliver|return|produce|output|submit|emit|create)\s+"
    r"(?:(?:the|a|an|one|exactly|only|single|requested|complete|final|pure|valid|registered|UTF-8|result|results|answer|findings|output|it|as|to|in)\s+)*"
    r"(?:/mnt/user-data/outputs/[^\s]*\.json\b|" + _JSON_TARGET + r")",
    re.I,
)


def _requires_answer(task):
    expected = (getattr(task, "expected_output", None) or "").strip()
    # An intermediate assignment may itself request JSON (a schema, candidate
    # list, or inventory). Require a final-answer signal in the same clause.
    for text, pattern in ((expected, _EXPECTED_ANSWER), (task.task, _WRITE_ANSWER)):
        for match in pattern.finditer(text):
            clause = text[match.start() :].lstrip(".!?;: \n")
            clause = re.split(r"[.!?;](?:\s|$)|\n", clause, maxsplit=1)[0].lower()
            if (
                re.search(
                    r"\b(?:final|complete)\s+(?:[\w-]+\s+){0,3}(?:\.?json|answer|deliverable)\b",
                    clause,
                )
                or "/mnt/user-data/outputs/" in clause
                or ("text" in clause and "explanation" in clause)
            ):
                return True
    return False


class FileDeliveryPolicy:
    def __init__(self):
        self._files: dict[str, Path] = {}
        self._pending: set[str] = set()
        self._aliases: dict[str, Path] = {}
        self._ownership = threading.Lock()
        self._verified_writes: dict[str, tuple[Path, str]] = {}
        self._validated_files: set[str] = set()
        self._final_answers: set[str] = set()

    def before_delegate(self, action, context):
        if action.target_agent == "coder":
            action.metadata.update(
                coder_task_kind="data_processing", requires_implementation=False
            )
            # Native data processing defaults to read_file and bash. Give a writer
            # the file API while preserving explicit tool scopes and read-only work.
            if action.stage not in {
                "perception",
                "planning",
                "research",
                "verification",
            } and not action.metadata.get("verification_only"):
                action.metadata.setdefault(
                    "tool_names", ["read_file", "grep", "write_file", "bash"]
                )

    def delegation_stage(self, target, metadata):
        return None

    def intermediate_artifact_type(self, action):
        # The native registration hook runs after prepare_result. A validated
        # answer is the requested generated file irrespective of who wrote it.
        if getattr(action, "action_id", None) in self._validated_files:
            return "generated_file"
        return None

    def review_gap(self, task, messages):
        return None

    def configure_task(self, task, context):
        name = _name(task.action_id)
        path = _output(context, name)
        if os.path.lexists(path):
            raise ValueError("Output already exists for this action")
        self._files[task.action_id] = path
        if _requires_answer(task):
            self._final_answers.add(task.action_id)
        # An explicitly assigned filename is part of Central's action contract.
        # Never infer ownership from an arbitrary file in the outputs directory.
        expected = getattr(task, "expected_output", "") or ""
        target_pattern = (
            re.escape(PREFIX) + r"([\w.-]+\.json)(?=\s|$|[,;:'\"\x60)]|\.(?:\s|$))"
        )
        targets = set(re.findall(r"(?<![\w/])" + target_pattern, expected))
        with self._ownership:
            if self._pending:
                # A shared output directory cannot establish which concurrent
                # shell produced an alias. Action-owned paths still work.
                self._aliases.clear()
            elif task.action_id in self._final_answers and len(targets) == 1:
                alias = path.parent / targets.pop()
                if not alias.name.startswith("review-") and not os.path.lexists(alias):
                    self._aliases[task.action_id] = alias
                    name = alias.name
            self._pending.add(task.action_id)
        task.task += (
            " "
            + GUIDANCE
            + f" If this assignment delivers the final answer, write its JSON array to "
            f"{PREFIX}{name} and list the file in created_paths. Alternatively return the complete array "
            f"in artifact_content and set artifact_metadata.filename to {name}. For intermediate work, "
            "return the findings requested by Central."
        )

    def is_document_review(self, task):
        return task.subagent_type == "coder"

    def checked_execution(self, task, name, args, output):
        if (
            name == "write_file"
            and output == "OK"
            and task.action_id in self._final_answers
        ):
            # Native execution verification pairs this tool result with this action’s
            # actual write. Never adopt files based on a claimed name or directory scan.
            owned = self._files[task.action_id]
            virtual = args.get("path")
            if isinstance(virtual, str) and re.fullmatch(
                re.escape(PREFIX) + r"[\w.-]+\.json", virtual
            ):
                other = owned.parent / virtual[len(PREFIX) :]
                if other != owned and not other.name.startswith("review-spact_"):
                    try:
                        contents = _read(other)
                    except ValueError:
                        return False
                    if (
                        contents == args.get("content")
                        and args.get("append", False) is False
                    ):
                        self._verified_writes[task.action_id] = (
                            other,
                            _digest(contents),
                        )
        if (
            name != "read_file"
            or args.get("path") != "/mnt/user-data/uploads/document.txt"
        ):
            return False
        try:
            proof = json.loads(output)
        except (TypeError, ValueError):
            return False
        return (
            isinstance(proof, dict)
            and proof.get("status") == "success"
            and proof.get("format") == "clause_lines_v1"
            and bool(proof.get("lines"))
        )

    def validate_written_file(self, task, messages, metadata):
        path = self._files.get(task.action_id)
        if path is None:
            return None
        virtual = PREFIX + path.name
        other = self._verified_writes.get(task.action_id)
        if other and PREFIX + other[0].name in (metadata.get("created_paths") or []):
            try:
                if _digest(_read(other[0])) == other[1]:
                    return {"path": PREFIX + other[0].name, "sha256": other[1]}
            except ValueError:
                pass
        if virtual not in (metadata.get("created_paths") or []):
            return None
        writes = set()
        for m in messages or []:
            if m.get("type") == "ai":
                for c in m.get("tool_calls") or []:
                    args = c.get("args")
                    if (
                        c.get("name") == "write_file"
                        and isinstance(args, dict)
                        and args.get("path") == virtual
                        and c.get("id")
                    ):
                        writes.add(c["id"])
            if (
                m.get("type") == "tool"
                and m.get("tool_call_id") in writes
                and m.get("status") != "error"
                and m.get("content") == "OK"
            ):
                try:
                    return {"path": virtual, "sha256": _digest(_read(path))}
                except ValueError:
                    return None
        return None

    def prepare_result(self, task, result, context):
        with self._ownership:
            try:
                self._register_result(
                    task, result, self._aliases.pop(task.action_id, None)
                )
            finally:
                self._pending.discard(task.action_id)

    def _register_result(self, task, result, alias):
        from .clause import parse_answer

        md = result.artifact_metadata
        md.pop("clause_file_sha256", None)
        if (
            result.stop_reason
            or md.get("completion_status") != "complete"
            or md.get("evidence_gaps")
        ):
            return
        path = self._files.get(task.action_id)
        if path is None:
            return
        virtual = PREFIX + path.name
        # The run assigned this unique path before execution and checked it was absent.
        # A completed writer can omit the path in its receipt. Only inspect its own path.
        written = os.path.lexists(path)
        named = md.get("filename") in (path.name, virtual) or virtual in (
            md.get("created_paths") or []
        )
        required = task.action_id in self._final_answers
        if not written and not named and not required:
            # A tool block preserved verbatim as the specialist's whole reply
            # is an unexecuted request, not evidence that its assignment finished.
            raw = result.artifact_content or getattr(result, "result", None)
            if isinstance(raw, str) and re.fullmatch(
                r"\s*<tool_call>\s*<function=[\w-]+>.*?</function>\s*</tool_call>\s*", raw, re.S
            ):
                gap = "Specialist returned an unexecuted tool call without a completed artifact or findings."
                result.result = gap
                result.completion_status = "partial"
                md.update(completion_status="partial", created_paths=[], evidence_gaps=[gap])
                result.artifact_content = None
            return
        try:
            if written:
                text = _read(path)
            elif alias is not None and md.get("created_paths") == [PREFIX + alias.name]:
                # It was absent before this exclusive action and is named both by
                # Central and by the complete native receipt. Accept actual bytes only.
                text = _read(alias)
                _persist_new(path, text)
            elif task.action_id in self._verified_writes:
                other, expected = self._verified_writes[task.action_id]
                text = _read(other)
                if _digest(text) != expected:
                    raise ValueError(
                        "The action's written JSON changed before registration"
                    )
                _persist_new(path, text)
            else:
                raw = result.artifact_content
                if required and raw is None:
                    raw = getattr(result, "result", None)
                text = (
                    json.dumps(raw, ensure_ascii=False)
                    if isinstance(raw, list)
                    else raw
                )
                if (
                    not isinstance(text, str)
                    or len(text.encode("utf-8")) > 16 * 1024 * 1024
                ):
                    raise ValueError("Missing or oversized final JSON")
                parse_answer(None, text)
                _persist_new(path, text)
            digest = _digest(text)
            checked = (md.get("execution_verification") or {}).get(
                "postwrite_file_check"
            ) or {}
            if checked.get("sha256") and checked["sha256"] != digest:
                raise ValueError("File changed after its successful write check")
            self._validated_files.add(task.action_id)
            md.update(created_paths=[virtual], clause_file_sha256=digest)
            result.artifact_content = None
        except (OSError, ValueError) as exc:
            if required and not written:
                gap = f"Final JSON delivery missing: no file at {virtual} and no valid complete CLAUSE JSON array in the returned body."
            else:
                gap = f"Final JSON delivery failed for {virtual}: {exc}"
            result.result = gap
            result.completion_status = "partial"
            md.update(
                completion_status="partial",
                created_paths=[],
                evidence_gaps=[gap],
            )
            result.artifact_content = None

    def check_finish(self, ref, context):
        ref = ref or {}
        md = ref.get("metadata") or {}
        name = _name(md.get("delegate_action_id"))
        if ref.get("virtual_path") != PREFIX + name:
            raise ValueError("Select the registered action-owned JSON file")
        text = _read(_output(context, name))
        digest = _digest(text)
        if md.get("clause_file_sha256") != digest:
            raise ValueError("Selected file differs from the validated JSON")
        validated_answer(
            dict(
                status="completed",
                native_returned=True,
                delivery_source="finish_artifact",
                run_id=context.run_id,
                thread_id=context.thread_id,
                delivery=text,
                delivery_sha256=digest,
                final_artifact=ref,
            )
        )
