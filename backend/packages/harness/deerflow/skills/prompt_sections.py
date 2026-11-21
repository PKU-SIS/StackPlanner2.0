"""Select existing Markdown sections without summarizing or rewriting rules."""

import re


def select_prompt_sections(content: str, requested: list[str] | None) -> str:
    """Keep the preamble and exact H2 sections; fall back to full text on drift.

    Only the host supplies section names. Unknown/duplicate headings must not
    silently omit a contract after a bundled document changes.
    """
    if not requested:
        return content
    headers = []
    offset = 0
    fence = None
    for line in content.splitlines(keepends=True):
        stripped = line.lstrip()
        mark = re.match(r"(`{3,}|~{3,})", stripped)
        if mark:
            marker = mark.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
        elif fence is None and line.startswith("## "):
            headers.append((line[3:].strip(), offset))
        offset += len(line)
    names = [name for name, _ in headers]
    if len(names) != len(set(names)) or not set(requested) <= set(names):
        return content
    selected = [content[:headers[0][1]]]
    for index, (name, start) in enumerate(headers):
        end = headers[index + 1][1] if index + 1 < len(headers) else len(content)
        if name in requested:
            selected.append(content[start:end])
    return "".join(selected).strip()
