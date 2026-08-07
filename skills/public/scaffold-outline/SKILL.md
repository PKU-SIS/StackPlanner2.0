---
name: scaffold-outline
description: Use inside the StackPlanner outline specialist for substantial research/report/document tasks after SA-style pre-research. Builds a ScopeTree from user query, Research Summary, and seed evidence, with node instructions, Evidence Map, and lightweight AGM State.
---

# Scaffold Outline

## Boundary

Use this skill only when delegated for an SA-style scaffolded report workflow.
Your job is to create or revise a report-writing contract, not to write the
final report and not to search. Read the user request, Research Summary, seed
evidence, mandatory requirements, and any prior outline before planning.

Return `artifact_type: "outline"`.

## ScopeTree Requirements

Output a ScopeTree, not a generic outline. The ScopeTree is simultaneously:

- the report structure;
- the evidence routing plan;
- the writing contract for the reporter;
- the state anchor for later focused research or outline revision.

Each node must have:

- `title`;
- `instruction`, explaining what the section must answer and how it serves the
  original user request;
- `children`;
- for leaves, `doc_ids` / `[n]` source ids or evidence requirements.

Default shape:

- prefer 3-5 first-level sections;
- keep total leaves at or below 10 unless the user explicitly asks for a long
  report;
- preserve every explicit user dimension in a section, subsection, or
  instruction;
- define a stable candidate/object set for multi-object tasks;
- make leaf nodes directly writable as report sections.

For comparison, evaluation, recommendation, ranking, forecasting, or solution
selection tasks, the ScopeTree must include:

- candidate/object scope;
- dimension-by-dimension comparison;
- answer-oriented synthesis, recommendation, ranking, or conclusion.

Do not make these standalone main sections unless the user explicitly requests
them:

- evidence gaps;
- source limitations;
- methodology or validation plan;
- future research.

When evidence is weak, encode the limitation in the relevant node instruction or
evidence requirement.

## Evidence Map

Bind seed evidence to the most specific relevant node. Do not attach all
evidence globally. For each leaf, record either:

- evidence refs/doc IDs that support the leaf; or
- concrete evidence requirements if support is missing.

Prefer stable `[n]` source ids from the Evidence Ledger plus artifact refs from
the supplied evidence. If the source is partial, mark it as partial rather than
treating it as complete. Do not invent source ids and do not renumber sources.

## Lightweight AGM State

V1 does not run the full ScaffoldAgent multi-round AGM action loop. Still,
produce a lightweight AGM State in artifact metadata so later focused research,
outline revision, and quality checks can continue from the same state.

Track:

- `active_nodes`: nodes most important for answering the user request;
- `evidence_gaps`: missing or weak evidence by node;
- `pending_queries`: focused searches needed to fill gaps;
- `expansion_targets`: nodes that are under-specified, too broad, too narrow, or
  conflict-heavy;
- `utility_signals`: compact notes about coverage, novelty, redundancy,
  citation support, and task completion risk.

## Output Contract

Return one JSON object only. Put the full ScopeTree JSON in `artifact_content`.
Do not return a generic Markdown outline. Do not return `artifact_content=null`.
If you cannot produce a valid ScopeTree JSON, set `completion_status` to
`partial` or `blocked` and list the concrete gap.

Your final response must be one raw JSON object only:

- do not include prose before or after the JSON object;
- do not wrap the JSON in a Markdown code fence;
- do not place the output-contract JSON inside another string;
- escape quotes inside JSON strings, including quoted report titles;
- put `evidence_map` and `agm_state` as top-level keys of
  `artifact_metadata`, not only inside `artifact_content` or a summary string.

If you accidentally cannot satisfy this raw-JSON contract, return a valid JSON
object with `completion_status="partial"` and a concrete `evidence_gaps` item
instead of returning prose.

Required metadata:

```json
{
  "completion_status": "complete|partial|blocked",
  "format": "scope_tree_json",
  "leaf_count": 0,
  "explicit_dimensions_covered": [],
  "stable_candidate_set": [],
  "evidence_map": {
    "leaf_node_id": ["[1]", "[2]"]
  },
  "evidence_gaps": [],
  "agm_state": {
    "active_nodes": [],
    "evidence_gaps": [],
    "pending_queries": [],
    "expansion_targets": [],
    "utility_signals": []
  }
}
```
