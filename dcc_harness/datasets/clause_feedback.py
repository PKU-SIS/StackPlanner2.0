"""Transient evidence-use feedback for repeated successful CLAUSE file reads.

This adapter sees only the already model-visible request. It never chooses a
role, fabricates evidence, or marks work complete. Only a native identical-call
loop warning ends tool availability for that delegated recovery response. No labels,
case IDs or evaluation results are available here.
"""

from __future__ import annotations
import copy
import hashlib
import json
import logging
import re

_READ_ONLY = frozenset({"read_file", "grep", "glob", "ls", "clause_check_passages"})
_NOTE = (
    "The last tool call succeeded and returned its results above. That output is "
    "available evidence, not a request to invoke the tool again. Use that evidence "
    "for your delegated result, or request genuinely missing evidence if needed. "
    "Its returned content is already present in earlier successful reads or searches. "
    "A ranged or truncated response covers only the returned part; this notice "
    "does not establish that the overall task is complete."
)
_READ_NOTE = (
    "The requested source-file read has completed successfully. Its numbered lines "
    "are now available as evidence; reading that file is not still pending. Use "
    "these lines for the remaining delegated analysis or result, or request "
    "genuinely missing evidence if needed. Line labels are tool metadata, not "
    "contract text. This is input coverage, not a judgment that the review is complete."
)

_IDENTICAL_WARNING = "[LOOP DETECTED] You are repeating the same tool calls. Stop calling tools and produce your final answer now. If you cannot complete the task, summarize what you accomplished so far."
_RECOVERY_NOTE = (
    "The native loop guard has requested a result from this delegated stage. "
    "No further tool invocation is available for this recovery response. Return "
    "the native specialist JSON envelope using only observed evidence. Use complete "
    "only if the assigned work is actually satisfied; otherwise use partial or blocked "
    "and state the concrete remaining gaps. Do not invent file creation or claim the "
    "whole user task is complete. A requested file must already exist before listing "
    "it in created_paths."
)
logger = logging.getLogger(__name__)


_PASS_READ_BUDGET = 14
_SOURCE = "/mnt/user-data/uploads/document.txt"


def _stage_request(messages):
    """Read the latest structured native task, including when a reminder precedes it."""
    tag = "<sp-subagent-task>"
    for index in range(len(messages) - 1, -1, -1):
        m = messages[index]
        body = m.get("content")
        if m.get("role") != "user" or not isinstance(body, str) or tag not in body:
            continue
        data = body.split(tag, 1)[1].lstrip()
        try:
            task, end = json.JSONDecoder().raw_decode(data)
        except ValueError:
            continue
        if isinstance(task, dict) and data[end:].lstrip().startswith(
            "</sp-subagent-task>"
        ):
            return task, messages[index + 1 :]
    return None, []


