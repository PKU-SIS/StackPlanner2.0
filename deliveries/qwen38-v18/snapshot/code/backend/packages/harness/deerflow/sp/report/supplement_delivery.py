"""Independent Markdown delivery for report gap research, never pre-research."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from deerflow.sp.report import store
from deerflow.sp.report.research_delivery import delivery_issues, markdown_body
from deerflow.sp.subagents.adapter import SPSubagentResult, SPSubagentStatus, SPSubagentTask

OUTPUT_INSTRUCTION = """
For this supplementary assignment, return the complete Markdown synthesis directly.
Do not wrap the body in JSON, quotes, code fences or artifact_content. The host
saves the complete summary.md. Use canonical [S<n>] citations adjacent to claims,
URLs and material/line locators. S133/m139 alone is not a citation: write
[S133] (m139, lines 1-20) only when the saved mapping and lines support it.
End with a small Markdown table headed exactly:
| gap_id | execution | material_ids |
|---|---|---|
Include one row per assigned gap, execution searched/not_attempted/technical_error,
and comma-separated saved material IDs (or '-' for none). Report actual execution,
not whether evidence is sufficient; only Outline judges sufficiency. Never claim
searched for untouched work. The host validates declarations against its records.
This table is part of Markdown, not a JSON envelope. Keep the useful full synthesis.
""".strip()
REPAIR_INSTRUCTION = """
Repair the cited Markdown supplementary synthesis using only the supplied body
and saved source/material evidence. No tools, new research or guessed identifiers.
Use canonical [S<n>] citations next to factual claims, with saved source URLs.
Use source_id from saved_evidence; material IDs such as m12 alone are not citations.
Preserve useful content and its language. If the original is only tool-protocol
text, summarize the supplied evidence for the assigned gaps instead of repeating
that text. Omit unsupported claims. Return the complete Markdown body directly.
Do not generate a gap execution table: the host preserves original declarations
and actual tool records separately. You cannot change historical execution.
""".strip()


def _without_execution_table(body: str) -> str:
    """Remove only the reserved execution table, preserving other Markdown."""
    lines = []
    in_table = False
    for line in body.splitlines():
        cells = [part.strip().strip("`") for part in line.strip().strip("|").split("|")]
        if cells == ["gap_id", "execution", "material_ids"]:
            in_table = True
            continue
        if in_table and line.strip().startswith("|") and len(cells) == 3:
            continue
        in_table = False
        lines.append(line)
    return "\n".join(lines).strip()


def _restore_execution_table(body: str, attempts: list[dict]) -> str:
    body = _without_execution_table(body)
    if not attempts:
        return body
    rows = ["| gap_id | execution | material_ids |", "|---|---|---|"]
    rows.extend(f"| {r['gap_id']} | {r['execution']} | {', '.join(r.get('material_ids') or []) or '-'} |" for r in attempts)
    return body + "\n\n" + "\n".join(rows)


def is_supplement(task: SPSubagentTask | None) -> bool:
    metadata = task.metadata if task else {}
    gaps = metadata.get("report_gap_ids")
    return bool(
        task
        and task.subagent_type == "researcher"
        and metadata.get("report_role") == "researcher"
        and metadata.get("report_id")
        and metadata.get("report_pre_research") is not True
        and isinstance(gaps, list)
        and gaps
        and all(isinstance(g, str) and g.strip() for g in gaps)
    )


def parse_attempts(body: str, task: SPSubagentTask) -> tuple[list[dict], list[str]]:
    assigned = set(task.metadata.get("report_gap_ids") or [])
    attempts, problems, seen = [], [], set()
    in_table = False
    has_table = False
    for line in body.splitlines():
        cells = [part.strip().strip("`") for part in line.strip().strip("|").split("|")]
        if cells == ["gap_id", "execution", "material_ids"]:
            in_table = True
            has_table = True
            continue
        if not in_table:
            continue
        if len(cells) != 3 or not line.strip().startswith("|"):
            if line.strip():
                in_table = False
            continue
        if all(re.fullmatch(r":?-+:?", cell) for cell in cells):
            continue
        gap, execution, material_text = cells
        materials = [value.strip().strip("`") for value in material_text.split(",") if value.strip() not in {"", "-"}]
        if gap not in assigned or gap in seen or execution not in {"searched", "not_attempted", "technical_error"} or any(not re.fullmatch(r"m\d+", value) for value in materials):
            problems.append("invalid_gap_execution_row:" + gap)
            continue
        seen.add(gap)
        attempts.append(dict(gap_id=gap, execution=execution, material_ids=materials, note="Declared in supplementary Markdown execution table; host validation still applies."))
    if has_table and assigned - seen:
        problems.append("missing_gap_execution_rows:" + ",".join(sorted(assigned - seen)))
    return attempts, problems


def content_issues(body: str, sources: set[str]) -> list[str]:
    problems = delivery_issues(body, sources)
    if "DSML" in body or "<tool_call>" in body:
        problems.append("tool_protocol_output")
    # A zero-result observation must not invent citations. Existing host
    # execution checks still decide whether an attempt actually ran.
    return [p for p in problems if p != "missing_citations" or sources]


def issues(body: str, task: SPSubagentTask, sources: set[str]) -> list[str]:
    return [*content_issues(body, sources), *parse_attempts(body, task)[1]]


def normalize(raw: Any, task: SPSubagentTask) -> SPSubagentResult:
    status = SPSubagentStatus(getattr(getattr(raw, "status", None), "value", getattr(raw, "status", "failed")))
    text = str(getattr(raw, "result", "") or "")
    body = markdown_body(text)
    attempts, problems = parse_attempts(body, task)
    # Fully valid legacy envelopes remain readable during migration. Never
    # guess malformed JSON or extract an inner object from a broken envelope.
    legacy = {}
    try:
        envelope = json.loads(text)
        if isinstance(envelope, dict):
            legacy = envelope.get("artifact_metadata") or {}
    except (ValueError, TypeError):
        pass
    legacy_envelope = isinstance(legacy, dict) and isinstance(legacy.get("gap_attempts"), list) and not attempts
    if legacy_envelope:
        attempts = legacy["gap_attempts"]
        problems = []
    stop = getattr(raw, "stop_reason", None)
    finish = next((s.get("response_metadata", {}).get("finish_reason") for s in reversed(getattr(raw, "ai_messages", []) or []) if s.get("type") == "ai"), None)
    if finish in {"length", "max_tokens"} and not stop:
        stop = "output_capped"
    error = getattr(raw, "error", None)
    complete = status == SPSubagentStatus.COMPLETED and not stop and not error
    metadata = dict(
        supplement_markdown=True,
        supplement_legacy_envelope=legacy_envelope,
        completion_status="complete" if complete else "partial",
        evidence_gaps=[],
        gap_attempts=attempts,
        supplement_table_issues=problems,
        result_source=getattr(raw, "result_source", None),
    )
    preview = " ".join(body.split())
    return SPSubagentResult(
        status=status,
        result=preview[:680] + "...<truncated>" if len(preview) > 700 else preview,
        artifact_content=body,
        artifact_type="research_observation",
        artifact_metadata=metadata,
        stop_reason=stop,
        error=error,
        finish_reason=finish,
        completion_status=metadata["completion_status"],
        task_id=getattr(raw, "task_id", None),
        token_usage_records=list(getattr(raw, "token_usage_records", None) or []),
    )


def prepare(result: SPSubagentResult, task: SPSubagentTask, ledger: Any, executor: Any) -> SPSubagentResult:
    from deerflow.sp.report.research_continuation import continue_output

    result = continue_output(result, task, ledger, executor, marker="supplement-repair-started.json")
    body = markdown_body(str(result.artifact_content or ""))
    if result.artifact_metadata.get("output_continuation_attempted"):
        result = replace(result, artifact_metadata={**result.artifact_metadata, "gap_attempts": parse_attempts(body, task)[0]})
    materials = ledger.materials()
    sources = {str(m.get("source_id")) for m in materials} & {str(s.get("source_id")) for s in ledger.payload.get("sources", [])}
    # Valid legacy declarations still use the existing native validator.
    legacy_attempts = result.artifact_metadata.get("gap_attempts")
    original_attempts = legacy_attempts or parse_attempts(body, task)[0]

    def check(text, *, repaired=False):
        found = content_issues(text, sources)
        if result.artifact_metadata.get("continuation_skipped_reason"):
            found.append("continuation_skipped:" + result.artifact_metadata["continuation_skipped_reason"])
        if repaired or not result.artifact_metadata.get("supplement_legacy_envelope"):
            _, table_issues = parse_attempts(text, task)
            found.extend(table_issues)
        if result.stop_reason or result.error or result.status != SPSubagentStatus.COMPLETED:
            found.append(result.stop_reason or str(result.error or result.status.value))
        return found

    problems = check(body)
    if not problems:
        return replace(result, artifact_content=body, artifact_metadata={**result.artifact_metadata, "supplement_delivery_issues": []})
    directory = ledger.paths.summary_file(task.action_id).parent / "diagnostics"
    if not (directory / "original.md").exists():
        store.write_text_atomic(directory / "original.md", body)
    claimed = False
    if executor is not None and materials and not result.stop_reason and not result.error and result.status == SPSubagentStatus.COMPLETED and not task.metadata.get("report_supplement_repair"):
        try:
            with (directory / "supplement-repair-started.json").open("x", encoding="utf-8") as stream:
                json.dump({"action_id": task.action_id, "issues": problems}, stream)
            claimed = True
        except FileExistsError:
            pass
    if claimed:
        evidence = []
        remaining_evidence_chars = 24_000
        current_materials = [m for m in materials if m.get("action_id") == task.action_id]
        cited_ids = set(re.findall(r"\[(S\d+)\]", body))
        candidates = [m for m in materials if m.get("source_id") in cited_ids] + current_materials
        if not candidates:
            candidates = materials
        seen_materials = set()
        for material in candidates:
            if material.get("material_id") in seen_materials or remaining_evidence_chars <= 0:
                continue
            seen_materials.add(material.get("material_id"))
            excerpt = str(material.get("preview") or "")[:600]
            path = material.get("path")
            if path and Path(path).is_file():
                with Path(path).open(encoding="utf-8") as stream:
                    excerpt = stream.read(600)
            excerpt = excerpt[:remaining_evidence_chars]
            remaining_evidence_chars -= len(excerpt)
            evidence.append({k: material.get(k) for k in ("source_id", "material_id", "title", "url")} | {"excerpt": excerpt, "excerpt_is_partial": True})
        repair_task = replace(
            task,
            task="Repair this supplementary delivery once, without new research.",
            description="Repair supplementary research delivery",
            expected_output="Complete cited Markdown",
            allowed_tools=[],
            context_refs={
                "supplement_delivery_repair": {
                    "issues": problems,
                    "original_body": body,
                    "saved_evidence": evidence,
                    "original_gap_attempts": original_attempts,
                    "assigned_gaps": (task.context_refs.get("report_packet") or {}).get("gaps", []),
                }
            },
            metadata={**task.metadata, "report_supplement_repair": True},
        )
        result = replace(result, artifact_metadata={**result.artifact_metadata, "supplement_repair_attempted": True})
        try:
            repaired = executor.execute(repair_task)
        except Exception as exc:
            result = replace(result, error=f"Supplement delivery repair failed: {type(exc).__name__}: {exc}")
        else:
            body = _restore_execution_table(markdown_body(str(repaired.artifact_content or "")), original_attempts)
            store.write_text_atomic(directory / "repair.md", body)
            # Correction changes presentation, never historical execution data.
            metadata = {**result.artifact_metadata, "supplement_repair_attempted": True}
            metadata["gap_attempts"] = original_attempts
            result = replace(repaired, artifact_metadata=metadata, token_usage_records=[*result.token_usage_records, *repaired.token_usage_records])
        problems = check(body, repaired=True)
    return replace(result, artifact_content=body, artifact_metadata={**result.artifact_metadata, "supplement_delivery_issues": problems})
