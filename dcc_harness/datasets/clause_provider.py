"""Restore visible Qwen function markup for a bound native Central action."""

import json
import logging
import re
from uuid import uuid4

from jsonschema import Draft202012Validator
from langchain_core.messages import AIMessage

from dcc_harness.evaluation.benchmark_runner import BenchmarkVllmChatModel

_CALL = re.compile(
    r"<tool_call>\s*<function=([A-Za-z_]\w*)>(.*?)</function>\s*</tool_call>", re.S
)
_PARAM = re.compile(r"\s*<parameter=([A-Za-z_]\w*)>(.*?)</parameter>", re.S)
_DELIMITER = re.compile(r"</?(?:tool_call|function|parameter)(?:[=>]|\s)")
logger = logging.getLogger(__name__)


def recover_control(result, options):
    tools = options.get("tools") or []
    bound = {
        t["function"]["name"]: t["function"]
        for t in tools
        if isinstance(t, dict) and t.get("type") == "function"
    }
    choice = options.get("tool_choice")
    if (
        "sp_delegate" not in bound
        or len(bound) != len(tools)
        or not all(n.startswith("sp_") for n in bound)
        or choice == "none"
    ):
        return result
    for gen in result.generations:
        msg = gen.message
        if not isinstance(msg, AIMessage) or msg.tool_calls or msg.invalid_tool_calls:
            continue
        finish = (gen.generation_info or {}).get(
            "finish_reason"
        ) or msg.response_metadata.get("finish_reason")
        if (
            finish != "stop"
            or msg.additional_kwargs.get("refusal")
            or msg.additional_kwargs.get("reasoning_promoted_to_content")
        ):
            continue
        match = (
            _CALL.fullmatch(msg.content.strip())
            if isinstance(msg.content, str)
            else None
        )
        if not match or match[1] not in bound:
            continue
        name, body = match.groups()
        if (
            isinstance(choice, dict)
            and (choice.get("function") or {}).get("name") != name
        ):
            continue
        schema = bound[name].get("parameters")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            continue
        properties = schema.get("properties", {})
        args = {}
        cursor = 0
        while body[cursor:].strip():
            p = _PARAM.match(body, cursor)
            if (
                not p
                or p[1] in args
                or p[1] not in properties
                or _DELIMITER.search(p[2])
            ):
                break
            key, raw = p.groups()
            value = raw.strip("\r\n")
            rule = properties[key]
            if not Draft202012Validator(rule).is_valid(value):
                try:
                    value = json.loads(value)
                except ValueError:
                    break
            args[key] = value
            cursor = p.end()
        else:
            if Draft202012Validator(schema).is_valid(args):
                msg.tool_calls = [
                    dict(
                        name=name, args=args, id="call_" + uuid4().hex, type="tool_call"
                    )
                ]
                msg.content = ""
                logger.info("Recovered visible CLAUSE Central function call: %s", name)
    return result


def _bound(options, name):
    for tool in options.get("tools") or ():
        if isinstance(tool, dict) and (tool.get("function") or {}).get("name") == name:
            return tool
    return None


def _normalize_shell_labels(result, options):
    if _bound(options, "bash"):
        for gen in result.generations:
            for call in getattr(gen.message, "tool_calls", []):
                args = call["args"]
                if (
                    call["name"] == "bash"
                    and set(args) == {"command"}
                    and isinstance(args["command"], str)
                ):
                    args["description"] = "Run the delegated shell command"
    return result


def _report_cut_shell(result, options, limit):
    """Pass an incomplete call to native argument validation, never to the shell."""
    tool = _bound(options, "bash")
    if not tool or options.get("tool_choice") == "none":
        return result
    choice = options.get("tool_choice")
    if (
        isinstance(choice, dict)
        and (choice.get("function") or {}).get("name") != "bash"
    ):
        return result
    schema = tool["function"].get("parameters") or {}
    if "command" not in schema.get("required", []) or "description" not in schema.get(
        "properties", {}
    ):
        return result
    for gen in result.generations:
        msg = gen.message
        invalid = getattr(msg, "invalid_tool_calls", [])
        tokens = (msg.usage_metadata or {}).get("output_tokens", 0)
        if (
            not isinstance(limit, int)
            or tokens < limit
            or msg.tool_calls
            or len(invalid) != 1
            or invalid[0].get("name") != "bash"
            or not invalid[0].get("id")
            or msg.additional_kwargs.get("refusal")
        ):
            continue
        # Missing required command means ToolNode returns a validation error.
        # Keep the model-selected name/ID and discard all partial shell text.
        msg.tool_calls = [
            dict(
                name="bash",
                id=invalid[0]["id"],
                type="tool_call",
                args={
                    "description": f"Rejected truncated bash arguments at {tokens} output tokens. The command was not executed."
                },
            )
        ]
        msg.invalid_tool_calls = []
        msg.additional_kwargs["clause_rejected_call"] = "truncated_bash"
    return result


