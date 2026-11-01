"""Complete, cited Markdown delivery for the report's pre-research stage only."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from deerflow.sp.report import store
from deerflow.sp.subagents.adapter import SPSubagentResult, SPSubagentStatus, SPSubagentTask

MARKDOWN_OUTPUT_INSTRUCTION = """
Return the complete Markdown research synthesis directly as your final answer.
Do not wrap the body in JSON, quotes, a code fence, or an artifact_content field.
The host saves this complete answer as summary.md and constructs the task-result
metadata itself. Keep theme-organized explanatory paragraphs, canonical [S<n>]
citations next to supported information, source URLs and material locators.
Do not shorten the evidence handoff to a compact summary or a source catalog.
Do not invent sources. Outline judges content sufficiency.
""".strip()

REPAIR_OUTPUT_INSTRUCTION = (
    """
You are repairing the format and citation identifiers of one existing report
pre-research synthesis. No tools are available. Do not search, fetch, add a new
research round, or invent evidence. Preserve the original useful content and
its language. Fix only the listed delivery issues using the supplied original
text and saved source/material excerpts. A material id is not a source id;
cite only source_id values in the supplied evidence. Remove an unsupported
identifier rather than claiming a guessed mapping. If evidence cannot support
a statement, omit that statement or describe the limitation honestly.
""".strip()
    + "\n\n"
    + MARKDOWN_OUTPUT_INSTRUCTION
)


def is_markdown_research(task: SPSubagentTask | None) -> bool:
    return bool(task and task.subagent_type == "researcher" and task.metadata.get("report_role") == "researcher" and task.metadata.get("report_id") and task.metadata.get("report_pre_research") is True)


def markdown_body(value: str) -> str:
    text = value.strip()
    if text.startswith("```markdown\n") and text.endswith("```"):
        return text[len("```markdown\n") : -3].strip()
    # Accept the old, fully valid envelope during migration. Never guess how
    # malformed quotes should have been escaped or extract an inner object.
    candidate = text[7:-3].strip() if text.startswith("```json") and text.endswith("```") else text
    if candidate.startswith("{"):
        try:
            envelope = json.loads(candidate)
        except (ValueError, TypeError):
            return value
        if isinstance(envelope, dict) and isinstance(envelope.get("artifact_content"), str):
            return envelope["artifact_content"]
    return value


def delivery_issues(body: str, sources: set[str]) -> list[str]:
    issues = []
    if not body.strip():
        issues.append("empty_summary")
    if body.lstrip().startswith(("{", "```json")):
        issues.append("raw_json_envelope")
    if "<raw-result-truncated; head and tail preserved>" in body:
        issues.append("truncated_raw_result")
    if any(marker in body for marker in ("[FORCED STOP]", "[Recovered tool evidence]", "<recovered-tool-evidence>")):
        issues.append("recovered_or_forced_output")
    citations = set(re.findall(r"\[(S\d+)\]", body))
    if not citations:
        issues.append("missing_citations")
    if citations - sources:
        issues.append("unknown_sources:" + ",".join(sorted(citations - sources)))
    return issues


def normalize_markdown_research(raw: Any, task: SPSubagentTask) -> SPSubagentResult:
    status_value = getattr(getattr(raw, "status", None), "value", getattr(raw, "status", "failed"))
    try:
        status = SPSubagentStatus(status_value)
    except ValueError:
        status = SPSubagentStatus.FAILED
    body = markdown_body(str(getattr(raw, "result", "") or ""))
    stop = getattr(raw, "stop_reason", None)
    finish_reason = next((step.get("response_metadata", {}).get("finish_reason") for step in reversed(getattr(raw, "ai_messages", []) or []) if step.get("type") == "ai"), None)
    if finish_reason in {"length", "max_tokens"} and not stop:
        stop = "output_capped"
    error = getattr(raw, "error", None)
    metadata = {"pre_research_markdown": True, "result_source": getattr(raw, "result_source", None), "completion_status": "complete" if status == SPSubagentStatus.COMPLETED and not stop and not error else "partial", "evidence_gaps": []}
    summary = " ".join(body.split())
    summary = summary[:680] + "...<truncated>" if len(summary) > 700 else summary
    return SPSubagentResult(
        status=status,
        result=summary,
        artifact_content=body,
        artifact_type="research_observation",
        artifact_metadata=metadata,
        error=error,
        stop_reason=stop,
        task_id=getattr(raw, "task_id", None),
        token_usage_records=list(getattr(raw, "token_usage_records", None) or []),
        completion_status=metadata["completion_status"],
        finish_reason=finish_reason,
    )


def prepare_delivery(result: SPSubagentResult, task: SPSubagentTask, ledger: Any, executor: Any) -> SPSubagentResult:
    """At most one tool-free repair. Diagnostics never become formal summaries."""
    body = markdown_body(str(result.artifact_content or ""))
    materials = ledger.materials()
    sources = {str(item.get("source_id")) for item in materials} & {str(item.get("source_id")) for item in ledger.payload.get("sources", [])}
    issues = delivery_issues(body, sources)
    if result.stop_reason or result.error:
        issues.append(result.stop_reason or str(result.error))
    if result.artifact_metadata.get("result_source") == "recovered_tool_evidence":
        issues.append("recovered_tool_evidence")
    if not issues:
        return replace(result, artifact_content=body)
    directory = ledger.paths.summary_file(task.action_id).parent / "diagnostics"
    if not (directory / "original.md").exists():
        store.write_text_atomic(directory / "original.md", str(result.artifact_content or ""))
    claimed = False
    if executor is not None and materials and not result.stop_reason and not result.error and result.status == SPSubagentStatus.COMPLETED and not task.metadata.get("report_summary_repair"):
        try:
            with (directory / "repair-started.json").open("x", encoding="utf-8") as stream:
                json.dump({"issues": issues, "action_id": task.action_id}, stream)
            claimed = True
        except FileExistsError:
            pass
    if claimed:
        evidence = []
        for material in materials:
            excerpt = str(material.get("preview") or "")
            path = material.get("path")
            if path and Path(path).is_file():
                with Path(path).open(encoding="utf-8") as stream:
                    excerpt = stream.read(1200)
            evidence.append({key: material.get(key) for key in ("source_id", "material_id", "title", "url")} | {"excerpt": excerpt, "excerpt_is_partial": True})
        repair_task = replace(
            task,
            task="Repair this existing synthesis once; do not perform new research.",
            description="Repair pre-research delivery",
            expected_output="Complete cited Markdown only",
            allowed_tools=[],
            context_refs={"research_summary_repair": {"issues": issues, "original_body": body, "saved_evidence": evidence}},
            metadata={**task.metadata, "report_summary_repair": True},
        )
        try:
            repaired = executor.execute(repair_task)
        except Exception as exc:  # noqa: BLE001 - preserve this attempt and close it
            result = replace(result, error=f"Summary repair failed: {type(exc).__name__}: {exc}")
        else:
            body = markdown_body(str(repaired.artifact_content or ""))
            store.write_text_atomic(directory / "repair.md", body)
            result = replace(repaired, token_usage_records=[*result.token_usage_records, *repaired.token_usage_records], artifact_metadata={**repaired.artifact_metadata, "summary_repair_attempted": True})
        issues = delivery_issues(body, sources)
        if result.stop_reason or result.error or result.status != SPSubagentStatus.COMPLETED:
            issues.append(result.stop_reason or result.error or result.status.value)
    return replace(result, artifact_content=body, artifact_metadata={**result.artifact_metadata, "research_delivery_issues": issues})
