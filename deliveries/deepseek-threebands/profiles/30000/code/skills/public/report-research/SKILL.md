---
name: report-research
description: Use inside the StackPlanner report Researcher for broad pre-research or one combined node-specific research assignment, saving relevant materials and handing Outline a detailed theme-organized synthesis with citations and honest per-gap execution.
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

### Broad Pre-research

When `context_refs.report_packet.pre_research` is true, explore the user's
subject across its relevant dimensions, mechanisms, perspectives, and source
types before Outline plans the report. Aim to collect and save 10-20 relevant
documents when the topic and available execution budget support that breadth.
This is a coverage goal, not a minimum or a reason to repeat failed searches.
Respect a supplied-material-only request and the actual task scope.

Finding one overview that appears to answer the question is not a stopping
condition. Follow useful documentation links and complementary sources; several
similar queries or pages repeating the same content do not establish breadth.
Prefer authoritative evidence for technical claims while retaining useful
independent analysis and implementation examples unless the user restricts
sources. Finish when the relevant themes are covered or the host budget requires
returning; state uncovered themes and acquisition failures without claiming
evidence sufficiency. Do not claim unread search hits as collected documents.

### Combined Supplementary Research

After Outline planning, one assignment may carry several gaps and writing
nodes. Read them together, merge overlapping searches, and collect a few useful
documents for the supplied directions. Reuse sources across nodes when relevant.
Return one research synthesis for the whole assignment, identifying the nodes
or gap ids each topic supports. Preserve each assigned gap's actual execution
status. A source-count shortfall alone does not justify another research round
or a user question; return the actual evidence and limitations in this attempt.

### Acquisition and Execution

With a content-bearing Search provider, request about 10 candidates per query.
The report search wrapper already saves usable bodies and returns source/material
ids, summaries and body excerpts. Use that supplied information directly in the
synthesis; do not fetch each result again or reread every saved page by default.
Use `report_fetch_material` for a relevant result whose body is missing or
insufficient, and `read_file` only when exact content is not already visible.
For saved evidence, prefer `report_read_material(material_id="m47", start_line=1,
end_line=40)`: the host resolves the real path, verifies its hash, and returns
the matching source ID and original numbered lines. Do not reconstruct or copy
hashed material paths; reserve `read_file` for requests, summaries and other files.
In supplementary research, prefer Fetch for known URLs and Search for genuinely
missing directions. Save remaining execution space for the complete synthesis;
after a budget warning, finish the evidence handoff before the hard cutoff.

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
4. Retrieve needed exact URLs with `report_fetch_material(url=...)` when their
   usable body was not already saved by Search. It invokes the deployment's
   configured fetch tool and saves the returned text before any summary.
   Direct `web_fetch` is not exposed to this role.
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

For **pre-research** (`report_packet.pre_research=true`), return the complete
theme-organized **Markdown directly** as the final answer. Do not put it inside
JSON, quotes, a code fence or an `artifact_content` field. The host saves the
complete body as `summary.md` and constructs metadata. This also applies when
finalizing after a budget warning. Include real canonical [S<n>] citations next
to supported information, material locators and source URLs; a material id m120
does not imply a source id S120. Do not invent identifiers or list fabricated
ranges of source ids. Use `read_file` for files; `grep` searches DIRECTORY paths
and requires `description`. Follow the exposed tool schemas.

For **supplementary research**, return the complete theme-organized research synthesis
as Markdown directly, without
a JSON envelope or `artifact_content` wrapper. The host saves it as `summary.md`. Write
connected explanatory paragraphs with canonical [S<n>] citations next to the
supported information. Explain what each relevant saved document contributes:
facts, mechanisms, examples, contrasting views, and relationships. Retain relevant
detail and source diversity instead of compressing everything into a short
answer or replacing the synthesis with a document catalog. Let length follow
useful information within the execution budget.

Preserve source URLs, material ids and locatable line ranges in the synthesis,
for example `... [S2] (m2, lines 12-28)`. Use only host-assigned source identities;
unfetched candidates must be labelled separately as unread leads, not cited as
verified evidence. State conflicts, acquisition failures and uncovered themes.
For a supplementary assignment, show how the information supports the assigned
nodes/gaps. End supplementary output with `## Gap execution` and a Markdown
table with exactly `gap_id | execution | material_ids` columns, one row per
assigned gap. Use execution `searched`, `not_attempted`, or `technical_error`,
and comma-separated saved material IDs, or `-` for none. The host reads this
small table and applies the existing execution checks; never invent an attempt.
Do not decide whether a gap is resolved.

For supplementary research only, in `artifact_metadata.gap_attempts`, return one item for every assigned gap:

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