def _stage_result_note(payload, messages):
    names = {(t.get("function") or {}).get("name") for t in payload.get("tools", [])}
    if not names or names - {
        "read_file",
        "grep",
        "write_file",
        "clause_check_passages",
    }:
        return None
    task, history = _stage_request(messages)
    if not task:
        return None
    md = task.get("metadata") or {}
    phase = md.get("review_phase")
    current = (
        "/mnt/user-data/outputs/review-" + str(task.get("action_id", "")) + ".json"
    )
    calls = {}
    observed = set()
    read_paths = set()
    owned = False
    checked = False
    for m in history:
        if m.get("role") == "assistant":
            for c in m.get("tool_calls") or []:
                fn = c.get("function") or {}
                args = fn.get("arguments", {})
                try:
                    args = json.loads(args) if isinstance(args, str) else args
                except ValueError:
                    continue
                if c.get("id") and isinstance(args, dict):
                    calls[c["id"]] = (fn.get("name"), args)
        elif m.get("role") == "tool":
            ident = m.get("tool_call_id")
            name, args = calls.get(ident, (None, {}))
            output = m.get("content", "")
            if name in ("grep", "read_file"):
                observed.add(ident)
            if name == "read_file" and _successful(output):
                path = args.get("path")
                if path == _SOURCE:
                    try:
                        data = json.loads(output)
                    except ValueError:
                        continue
                    if (
                        not isinstance(data, dict)
                        or data.get("format") != "clause_lines_v1"
                        or not data.get("lines")
                    ):
                        continue
                read_paths.add(path)
            if name == "write_file" and args.get("path") == current:
                owned = output == "OK"
                checked = False
            if name == "clause_check_passages" and args.get("answer_path") == current:
                try:
                    proof = json.loads(output)
                except (TypeError, ValueError):
                    continue
                checked = (
                    isinstance(proof, dict)
                    and proof.get("status") == "success"
                    and isinstance(proof.get("locations"), list)
                    and all(
                        isinstance(x, dict)
                        and x.get("found") is True
                        and not x.get("missing_clause_prefix")
                        for x in proof["locations"]
                    )
                )
    last = messages[-1]
    if (
        phase == "delivery"
        and owned
        and checked
        and last.get("role") == "tool"
        and calls.get(last.get("tool_call_id"), (None,))[0] == "clause_check_passages"
    ):
        return f"The current action successfully wrote {current} and checked all quotations in that version. Return the native specialist envelope now. List that file only if the assigned work is complete; otherwise report partial and the specific gap. Central decides whether to revise or finish."
    if (
        phase not in ("evidence", "verification")
        or names - {"read_file", "grep"}
        or _SOURCE not in read_paths
    ):
        return None
    if (
        phase == "verification"
        and (md.get("review_inputs") or {}).get("evidence") not in read_paths
    ):
        return None
    total_warning = r"\[LOOP DETECTED\] You have made \d+ total tool calls without producing a final answer\. Stop exploring, use the evidence already collected, and produce your final answer now\."
    warned = (
        last.get("role") == "user"
        and last.get("name") == "loop_warning"
        and any(
            re.fullmatch(total_warning, x)
            for x in str(last.get("content", "")).split("\n\n")
        )
    )
    if warned or (last.get("role") == "tool" and len(observed) >= _PASS_READ_BUDGET):
        return "Return this read-only stage's native envelope with existing evidence. Put the map or verification in artifact_content; disclose concrete outstanding questions as partial if this assignment is incomplete. Central can commission another bounded check."
    return None


def _verification_first_read(payload, messages):
    task, history = _stage_request(messages)
    if not task or any(m.get("role") in ("assistant", "tool") for m in history):
        return None
    md = task.get("metadata") or {}
    path = (md.get("review_inputs") or {}).get("evidence")
    if (
        md.get("review_phase") != "verification"
        or not isinstance(path, str)
        or not path.startswith("/mnt/user-data/outputs/sp/")
    ):
        return None
    tools = payload.get("tools") or []
    if {t.get("function", {}).get("name") for t in tools} != {"read_file", "grep"}:
        return None
    read = copy.deepcopy(next(t for t in tools if t["function"]["name"] == "read_file"))
    properties = read["function"].get("parameters", {}).get("properties", {})
    if "path" not in properties:
        return None
    properties["path"]["enum"] = [path]
    return {
        **payload,
        "tools": [read],
        "tool_choice": {"type": "function", "function": {"name": "read_file"}},
    }


def _intermediate_format():
    metadata = {
        "type": "object",
        "properties": {
            "completion_status": {
                "type": "string",
                "enum": ["complete", "partial", "blocked"],
            },
            "evidence_gaps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Only unmet requirements of this specific stage.",
            },
            "disclosed_limitations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Nonblocking limitations and questions to carry forward to the next stage.",
            },
        },
        "required": ["completion_status", "evidence_gaps", "disclosed_limitations"],
        "additionalProperties": False,
    }
    fields = {
        "summary": {"type": "string"},
        "artifact_content": {
            "type": "string",
            "description": "Actual source-grounded findings and outstanding gaps for Central.",
        },
        "artifact_metadata": metadata,
    }
    schema = {
        "type": "object",
        "properties": fields,
        "required": list(fields),
        "additionalProperties": False,
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "clause_stage_result",
            "strict": True,
            "schema": schema,
        },
    }


