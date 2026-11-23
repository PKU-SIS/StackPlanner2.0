"""Compact reads of saved report evidence at its accepted Outline locators."""

from __future__ import annotations

import json
from pathlib import Path

from deerflow.sp.report import store as native
from deerflow.sp.report.citations import CITATION_RE
from deerflow.sp.report.ledger import ReportValidationError


def read_provenance(store, version_id, node_id=None):
    manifest = store.verify(version_id)
    directory = store.directory(version_id) / "artifacts"
    ledger = native.read_json(directory / "ledger.json")

    def saved(ref):
        relative = Path(ref["path"]).relative_to(manifest["origin_directory"]).as_posix()
        if manifest["files"].get(relative) != ref.get("sha256"):
            raise ReportValidationError("unbound snapshot provenance reference")
        return native.read_text(directory / relative)

    tree = json.loads(saved(ledger["tree"]))
    catalog = [{"node_id": n["node_id"], "title": n.get("title", "")} for n in tree.get("nodes", []) if n.get("kind") == "writable"]
    if node_id:
        selected = [node_id]
    else:
        changes = store.directory(version_id) / "changes.json"
        selected = native.read_json(changes).get("changed_nodes", []) if changes.exists() else []
    nodes = {n["node_id"]: n for n in tree.get("nodes", [])}
    sections = []
    for identity in selected:
        node = nodes.get(identity)
        draft = next((d for d in ledger.get("drafts", []) if d.get("is_current") and d.get("node_id") == identity), None)
        if not node or not draft:
            raise ReportValidationError("unknown provenance section")
        content = saved(draft)
        excerpts = []
        evidence_ids = set()
        for ref in node.get("evidence", []):
            material = next((m for m in ledger.get("materials", []) if m.get("material_id") == ref.get("material_id")), None)
            sources = list(ref.get("source_ids") or [])
            if material and material.get("source_id") not in sources:
                sources.append(material["source_id"])
            evidence_ids.update(sources)
            primary = material.get("source_id") if material else next(iter(sources), None)
            source = next((s for s in ledger.get("sources", []) if s.get("source_id") == primary), {})
            row = {
                "material_id": ref.get("material_id"),
                "source_ids": sources,
                "source": {k: source.get(k) for k in ("source_id", "title", "url")},
                "saved_locator": ref.get("locator"),
                "report_use": ref.get("use"),
                "scope": "report_use is the report interpretation; content is the saved source text",
            }
            if material:
                lines = saved(material).splitlines()
                locator = (ref.get("locator") or {}).get("lines")
                if isinstance(locator, list) and len(locator) == 2 and all(type(v) is int for v in locator) and 1 <= locator[0] <= locator[1] <= len(lines):
                    start, end = locator
                    # A compact quotation is a view, never a replacement for the
                    # original evidence. Original ranges and truncation stay explicit.
                    stop = min(end, start + 7)
                    excerpt = "\n".join(f"{i}: {lines[i - 1]}" for i in range(start, stop + 1))
                    shortened = len(excerpt) > 2400
                    displayed = excerpt if not shortened else excerpt[:1200] + "\n[Middle omitted]\n" + excerpt[-1200:]
                    ranges = [[1, len(excerpt)]] if not shortened else [[1, 1200], [len(excerpt) - 1199, len(excerpt)]]
                    row.update(content=displayed, lines=[start, stop], truncated=stop < end or shortened, numbered_excerpt_char_ranges=ranges, total_lines=len(lines), material_sha256=material["sha256"])
                else:
                    row.update(content=None, missing_valid_line_locator=True, material_sha256=material["sha256"], hint="Read material:<id> if more source text is necessary; a report interpretation is not a source quote.")
            else:
                row.update(content=None, missing_saved_material=True)
            excerpts.append(row)
        cited_ids = list(dict.fromkeys(m.group("source_id") for m in CITATION_RE.finditer(content)))
        sections.append(
            {
                "node_id": identity,
                "title": node.get("title"),
                "content": content if len(content) <= 12000 else content[:3000] + "\n[Middle omitted; original remains saved]\n" + content[-9000:],
                "content_truncated": len(content) > 12000,
                "draft_sha256": draft["sha256"],
                "evidence": excerpts,
                "citations_without_saved_locator": [s for s in cited_ids if s not in evidence_ids],
            }
        )
    return {
        "version_id": version_id,
        "section_catalog": catalog,
        "sections": sections,
        "scope": "Saved report passages and their original evidence locators; not fresh research or independent factual verification.",
        "answer_guidance": "Answer from these passages. Separate source statements from report synthesis and recommendations. State missing or truncated evidence explicitly; a missing detail does not require reading every chapter.",
        "next_read": None if sections else "Select provenance:<node_id> from section_catalog.",
    }
