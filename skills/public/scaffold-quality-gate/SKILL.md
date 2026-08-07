---
name: scaffold-quality-gate
description: Use inside the StackPlanner reporter or verifier for SA-style scaffolded reports to check ScopeTree coverage, node instructions, evidence boundaries, citations, candidate stability, comparison structure, and completion status before finalizing.
---

# Scaffold Quality Gate

## Boundary

Use this skill before marking an SA-style scaffolded report complete. It is a
quality gate over the supplied report, ScopeTree, Research Summary, Evidence
Map, materialized evidence bodies, and mandatory user requirements. Do not add
new facts by memory. If evidence is missing, return a precise gap.

## Checks

Verify:

- every explicit user requirement is addressed;
- every first-level ScopeTree section is represented in the report;
- every relevant node instruction is satisfied;
- stable candidate/object set is preserved across the report;
- comparison tasks use real cross-dimensional comparison, not only sequential
  object descriptions;
- recommendations, rankings, evaluations, forecasts, or conclusions directly
  answer the user request;
- factual claims are supported by materialized evidence bodies;
- citations and source names come from supplied evidence;
- Research Summary, ScopeTree, and logic skeletons are not cited as factual
  sources;
- evidence gaps are disclosed without becoming the main structure;
- the output is a final readable report, not notes, an outline, or a JSON
  envelope;
- numbers, units, dates, rankings, and denominators are internally consistent.

## Completion Status

Use `completion_status: "complete"` only when the report is usable and all
required checks pass or remaining limitations are explicitly disclosed and do not
block the requested deliverable.

Use `completion_status: "partial"` when the report is mostly usable but needs
focused research or revision before finalization.

Use `completion_status: "blocked"` when missing user input, unavailable
evidence, or tool failure prevents a meaningful report.

## Metadata

Record a compact checklist in `artifact_metadata.quality_checks`, plus any
remaining `evidence_gaps`. If a check fails, name the affected ScopeTree node,
claim, candidate, or section so CentralAgent can delegate focused correction.