def _loop_recovery(payload, messages):
    last = messages[-1]
    identical = (
        last.get("role") == "user"
        and last.get("name") == "loop_warning"
        and _IDENTICAL_WARNING in str(last.get("content", "")).split("\n\n")
    )
    if last.get("role") not in ("tool", "user"):
        return None
    tools = payload.get("tools") or []
    if not tools or any(
        str((t.get("function") or {}).get("name", "")).startswith("sp_") for t in tools
    ):
        return None
    note = _stage_result_note(payload, messages)
    if note is None:
        if not identical or not any(
            m.get("role") == "user"
            and "<sp-subagent-task>" in str(m.get("content", ""))
            for m in messages
        ):
            return None
        note = _RECOVERY_NOTE
    result = {
        k: v
        for k, v in payload.items()
        if k not in {"tools", "tool_choice", "parallel_tool_calls"}
    }
    task, _ = _stage_request(messages)
    if task and (task.get("metadata") or {}).get("review_phase") in (
        "evidence",
        "verification",
    ):
        result["response_format"] = _intermediate_format()
        phase = task["metadata"]["review_phase"]
        note += " Write the actual stage work in artifact_content. Judge completion against this stage alone. Use evidence_gaps for its unmet requirements and disclosed_limitations for nonblocking caveats."
        if phase == "evidence":
            note += " An evidence map identifies anchored candidates and coverage. Their subsequent adjudication, quotation boundaries for final output, and JSON writing belong to later stages. Report a partial map if the required source coverage or candidate mapping is unfinished."
    result["messages"] = [*messages, {"role": "user", "content": note}]
    logger.warning(
        "CLAUSE_LOOP_RECOVERY: scoped specialist result requested; native validation remains active"
    )
    return result


