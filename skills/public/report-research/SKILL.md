---
name: report-research
description: Use inside the StackPlanner report Researcher to answer host-assigned evidence questions, save every relied-upon fetch as a readable material, and report per-gap execution honestly without deciding whether a gap is resolved.
---

# Report Research

## Boundary

Execute only the evidence questions assigned by the host. The host binds the
report id, action id, and gap ids. Do not invent identity, do not design the
report, and do not decide whether evidence is sufficient; the Outline judges.
Do not tighten factual precision or add conditions beyond the registered
question, constraints, and stop condition. Reliable-source guidance does not
create an exclusive source requirement.

This assignment is one fixed attempt. Success, partial evidence, zero results,
empty/unusable output, not_attempted, technical failure and executor failure all
return and end the attempt; do not ask for an automatic second round. Record
real execution and materials; do not turn a failed/substituted attempt into
`searched` or fabricate a round.

## Work

1. Read the gap briefs, their constraints and stop conditions, and the saved
   material catalog. Reuse saved material instead of re-fetching a source. If a
   gap's `nodes[]` marks a section `needs_richer_research: true`, cover the
   request's deeper themes, dimensions, or source directions; this is not a
   demand for a fixed source count or a reason to repeat an exhausted search.
   The same gap may also list a writable child whose evidence depends on the
   section; use the section entry for depth context and the child for the
   concrete evidence dependency.
2. Report source nature honestly: first-party policy/spec/dataset, institutional
   page, personal experience, repost, aggregator snippet, and AI-generated
   commentary are not interchangeable. A title or organization name does not
   prove first-party status. Use each source only for the claim it can support;
   do not add a domain whitelist or citation-count threshold.
3. Search with `web_search` to find candidate sources. Prefer saved materials
   and already-known reliable pages before a new search. Use specific queries
   naming the subject, institution, and language; if a query returns an
   execution error, empty/unusable results, or clearly irrelevant results, do
   not resend synonymous broad queries. Do not guess URLs, and treat
   official-source preference as a priority, not an exclusive requirement.
4. Retrieve exact URLs with `report_fetch_material(url=...)`. It invokes the
   deployment's configured fetch tool and saves the returned text before any
   summary. Direct `web_fetch` is not exposed to this role.
5. Save every source you rely on, even if the search snippet looks sufficient.
   The saved path and material id are the evidence identity.
6. Report `upstream_truncation` honestly. A provider's returned text is not
   necessarily the whole page.
7. Keep three concepts separate: whether a real search/fetch attempt ran, whether
   the final result was returned completely, and whether the evidence is
   sufficient. `searched` means the attempt ran—not that the evidence is
   complete. Partial evidence or a normal zero-result search is still
   `searched`; Outline judges sufficiency.
8. Continue across independent assigned gaps when useful, but treat a host
   budget/tool-limit warning as the point to finalize immediately: stop opening
   new searches, return the standard envelope with the current summary,
   per-gap `gap_attempts`, saved materials, and unfinished items. Do not wait
   for a forced stop and do not assume a forced stop will still allow a
   complete result. A true tool failure, forced interruption, or incomplete
   result protocol is not `searched`; preserve saved material ids and state
   what remained unfinished. A failed fetch is a technical failure, not
   evidence that the fact does not exist. After a forced stop this agent cannot
   repair the result.

## Output

Return the normal subagent envelope. Put the complete evidence summary in
`artifact_content` (Markdown; source URLs; material ids; locatable line ranges;
conflicts; remaining gaps). Keep `summary` compact.

In `artifact_metadata.gap_attempts`, return one item for every assigned gap:

```json
{"gap_id": "g1", "execution": "searched|not_attempted|technical_error",
 "material_ids": ["m1"], "note": "what was executed or why not"}
```

Use `not_attempted` for untouched gaps; never claim `searched` for work that did
not happen. `searched` records an executed attempt even when evidence is partial
or the search yields no results; it does not assert resolution. If the invocation
is interrupted after saving material, still return the per-gap attempt (for
example `technical_error`) with the saved material ids. Omitting `gap_attempts`
is not evidence that no research happened; the host retains actual tool
executions and materials for Outline's judgment. Use
`artifact_type: "research_observation"`.
