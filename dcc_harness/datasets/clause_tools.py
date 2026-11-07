"""CLAUSE source search: no silent long-line skips or irrelevant previews."""

from __future__ import annotations
import asyncio
import bisect
import json
import regex
from langchain.tools import tool
from deerflow.tools.types import Runtime
from deerflow.sandbox.tools import (
    read_current_file_content,
    grep_tool as native_grep,
    read_file_tool as native_read,
)
from deerflow.config.app_config import get_app_config
from deerflow.sandbox.search import path_matches

_SOURCE = "/mnt/user-data/uploads/document.txt"


def search_document(
    text,
    pattern,
    *,
    literal=False,
    case_sensitive=False,
    max_results=100,
    timeout_seconds=1.0,
    start_offset=0,
):
    """Search all characters with a bounded regex engine; quote actual matches."""
    result = {
        "status": "success",
        "search_complete": True,
        "truncated": False,
        "matched_lines": 0,
        "matches": [],
    }
    if (
        isinstance(max_results, bool)
        or not isinstance(max_results, int)
        or not 1 <= max_results <= 200
    ):
        return {
            **result,
            "status": "error",
            "search_complete": False,
            "reason": "max_results must be 1..200",
        }
    if (
        isinstance(start_offset, bool)
        or not isinstance(start_offset, int)
        or not 0 <= start_offset <= len(text) + 1
    ):
        return {
            **result,
            "status": "error",
            "search_complete": False,
            "reason": "invalid start_offset",
        }
    result["start_offset"] = start_offset
    offsets = [0] + [i + 1 for i, c in enumerate(text) if c == "\n"]
    seen = set()
    try:
        expression = regex.escape(pattern) if literal else pattern
        flags = regex.MULTILINE | (0 if case_sensitive else regex.IGNORECASE)
        compiled = regex.compile(expression, flags=flags)
        matches = (
            ()
            if start_offset > len(text)
            else compiled.finditer(text, pos=start_offset, timeout=timeout_seconds)
        )
        for match in matches:
            line = bisect.bisect_right(offsets, match.start())
            if len(result["matches"]) >= max_results:
                last = result["matches"][-1]
                result.update(
                    search_complete=False,
                    truncated=True,
                    next_offset=max(
                        last["match_end_offset"], last["match_start_offset"] + 1
                    ),
                )
                break
            seen.add(line)
            line_start = offsets[line - 1]
            line_end = text.find("\n", match.start())
            if line_end < 0:
                line_end = len(text)
            start = max(line_start, match.start() - 100)
            end = min(
                line_end,
                max(match.start() + 160, min(match.end() + 100, match.start() + 400)),
            )
            result["matches"].append(
                {
                    "line": line,
                    "match_start_offset": match.start(),
                    "match_end_offset": match.end(),
                    "match_column": match.start() - line_start + 1,
                    "excerpt_start_offset": start,
                    "excerpt_end_offset": end,
                    "excerpt_truncated": start > line_start
                    or end < line_end
                    or end < match.end(),
                    "start_column": start - line_start + 1,
                    "text": text[start:end],
                }
            )
    except TimeoutError:
        result.update(
            status="incomplete", search_complete=False, reason="regex_timeout"
        )
    except regex.error:
        result.update(status="error", search_complete=False, reason="invalid_regex")
    result["matched_lines"] = len(seen)
    result["match_count"] = len(result["matches"])
    return result


@tool("grep", parse_docstring=True)
def grep_tool(
    runtime: Runtime,
    description: str,
    pattern: str,
    path: str,
    glob: str | None = None,
    literal: bool = False,
    case_sensitive: bool = False,
    max_results: int = 100,
    start_offset: int = 0,
) -> str:
    """Search text with line references; CLAUSE source searches include long paragraphs.

    Args:
        description: Short reason for searching.
        pattern: Regex or literal text to search for.
        path: Absolute source file or uploads directory; other paths use native grep.
        glob: Optional candidate filename filter, e.g. **/*.txt.
        literal: Treat the pattern as literal text when true.
        case_sensitive: Match case when true.
        max_results: Maximum matching occurrences, from 1 to 200.
        start_offset: Zero-based character offset for source search; use next_offset to continue a truncated result.
    """
    if path not in {_SOURCE, "/mnt/user-data/uploads", "/mnt/user-data/uploads/"}:
        if start_offset:
            return "Error: start_offset pagination is supported only for the CLAUSE source document."
        return native_grep.func(
            runtime,
            description,
            pattern,
            path,
            glob,
            literal,
            case_sensitive,
            max_results,
        )
    if glob is not None and not path_matches(glob, "document.txt"):
        return json.dumps(
            {
                "status": "success",
                "search_complete": True,
                "truncated": False,
                "matched_lines": 0,
                "matches": [],
                "path": _SOURCE,
            }
        )
    try:
        text = read_current_file_content(runtime, _SOURCE)
    except Exception as exc:
        return json.dumps(
            {
                "status": "error",
                "search_complete": False,
                "reason": type(exc).__name__,
                "path": _SOURCE,
            }
        )
    result = search_document(
        text,
        pattern,
        literal=literal,
        case_sensitive=case_sensitive,
        max_results=max_results,
        start_offset=start_offset,
    )
    return json.dumps({**result, "path": _SOURCE}, ensure_ascii=False)