def _signature(call):
    function = call.get("function") or {}
    name = function.get("name")
    if name not in _READ_ONLY:
        return None
    try:
        args = function.get("arguments", {})
        args = json.loads(args) if isinstance(args, str) else args
        if not isinstance(args, dict):
            return None
        args = {k: v for k, v in args.items() if k != "description"}
        return name, json.dumps(args, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return None


def _successful(content):
    if not isinstance(content, str) or not content.strip():
        return False
    text = content.lstrip()
    if text.startswith("{"):
        try:
            value = json.loads(text)
            if isinstance(value, dict) and value.get("status") in {
                "error",
                "incomplete",
                "blocked",
            }:
                return False
        except ValueError:
            pass
    return not text.startswith(
        (
            "Error:",
            "Error invoking tool",
            "Command blocked:",
            "[FORCED STOP]",
            '{"error"',
            "Tool execution failed",
        )
    )


def _rejected_stage_feedback(payload, messages):
    # Mirror the existing ordering guard only on a real native refusal.
    last = messages[-1]
    if last.get("role") != "tool" or not last.get("tool_call_id"):
        return None
    try:
        receipt = json.loads(last.get("content", ""))
    except (ValueError, TypeError):
        return None
    if (
        not isinstance(receipt, dict)
        or receipt.get("action_type") != "DELEGATE"
        or receipt.get("next_step") != "error_recoverable"
    ):
        return None
    match = re.match(
        r"^Complete the (evidence|verification) delegation first\.",
        str(receipt.get("error", "")),
    )
    if not match or not any(
        call.get("id") == last["tool_call_id"]
        and (call.get("function") or {}).get("name") == "sp_delegate"
        for m in messages[:-1]
        if m.get("role") == "assistant"
        for call in (m.get("tool_calls") or [])
    ):
        return None
    phase = match[1]
    for i, base in enumerate(payload.get("tools") or []):
        if (base.get("function") or {}).get("name") != "sp_delegate":
            continue
        tool = copy.deepcopy(base)
        meta = (
            tool["function"].get("parameters", {}).get("properties", {}).get("metadata")
        )
        if not isinstance(meta, dict):
            return None
        valid = ["evidence"] if phase == "evidence" else ["evidence", "verification"]
        meta.setdefault("properties", {})["review_phase"] = {
            "type": "string",
            "enum": valid,
        }
        notice = f"The last delegation did not execute. Its metadata.review_phase must agree with the assigned scope. The pending phase is {phase}. If delegating, choose an eligible phase and a role, and describe that phase's work. Other Central control actions remain available."
        return {
            **payload,
            "tools": [*payload["tools"][:i], tool, *payload["tools"][i + 1 :]],
            "messages": [*messages, {"role": "user", "content": notice}],
        }
    return None


def _routing_feedback(messages):
    last = messages[-1]
    if last.get("role") != "tool":
        return None
    try:
        result = json.loads(last.get("content", ""))
    except (ValueError, TypeError):
        return None
    if not isinstance(result, dict) or not str(result.get("error", "")).startswith(
        "Blocked repeated completed perception by perception."
    ):
        return None
    for previous in reversed(messages[:-1]):
        if previous.get("role") != "assistant":
            continue
        for call in previous.get("tool_calls") or []:
            function = call.get("function") or {}
            if (
                call.get("id") != last.get("tool_call_id")
                or function.get("name") != "sp_delegate"
            ):
                continue
            try:
                args = function.get("arguments") or {}
                args = json.loads(args) if isinstance(args, str) else args
            except (ValueError, TypeError):
                return None
            if (
                not isinstance(args, dict)
                or args.get("target_agent", args.get("agent")) != "coder"
            ):
                return None
            logger.warning(
                "CLAUSE_ROUTING_FEEDBACK: native Coder-to-Perception rejection explained; Central retains every control choice"
            )
            return (
                "The last Coder delegation was mapped to Perception by native routing "
                "for local-document inspection, then rejected because Perception is "
                "already complete. Changing the stage name alone does not change this "
                "routing. Choose a supported way to perform the remaining analysis or "
                "file delivery; do not repeat the same rejected delegation. This notice "
                "does not select a replacement role or authorize software changes."
            )
        return None
    return None


_TASK_KIND_HELP = (
    " CLAUSE review needs separate evidence, verification and delivery delegations. "
    "Declare review_phase and review_focus in metadata. Read the verification artifact, decide whether to supplement, revise or deliver, and give the reason. Choose roles yourself. "
    "After accepted delivery, use the registered artifact_id and type in FINISH. Only request a new delivery for an explicit revision. "
    "If you choose Coder for local computation, JSON-file creation, or file validation, "
    'declare metadata={"coder_task_kind":"data_processing","requires_implementation":false} '
    "in addition to any other needed metadata. This selects the existing native "
    "data-processing evidence contract; it does not waive the required review, "
    "real file creation, or validation. Role selection remains your decision."
)


def _annotate_task_kind(payload):
    tools = payload.get("tools") or []
    for index, tool in enumerate(tools):
        function = tool.get("function") or {}
        if function.get("name") != "sp_delegate":
            continue
        field = (function.get("parameters") or {}).get("properties", {}).get("metadata")
        if not isinstance(field, dict) or _TASK_KIND_HELP in field.get(
            "description", ""
        ):
            continue
        replacement = copy.deepcopy(tool)
        target = replacement["function"]["parameters"]["properties"]["metadata"]
        target["description"] = target.get("description", "") + _TASK_KIND_HELP
        return {**payload, "tools": [*tools[:index], replacement, *tools[index + 1 :]]}
    return payload


def _blocked_answer_command(messages):
    """Recognize the sandbox's rejection of a specialist's oversized JSON output command."""
    response = messages[-1]
    if (
        response.get("role") != "tool"
        or response.get("content") != "Command blocked: command too long. Please use a safer alternative approach."
        or not response.get("tool_call_id")
    ):
        return False
    for message in reversed(messages[:-1]):
        if message.get("role") == "tool":
            continue
        if message.get("role") != "assistant":
            return False
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            if call.get("id") != response["tool_call_id"] or function.get("name") != "bash":
                continue
            try:
                args = json.loads(function["arguments"])
            except (ValueError, TypeError, KeyError):
                return False
            command = args.get("command") if isinstance(args, dict) else None
            return isinstance(command, str) and bool(re.search(r"/mnt/user-data/outputs/[\w.-]+\.json\b", command))
        return False
    return False


def _final_answer_closeout(payload, messages, names, *, returning=False):
    """At a native stop warning, serialize the specialist's own CLAUSE result."""
    from types import SimpleNamespace
    from .clause_delivery import GUIDANCE, _requires_answer

    if not messages or not names or any(name.startswith("sp_") for name in names):
        return None
    last = messages[-1]
    if not returning:
        warned = last.get("role") == "user" and last.get("name") == "loop_warning"
        notes = str(last.get("content", "")).split("\n\n") if warned else []
        native_stop = any(
            note == _IDENTICAL_WARNING
            or re.fullmatch(
                r"\[LOOP DETECTED\] You have made \d+ total tool calls without producing a final answer\. Stop exploring, use the evidence already collected, and produce your final answer now\.",
                note,
            )
            for note in notes
        )
        if not native_stop and not _blocked_answer_command(messages):
            return None
    task, history = _stage_request(messages)
    if not task or (task.get("protocol") or {}).get("sender") != "central":
        return None
    original = (task.get("task") or "").partition(" " + GUIDANCE)[0]
    if not _requires_answer(
        SimpleNamespace(task=original, expected_output=task.get("expected_output"))
    ):
        return None

    # Establish document coverage using real tool receipts, never contract text
    # that happens to contain JSON or a warning-like string.
    ids = set()
    spans = []
    total = None
    for msg in history:
        if msg.get("role") == "assistant":
            for call in msg.get("tool_calls") or []:
                function = call.get("function") or {}
                if function.get("name") != "read_file":
                    continue
                try:
                    args = json.loads(function["arguments"])
                    if args.get("path") == _SOURCE:
                        ids.add(call["id"])
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
        if msg.get("role") != "tool" or msg.get("tool_call_id") not in ids:
            continue
        try:
            read = json.loads(msg["content"])
            start, end, size = (
                read[k] for k in ("start_line", "end_line", "total_lines")
            )
            if (
                read.get("format") != "clause_lines_v1"
                or read.get("path") != _SOURCE
                or read.get("status") != "success"
                or not all(type(v) is int for v in (start, end, size))
                or not 1 <= start <= end <= size
                or len(read["lines"]) != end - start + 1
            ):
                continue
            if total is not None and total != size:
                return None
            total = size
            spans.append((start, end))
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
    covered = 0
    for start, end in sorted(spans):
        if start > covered + 1:
            break
        covered = max(covered, end)
    if total is None or covered != total:
        return None
    response = _intermediate_format()
    response["json_schema"]["name"] = "clause_final_receipt"
    fields = response["json_schema"]["schema"]["properties"]
    findings = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "explanation": {"type": "string"}},
            "required": ["text", "explanation"],
            "additionalProperties": False,
        },
    }
    # Emit the actual findings before the status summary. Partial/blocked
    # receipts may carry an empty array and still retain their explicit gaps.
    response["json_schema"]["schema"]["properties"] = {
        "artifact_content": findings,
        "artifact_metadata": fields["artifact_metadata"],
        "summary": fields["summary"],
    }
    # Keep the native completion and gap fields. No artifact exists until the
    # plugin receives the whole array and successfully publishes its exact bytes.
    result = {
        k: v
        for k, v in payload.items()
        if k not in {"tools", "tool_choice", "parallel_tool_calls"}
    }
    result["response_format"] = response
    cap_key = (
        "max_completion_tokens" if "max_completion_tokens" in result else "max_tokens"
    )
    cap = result.get(cap_key)
    if isinstance(cap, int) and 0 < cap < 12288:
        result[cap_key] = min(12288, cap * 2)
    logger.info("CLAUSE final receipt serialization for %s", task.get("action_id"))
    return result


