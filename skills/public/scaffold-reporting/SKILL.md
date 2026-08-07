---
name: scaffold-reporting
description: Use inside the StackPlanner reporter specialist for SA-style scaffolded report workflows. Writes the final Markdown report from user request, Research Summary, ScopeTree, Evidence Map, and materialized evidence bodies without searching or inventing facts.
---

# Scaffold Reporting

## Boundary

Use this skill only when the reporter is delegated after SA-style pre-research
and ScopeTree planning. The CentralAgent owns orchestration. You own synthesis.
Do not search, fetch, delegate, or invent missing facts.

Return `artifact_type: "report_revision"`.

## Required Inputs

Read all supplied materialized artifact bodies, not only summaries. The report
should be grounded in:

- the current user request and mandatory requirements;
- Research Summary as compressed fact context and task alignment;
- Evidence Ledger and Numeric Claim Map as citation and numeric-claim source of
  truth;
- ScopeTree as the writing contract;
- Evidence Map and materialized evidence bodies as the factual boundary;
- pinned human feedback for revisions.

Research Summary can be used as compressed factual context only when its claims
carry stable `[n]` citations that resolve to the Evidence Ledger. ScopeTree can
guide structure and scope, but it is not a factual source. Final report facts
and numbers must be traceable to `[n]` sources and, for numeric claims, the
Numeric Claim Map.

## Report Rules

- Follow the ScopeTree section order and section responsibilities.
- Cover every first-level ScopeTree section unless the user explicitly narrows
  the deliverable.
- Do not add unrelated main sections.
- Satisfy each node instruction explicitly.
- Preserve the stable candidate set across the whole report.
- For comparison, evaluation, recommendation, ranking, and forecasting tasks,
  use horizontal comparison by dimension, preferably with Markdown tables.
- Do not delete a user-required object because its evidence is weak; disclose
  the evidence boundary in the relevant section.
- Each major analytical section should close the loop: fact -> analysis ->
  judgment.
- Recommendations, rankings, forecasts, evaluations, and option selections must
  give a direct supported conclusion.
- Evidence gaps belong near the affected claims or in concise limitations, not
  as the main report structure.
- Numbers, rankings, dates, institutions, financial data, and quotations must be
  traceable to supplied evidence through stable `[n]` citations.

## Section Draft Mode

If `metadata.kind="section_draft"` or the delegated task explicitly asks for a
single section / leaf group:

- Write only the delegated section or leaf group.
- Use only the supplied section subtree, `section_id`, `covered_leaf_ids`,
  `source_ids`, Numeric Claim Map slice, and evidence slice.
- Do not write the full report introduction, global conclusion, or source list
  unless the delegated section is explicitly responsible for them.
- Preserve stable `[n]` citations exactly; do not renumber sources.
- Keep transitions local to the section so a later merge step can combine
  drafts cleanly.
- Return Markdown section body in `artifact_content`.
- Set `artifact_metadata.kind="section_draft"`, `section_id`,
  `covered_leaf_ids`, `source_ids`, `numeric_claim_ids`, and `evidence_gaps`.

Use `artifact_type="report_revision"` if the runtime does not support a
dedicated `section_draft` artifact type yet.

## Report Merge Mode

If `metadata.kind="report_merge"` or the delegated task asks you to merge
section drafts:

- Do not add new facts, numbers, companies, events, or citations.
- Merge supplied section drafts into one complete Markdown report.
- Normalize heading levels, duplicate transitions, tone, table formatting,
  citation formatting, limitations, and final source list.
- Preserve all valid `[n]` citations and source ordering from the Evidence
  Ledger.
- Check that every supplied ScopeTree first-level section or leaf group is
  represented.
- For long reports, use `write_file` and return a final `report_revision`
  artifact with `created_paths`.

## Citation and Evidence Discipline

- Preserve source titles, URLs, publication dates, and doc IDs when available.
- Use numeric citations `[1]`, `[2]`, `[3]` consistently from the supplied
  Evidence Ledger. Do not use `E1`, `N1`, naked URLs, or a new report-only
  numbering scheme.
- The final source list must be ordered by Evidence Ledger `source_id`.
- Key numbers must cite `[n]` and match Numeric Claim Map values, units,
  denominators, entity scope, time scope, and methodology/scope notes.
- If sources conflict on the same metric, state the conflicting values and
  scope difference instead of silently choosing one.
- Never cite the ScopeTree or an internal logic skeleton as a fact source.
- Represent source conflicts and uncertainty instead of silently choosing the
  convenient source.
- Use the user's requested citation style only if it does not break the stable
  `[n]` lineage requirement.

## Delivery

Produce clean Markdown in the user's language unless explicitly requested
otherwise. Do not include transport JSON, planning notes, or process narration in
the report body.

For long reports, write a unique `/mnt/user-data/outputs/report-<action_id>.md`
when the write tool is available and list the path in
`artifact_metadata.created_paths`. Otherwise return the complete report in
`artifact_content`.

Required metadata:

```json
{
  "completion_status": "complete|partial|blocked",
  "source_artifact_ids": [],
  "scope_tree_artifact_id": "",
  "research_summary_artifact_id": "",
  "citation_style": "[n]",
  "numeric_claim_ids": [],
  "quality_checks": [],
  "evidence_gaps": []
}
```