async def _grep_async(
    runtime: Runtime,
    description: str,
    pattern: str,
    path: str,
    glob: str | None = None,
    literal: bool = False,
    case_sensitive: bool = False,
    max_results: int = 100,
    start_offset: int = 0,
) -> str:
    return await asyncio.to_thread(
        grep_tool.func,
        runtime,
        description,
        pattern,
        path,
        glob,
        literal,
        case_sensitive,
        max_results,
        start_offset,
    )


grep_tool.coroutine = _grep_async


_NUMERIC_REFERENCE = regex.compile(
    r"\b(section|article|clause|schedule|appendix|exhibit|paragraph)[ \t]+(\d+(?:\.\d+)*)\b",
    regex.IGNORECASE,
)


def numbered_mentions(lines):
    """Index literal numbered mentions, including references to outside documents."""
    found = {}
    count = 0
    for row in lines:
        for match in _NUMERIC_REFERENCE.finditer(row["text"]):
            label = f"{match[1].lower()} {match[2]}"
            locations = found.setdefault(label, [])
            if not locations or locations[-1] != row["line"]:
                if count == 256:
                    return {"lines_by_label": found, "complete": False}
                locations.append(row["line"])
                count += 1
    return {"lines_by_label": found, "complete": True}


def document_read(text, *, start_line=None, end_line=None, max_chars=262144):
    """Number source lines without altering their text or claiming unseen coverage."""
    source_lines = text.splitlines(keepends=True)
    start = 1 if start_line is None else start_line
    if start < 1 or (end_line is not None and end_line < start):
        raise ValueError("invalid line range")
    end = len(source_lines) if end_line is None else min(end_line, len(source_lines))
    lines = [{"line": i + 1, "text": source_lines[i]} for i in range(start - 1, end)]
    result = {
        "format": "clause_lines_v1",
        "status": "success",
        "path": _SOURCE,
        "total_lines": len(source_lines),
        "start_line": start,
        "end_line": end,
        "eof": end == len(source_lines),
        "file_complete": start == 1 and end == len(source_lines),
        "truncated": False,
        "lines": lines,
    }
    if max_chars > 0:
        while (
            len(json.dumps(result, ensure_ascii=False)) > max_chars and result["lines"]
        ):
            result["lines"].pop()
            last = result["lines"][-1]["line"] if result["lines"] else start - 1
            result.update(
                end_line=last,
                eof=False,
                file_complete=False,
                truncated=True,
                next_start_line=last + 1,
            )
        if result["truncated"] and not result["lines"]:
            result.update(
                status="incomplete", reason="one_source_line_exceeds_output_budget"
            )
    if result["file_complete"]:
        mentions = numbered_mentions(lines)
        if mentions["lines_by_label"]:
            indexed = {"numbered_mentions": mentions, **result}
            if max_chars <= 0 or len(json.dumps(indexed, ensure_ascii=False)) <= max_chars:
                return indexed
    return result