def _central_control_channel(payload):
    names = [
        (tool.get("function") or {}).get("name", "")
        for tool in payload.get("tools") or []
    ]
    choice = payload.get("tool_choice")
    messages = payload.get("messages") or []
    if choice in (None, "auto"):
        closeout = _final_answer_closeout(payload, messages, names)
        if closeout is not None:
            return closeout
    if names and set(names) <= _READ_ONLY and messages and choice in (None, "auto"):
        last = messages[-1]
        if (
            last.get("name") == "loop_warning"
            and last.get("role") == "user"
            and re.search(
                r"(?:^|\n)\[LOOP DETECTED\] You have made \d+ total tool calls without producing a final answer\.",
                str(last.get("content", "")),
            )
            and _stage_request(messages)[0]
        ):
            # The runtime itself requested the specialist's return. Preserve
            # the original messages, including its role-specific receipt schema.
            return {
                k: v
                for k, v in payload.items()
                if k not in {"tools", "tool_choice", "parallel_tool_calls"}
            }
    if (
        "sp_delegate" in names
        and all(name.startswith("sp_") for name in names)
        and (choice is None or choice == "auto")
    ):
        # Native SP requires a control action, including an accepted FINISH for
        # file delivery. Do not prescribe a particular action or target role.
        return {**payload, "tool_choice": "required"}
    return payload


