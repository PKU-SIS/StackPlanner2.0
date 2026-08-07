---
name: scaffold-preresearch
description: Use inside the StackPlanner researcher specialist for substantial research/report/document tasks that need SA-style pre-research before outline generation. Produces a compact Research Summary, Evidence Ledger, Numeric Claim Map, stable candidate set, explicit dimensions, and evidence gaps for downstream ScopeTree planning and report synthesis.
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
- Assign one stable numeric source id to every retained source, formatted as
  `[1]`, `[2]`, `[3]`, and keep that numbering stable for all downstream stages.
- Extract decision-critical numbers into a Numeric Claim Map with their source
  ids, units, metric definitions, entity, time scope, region scope, and
  methodology/scope notes when available.
- Stop retrying equivalent failed queries after two failures; report the exact
  evidence gap and the smallest useful follow-up.

## Research Summary

The Research Summary is a compact compressed fact layer plus planning context
for outline and report alignment. It may be used by outline and reporter as
fact context, but every key factual statement, number, ranking, financing
amount, shipment figure, market size, policy fact, or order claim must carry a
stable `[n]` citation that resolves to the Evidence Ledger. Do not include
untraceable factual claims.

Include:

- final deliverable and answer objective;
- explicit user dimensions and constraints;
- stable candidate or object set when the task compares multiple objects;
- time, region, industry, data, or methodology boundaries;
- compact evidence inventory using stable `[n]` source ids;
- key numeric claims with matching `[n]` citations and Numeric Claim Map ids;
- known conflicts and evidence gaps;
- conservative wording needed because of weak or missing evidence;
- suggested seed `[n]` evidence refs for ScopeTree leaves.

Avoid:

- generic research plans;
- checklist-only output;
- making evidence gaps the main story;
- invented citations, rankings, metrics, or candidates;
- uncited factual compression such as "multiple sources show..." without a
  supporting `[n]` citation;
- table fields that were not supported by evidence.

## Evidence Ledger

Create an Evidence Ledger for every retained source. Use numeric ids only. Do
not use `E1`, `E2`, `N1`, `N2`, naked URLs, or per-stage renumbering as the
downstream citation format.

Each item should have this shape:

```json
{
  "source_id": 1,
  "citation": "[1]",
  "title": "",
  "url": "",
  "publisher": "",
  "published_at": "",
  "retrieved_at": "",
  "source_type": "official|research|media|database|company|other",
  "reliability": "high|medium|low",
  "claim_summary": "",
  "used_for": []
}
```

## Numeric Claim Map

Create a Numeric Claim Map for important values. `claim_id` is internal only;
the report citation format is still `[n]`.

Each item should have this shape:

```json
{
  "claim_id": "C1",
  "claim_text": "",
  "value": "",
  "unit": "",
  "metric": "",
  "entity": "",
  "time_scope": "",
  "region_scope": "",
  "methodology_or_scope": "",
  "source_ids": [1],
  "citation": "[1]",
  "confidence": "high|medium|low",
  "conflicts_with": []
}
```

## Output Contract

Return one JSON object only. Put the compact Research Summary, Evidence Ledger,
Numeric Claim Map, and seed evidence inventory in `artifact_content`.

Required metadata:

```json
{
  "completion_status": "complete|partial|blocked",
  "queries": [],
  "explicit_dimensions": [],
  "stable_candidate_set": [],
  "source_refs": ["[1]", "[2]"],
  "evidence_ledger": [],
  "numeric_claim_map": [],
  "evidence_gaps": []
}
```
