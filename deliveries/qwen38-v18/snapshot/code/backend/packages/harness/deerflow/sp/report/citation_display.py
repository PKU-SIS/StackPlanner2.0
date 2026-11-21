"""Pure final-file citation formatting, after the canonical report is reviewed."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from deerflow.sp.report.citations import CITATION_RE as _CANONICAL_CITATION

_SOURCE_HEADING = "\n## 来源\n"


def render_final_citations(content: str, source_table: Iterable[Mapping[str, Any]]) -> tuple[str, dict[str, int]]:
    """Reuse source-table order; leave unrecognized legacy layouts untouched.

    This is presentation only: it does not verify content, invoke agents, or
    modify source identities, drafts, review inputs or the accepted candidate.
    """
    body, separator, _ = content.rpartition(_SOURCE_HEADING)
    if not separator:
        return content, {}
    sources = list(source_table)
    if not sources:
        return content, {}
    numbering: dict[str, int] = {}
    for source in sources:
        if not isinstance(source, Mapping):
            return content, {}
        source_id = source.get("source_id")
        if not isinstance(source_id, str) or not re.fullmatch(r"S\d+", source_id) or source_id in numbering:
            return content, {}
        numbering[source_id] = len(numbering) + 1

    def replace_marker(match: re.Match[str]) -> str:
        number = numbering.get(match.group("source_id"))
        return f"[{number}]" if number is not None else match.group(0)

    lines = ["## 来源", ""]
    for source in sources:
        number = numbering[source["source_id"]]
        title = str(source.get("title") or "").strip()
        url = str(source.get("url") or "").strip()
        lines.extend([f"[{number}] {title}{(' — ' + url) if url else ''}".rstrip(), ""])
    bibliography = "\n".join(lines).rstrip() + "\n"
    return _CANONICAL_CITATION.sub(replace_marker, body) + "\n" + bibliography, numbering
