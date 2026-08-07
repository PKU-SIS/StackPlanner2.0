---
name: scaffold-preresearch
description: Use inside the StackPlanner researcher specialist for substantial research/report/document tasks that need SA-style pre-research before outline generation. Produces a Research Summary, seed evidence, stable candidate set, explicit dimensions, source inventory, and evidence gaps for downstream ScopeTree planning.
---

# Scaffold Pre-Research

## Boundary

Use this skill only when delegated by StackPlanner CentralAgent for an
SA-style scaffolded report workflow. Your output is planning evidence, not the
final report. Do not write report prose, do not ask the user questions, and do
not delegate.

Return `artifact_type: "research_observation"`.

## Inputs

Use the delegated task, expected output, current user request, mandatory
requirements, and any materialized artifact bodies. If an earlier outline or
Research Summary is supplied, treat it as a constraint and update only what the
delegated task asks you to refresh.

## Query Planning

Before searching or fetching, decompose the user request into evidence needs.
Cover the dimensions that matter for the requested deliverable:

- object or candidate type;
- explicit named objects and required candidate scope;
- time range and recency requirements;
- geography, market, domain, or technology boundary;
- comparison, ranking, recommendation, evaluation, or forecast dimensions;
- metric names, denominators, units, and authority/source preferences;
- final answer shape, such as top-N, options analysis, due diligence, or report.

For comparison tasks, search by dimension rather than only by object. For
ranking, recommendation, evaluation, and forecast tasks, include search terms
for criteria, methodology, outcome metrics, and authoritative sources. Do not
hard-code specific institutions, products, or candidates unless the user or
evidence provides them.

## Evidence Collection

- Search from multiple non-duplicate angles.
- Fetch or preserve full source context when snippets are insufficient.
- Deduplicate sources and identify materially conflicting evidence.
- Track coverage against every explicit user dimension and expected output item.
- Stop retrying equivalent failed queries after two failures; report the exact
  evidence gap and the smallest useful follow-up.

## Research Summary

The Research Summary is global planning context for outline and report
alignment. It is not a factual citation source for the final report.

Include:

- final deliverable and answer objective;
- explicit user dimensions and constraints;
- stable candidate or object set when the task compares multiple objects;
- time, region, industry, data, or methodology boundaries;
- compact evidence inventory with source refs, titles, URLs, and dates when
  available;
- known conflicts and evidence gaps;
- conservative wording needed because of weak or missing evidence;
- suggested seed evidence refs for ScopeTree leaves.

Avoid:

- generic research plans;
- checklist-only output;
- making evidence gaps the main story;
- invented citations, rankings, metrics, or candidates;
- table fields that were not supported by evidence.

## Output Contract

Return one JSON object only. Put the full Research Summary and seed evidence
inventory in `artifact_content`.

Required metadata:

```json
{
  "completion_status": "complete|partial|blocked",
  "queries": [],
  "explicit_dimensions": [],
  "stable_candidate_set": [],
  "source_refs": [],
  "evidence_gaps": []
}
```