def _answer_write_retry(result, options, default_limit):
    """Retry only an exhausted, unexecuted answer write with a resolved destination."""
    import copy
    from .clause_policy import PREFIX

    tool = _bound(options, "write_file")
    if not tool or len(result.generations) != 1 or options.get("tool_choice") == "none":
        return None
    explicit = options.get("tool_choice")
    if (
        isinstance(explicit, dict)
        and (explicit.get("function") or {}).get("name") != "write_file"
    ):
        return None
    msg = result.generations[0].message
    bad = getattr(msg, "invalid_tool_calls", [])
    limit = (
        options.get("max_completion_tokens")
        or options.get("max_tokens")
        or default_limit
    )
    if (
        not isinstance(limit, int)
        or not 0 < limit < 24576
        or msg.tool_calls
        or msg.additional_kwargs.get("refusal")
        or len(bad) != 1
        or bad[0]["name"] != "write_file"
        or (msg.usage_metadata or {}).get("output_tokens", 0) < limit
    ):
        return None
    # Only parse complete header fields. Never repair the truncated content.
    raw = bad[0]["args"]
    if not isinstance(raw, str) or not raw.lstrip().startswith("{"):
        return None
    rest = raw.lstrip()[1:].lstrip()
    header = {}
    decoder = json.JSONDecoder()
    try:
        while rest:
            key, i = decoder.raw_decode(rest)
            rest = rest[i:].lstrip()
            if not rest.startswith(":") or not isinstance(key, str) or key in header:
                return None
            rest = rest[1:].lstrip()
            if key == "content":
                break
            if key not in ("path", "description", "append"):
                return None
            header[key], i = decoder.raw_decode(rest)
            rest = rest[i:].lstrip()
            if not rest.startswith(","):
                return None
            rest = rest[1:].lstrip()
    except ValueError:
        return None
    path = header.get("path")
    # Confirm that the unfinished string started a CLAUSE array of findings.
    if (
        not isinstance(path, str)
        or not re.fullmatch(re.escape(PREFIX) + r"[\w.-]+\.json", path)
        or header.get("append", False) is not False
        or not re.match(
            r'^"\s*\[(?:\\[rnt]|\s)*\{(?:\\[rnt]|\s)*\\"(?:text|explanation)\\"\s*:',
            rest,
        )
    ):
        return None
    selected = copy.deepcopy(tool)
    props = selected["function"]["parameters"]["properties"]
    props["path"] = {"type": "string", "enum": [path]}
    props["content"] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "explanation": {"type": "string"},
            },
            "required": ["text", "explanation"],
            "additionalProperties": False,
        },
    }
    if "append" in props:
        props["append"] = {"type": "boolean", "enum": [False]}
    kept = {
        k: v
        for k, v in options.items()
        if k not in {"max_tokens", "max_completion_tokens", "parallel_tool_calls"}
    }
    return {
        **kept,
        "tools": [selected],
        "tool_choice": {"type": "function", "function": {"name": "write_file"}},
        "max_completion_tokens": min(24576, limit * 3),
    }


def _serialized_write(first, retry, options):
    from .clause import parse_answer
    from langchain_core.messages.ai import add_usage

    original = first.generations[0].message
    if len(retry.generations) != 1:
        return first
    gen = retry.generations[0]
    msg = gen.message
    finish = (gen.generation_info or {}).get(
        "finish_reason"
    ) or msg.response_metadata.get("finish_reason")
    accepted = False
    if (
        len(msg.tool_calls) == 1
        and not msg.invalid_tool_calls
        and not msg.additional_kwargs.get("refusal")
        and finish in {"tool_calls", "stop"}
    ):
        call = msg.tool_calls[0]
        schema = options["tools"][0]["function"]["parameters"]
        args = call["args"]
        if (
            call["name"] == "write_file"
            and Draft202012Validator(schema).is_valid(args)
            and isinstance(args.get("content"), list)
        ):
            body = json.dumps(
                args["content"], ensure_ascii=False, separators=(",", ":")
            )
            try:
                parse_answer(None, body)
            except ValueError:
                pass
            else:
                call["args"] = {**args, "content": body}
                accepted = True
    combined = add_usage(original.usage_metadata, msg.usage_metadata)
    chosen = retry if accepted else first
    output = chosen.generations[0].message
    output.usage_metadata = combined
    output.additional_kwargs["clause_write_retry"] = {
        "first_output_tokens": (
            original.response_metadata.get("token_usage") or {}
        ).get("completion_tokens")
        or (first.generations[0].generation_info or {})
        .get("token_usage", {})
        .get("completion_tokens"),
        "retry_cap": options["max_completion_tokens"],
        "accepted": accepted,
    }
    return chosen


