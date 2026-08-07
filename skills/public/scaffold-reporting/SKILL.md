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
- Research Summary for task alignment only;
- ScopeTree as the writing contract;
- Evidence Map and materialized evidence bodies as the factual boundary;
- pinned human feedback for revisions.

Research Summary and ScopeTree can guide structure and scope, but they are not
final factual citation sources. Factual claims must come from materialized
evidence bodies.

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
  traceable to supplied evidence.

## Citation and Evidence Discipline

- Preserve source titles, URLs, publication dates, and doc IDs when available.
- Never cite the Research Summary, ScopeTree, or an internal logic skeleton as a
  fact source.
- Represent source conflicts and uncertainty instead of silently choosing the
  convenient source.
- Use the user's requested citation style when specified; otherwise use clear
  Markdown links or a final source list based on supplied evidence.

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
  "quality_checks": [],
  "evidence_gaps": []
}
```