def adapt_request(payload: dict) -> dict:
    """Append a nonpersistent reminder only after an unchanged repeated read.

    Any intervening mutating/unknown call invalidates prior evidence identities.
    Failed, unmatched or changed results never activate the reminder. The model
    retains all tools and can still request a justified reread or more evidence.
    A trusted native identical-call warning instead gets a result-only recovery
    turn, before native hard-stop handling; normal planning is never restricted.
    """
    payload = _central_control_channel(_annotate_task_kind(payload))
    messages = payload.get("messages") or []
    if not messages:
        return payload
    first_read = _verification_first_read(payload, messages)
    if first_read is not None:
        return first_read
    stage_retry = _rejected_stage_feedback(payload, messages)
    if stage_retry is not None:
        return stage_retry
    routing_note = _routing_feedback(messages)
    if routing_note is not None:
        return {
            **payload,
            "messages": [*messages, {"role": "user", "content": routing_note}],
        }
    recovery = _loop_recovery(payload, messages)
    if recovery is not None:
        return recovery
    if messages[-1].get("role") != "tool":
        return payload
    pending = {}
    seen = set()
    read_contents = {}
    duplicate_latest = False
    full_read_latest = False
    for message in messages:
        role = message.get("role")
        if role == "user":
            seen.clear()
            read_contents.clear()
            pending.clear()
            duplicate_latest = False
        elif role == "assistant":
            calls = message.get("tool_calls") or []
            if any(_signature(call) is None for call in calls):
                seen.clear()
                read_contents.clear()
            pending = {
                call.get("id"): _signature(call) for call in calls if call.get("id")
            }
            duplicate_latest = False
        elif role == "tool":
            signature = pending.get(message.get("tool_call_id"))
            content = message.get("content")
            duplicate_latest = False
            full_read_latest = False
            if signature is not None and _successful(content):
                name, encoded_args = signature
                args = json.loads(encoded_args)
                scope = (name, args.get("path"))
                if (
                    name == "read_file"
                    and args.get("path") == "/mnt/user-data/uploads/document.txt"
                ):
                    try:
                        value = json.loads(content)
                    except ValueError:
                        value = None
                    if (
                        isinstance(value, dict)
                        and value.get("format") == "clause_lines_v1"
                    ):
                        full_read_latest = (
                            value.get("file_complete") is True
                            and value.get("truncated") is False
                        )
                        content = "".join(row["text"] for row in value["lines"])

                evidence = (scope, hashlib.sha256(content.encode()).hexdigest())
                duplicate_latest = evidence in seen
                if (
                    name == "read_file"
                    and content.strip()
                    and not content.startswith("(empty")
                ):
                    previous = read_contents.setdefault(scope, [])
                    duplicate_latest |= any(content in old for old in previous)
                    previous.append(content)
                seen.add(evidence)
    if not duplicate_latest and not full_read_latest:
        return payload
    logger.warning(
        "CLAUSE_READ_FEEDBACK: input coverage/result availability clarified; all tool choices retained"
    )
    return {
        **payload,
        "messages": [
            *messages,
            {"role": "user", "content": _NOTE if duplicate_latest else _READ_NOTE},
        ],
    }