@tool("read_file", parse_docstring=True)
def read_file_tool(
    runtime: Runtime,
    description: str,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """Read text; CLAUSE source replies include line numbers and exact coverage metadata.

    Args:
        description: Short reason for reading.
        path: Absolute file path. Source line labels are metadata, not source text.
        start_line: Optional first line, 1-indexed and inclusive.
        end_line: Optional last line, inclusive; omitted means EOF.
    """
    if path != _SOURCE:
        return native_read.func(runtime, description, path, start_line, end_line)
    try:
        text = read_current_file_content(runtime, path)
        config = get_app_config().sandbox
        limit = config.read_file_output_max_chars if config else 50000
        result = document_read(
            text, start_line=start_line, end_line=end_line, max_chars=limit
        )
        if result["status"] != "success":
            return (
                "Error: source read incomplete ("
                + result.get("reason", "output_budget")
                + ")."
            )
        return json.dumps(result, ensure_ascii=False)
    except ValueError:
        return "Error: invalid line range; use positive 1-indexed bounds with end_line >= start_line."
    except Exception as exc:
        return "Error: source read failed (" + type(exc).__name__ + ")."


async def _read_async(
    runtime: Runtime,
    description: str,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    return await asyncio.to_thread(
        read_file_tool.func, runtime, description, path, start_line, end_line
    )


read_file_tool.coroutine = _read_async


def passage_list(value):
    # Some compatible tool-call providers encode nested arrays as JSON strings.
    if isinstance(value, str):
        value = json.loads(value) if value.lstrip().startswith("[") else [value]
    if not isinstance(value, list) or any(not isinstance(p, str) for p in value):
        raise ValueError("expected an array of passage strings")
    return value


# Source clause labels may start a line or a paragraph flattened onto the same line.
_CLAUSE_PREFIX = regex.compile(
    r"(?:^|(?<=[.!?;:])[ \t]{2,})[ \t]*(?P<prefix>"
    r"(?:(?:section|article|clause|§)[ \t]+(?:\d+(?:\.\d+)*|[IVXLCDM]+)\.?|"
    r"\d+(?:\.\d+)+\.?|\d{1,3}[.)]|\([a-zA-Z0-9ivxIVX]{1,4}\))"
    r"(?:\([a-zA-Z0-9ivxIVX]{1,4}\))*[ \t]+)\Z",
    regex.IGNORECASE | regex.MULTILINE,
)


def locate_passages(source, passages):
    """Check exact quotations; a missing candidate is a finding, not a shell error."""
    passages = passage_list(passages)
    if (
        not passages
        or len(passages) > 256
        or any(not isinstance(p, str) or not p.strip() for p in passages)
    ):
        raise ValueError("provide 1..256 nonblank passages")
    starts = [0] + [i + 1 for i, char in enumerate(source) if char == "\n"]
    # Match evidence locations without normalizing the submitted answer.
    collapsed = []
    source_offsets = []
    for i, char in enumerate(source):
        char = " " if char.isspace() else char
        if char == " " and collapsed and collapsed[-1] == " ":
            continue
        collapsed.append(char)
        source_offsets.append(i)
    compact_source = "".join(collapsed)
    locations = []
    for index, p in enumerate(passages):
        offset = source.find(p)
        end = offset + len(p) if offset >= 0 else None
        exact = offset >= 0
        if not exact:
            compact = " ".join(p.split())
            i = compact_source.find(compact)
            if i >= 0:
                offset = source_offsets[i]
                end = source_offsets[i + len(compact) - 1] + 1
        row = {
            "index": index,
            "found": exact,
            "start_line": bisect.bisect_right(starts, offset) if offset >= 0 else None,
            "end_line": bisect.bisect_right(starts, end - 1)
            if end is not None
            else None,
        }
        if not exact and end is not None:
            row.update(
                comparison="whitespace_only", source_start=offset, source_end=end
            )
        if offset >= 0:
            match = _CLAUSE_PREFIX.search(
                source, starts[bisect.bisect_right(starts, offset) - 1], offset
            )
            if match and match.end() == offset:
                # A repeated passage may belong to a different, unnumbered clause.
                needle = " ".join(p.split())
                first = compact_source.find(needle)
                if compact_source.find(needle, first + 1) < 0:
                    row["missing_clause_prefix"] = match.group("prefix")
        locations.append(row)
    return locations


@tool("clause_check_passages", parse_docstring=True)
def check_passages_tool(runtime: Runtime, answer_path: str) -> str:
    """Check every quotation in an existing answer against the current source by filename.

    Args:
        answer_path: Absolute virtual path of the assigned .json file under outputs.
    """
    from .clause import parse_answer
    from pathlib import PurePosixPath

    try:
        path = PurePosixPath(answer_path)
        if (
            path.parent != PurePosixPath("/mnt/user-data/outputs")
            or path.suffix != ".json"
        ):
            raise ValueError("use a JSON file directly under outputs")
        items = parse_answer(None, read_current_file_content(runtime, answer_path))
        source = read_current_file_content(runtime, _SOURCE)
        locations = []
        for i in range(0, len(items), 128):
            group = locate_passages(
                source, [item["text"] for item in items[i : i + 128]]
            )
            locations.extend({**row, "index": row["index"] + i} for row in group)
        return json.dumps({"status": "success", "locations": locations})
    except (ValueError, OSError, TypeError) as exc:
        return json.dumps({"status": "error", "reason": str(exc)})