def _needs_delivery_body(msg):
    from .clause import parse_answer

    if (
        msg.tool_calls
        or msg.invalid_tool_calls
        or msg.additional_kwargs.get("refusal")
        or not isinstance(msg.content, str)
        or not msg.content.strip()
    ):
        return False
    try:
        body = json.loads(msg.content)
    except ValueError:
        body = None
    if isinstance(body, dict):
        md = body.get("artifact_metadata") or {}
        if (
            not isinstance(md, dict)
            or md.get("completion_status") in {"partial", "blocked"}
            or md.get("evidence_gaps")
            or md.get("created_paths")
        ):
            return False
        body = body.get("artifact_content")
    try:
        parse_answer(None, body if isinstance(body, str) else json.dumps(body))
    except ValueError:
        return True
    return False


class ClauseVllmChatModel(BenchmarkVllmChatModel):
    def _receipt_options(self, result, messages, kwargs, *, cut_write=False):
        from .clause_feedback import _final_answer_closeout

        if (
            len(result.generations) != 1
            or not kwargs.get("tools")
            or kwargs.get("tool_choice") not in (None, "auto")
        ):
            return None
        gen = result.generations[0]
        finish = (gen.generation_info or {}).get(
            "finish_reason"
        ) or gen.message.response_metadata.get("finish_reason")
        if not cut_write and (finish != "stop" or not _needs_delivery_body(gen.message)):
            return None
        wire = self._get_request_payload(messages, **kwargs)
        if wire.get("response_format"):
            return None
        names = [t.get("function", {}).get("name", "") for t in wire.get("tools", [])]
        closeout = _final_answer_closeout(
            wire, wire.get("messages", []), names, returning=True
        )
        if closeout is None:
            return None
        kept = {
            k: v
            for k, v in kwargs.items()
            if k
            not in {
                "tools",
                "tool_choice",
                "parallel_tool_calls",
                "max_tokens",
                "max_completion_tokens",
            }
        }
        return {
            **kept,
            "response_format": closeout["response_format"],
            "max_completion_tokens": closeout.get("max_completion_tokens")
            or closeout.get("max_tokens")
            or self.max_tokens,
        }

    @staticmethod
    def _record_receipt(original, repaired):
        from langchain_core.messages.ai import add_usage

        a = original.generations[0].message
        b = repaired.generations[0].message
        b.usage_metadata = add_usage(a.usage_metadata, b.usage_metadata)
        b.additional_kwargs["clause_receipt_retry"] = True
        return repaired

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        result = super()._generate(
            messages, stop=stop, run_manager=run_manager, **kwargs
        )
        retry = _answer_write_retry(result, kwargs, self.max_tokens)
        receipt = self._receipt_options(result, messages, kwargs, cut_write=retry is not None)
        if receipt:
            repaired = super()._generate(messages, stop=stop, run_manager=run_manager, **receipt)
            result = self._record_receipt(result, repaired)
        elif retry:
            attempt = super()._generate(messages, stop=stop, run_manager=run_manager, **retry)
            result = _serialized_write(result, attempt, retry)
        limit = (
            kwargs.get("max_completion_tokens")
            or kwargs.get("max_tokens")
            or self.max_tokens
        )
        result = _report_cut_shell(result, kwargs, limit)
        return _normalize_shell_labels(recover_control(result, kwargs), kwargs)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        result = await super()._agenerate(
            messages, stop=stop, run_manager=run_manager, **kwargs
        )
        retry = _answer_write_retry(result, kwargs, self.max_tokens)
        receipt = self._receipt_options(result, messages, kwargs, cut_write=retry is not None)
        if receipt:
            repaired = await super()._agenerate(messages, stop=stop, run_manager=run_manager, **receipt)
            result = self._record_receipt(result, repaired)
        elif retry:
            attempt = await super()._agenerate(messages, stop=stop, run_manager=run_manager, **retry)
            result = _serialized_write(result, attempt, retry)
        limit = (
            kwargs.get("max_completion_tokens")
            or kwargs.get("max_tokens")
            or self.max_tokens
        )
        result = _report_cut_shell(result, kwargs, limit)
        return _normalize_shell_labels(recover_control(result, kwargs), kwargs)
