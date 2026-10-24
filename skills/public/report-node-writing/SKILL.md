---
name: report-node-writing
description: Use inside the StackPlanner report Writer to turn one Outline node specification and its saved evidence into the node's Markdown body at the host-assigned draft path, or to return node_feedback without writing a completion draft.
---

# Report Node Writing

## Boundary

Write exactly one assigned node. The Outline owns the plan and evidence
judgement; the host owns the draft path and acceptance. Do not search, delegate,
re-plan the whole report, or write any other output file.

## Before Writing

1. Read the node spec, evidence paths/line ranges, adjacent context, and
   language/style requirements from `context_refs.report_packet`.
2. Read the cited material with `read_file(description=..., path=...,
   start_line=..., end_line=..., line_numbers=true)`; `description` is required
   and line numbers are opt-in. Use only supplied evidence.
3. Keep the node's question, argument, conclusion boundary, constraints, and
   requirement ids intact. Do not add a new section or silently broaden scope.

## Draft

Write the body to the exact `draft_path` with `write_file`. Write body only:
the Outline owns the node title and the host renders that heading exactly once,
so do not repeat the node title or add a title line. Use clean UTF-8 Markdown;
keep canonical `[S<n>]` citation markers where source ids are supplied. Do not
use numeric-only aliases such as `[1]`, do not add a `citation_map`, and do not
include an outer code fence, process narration, or a JSON envelope inside the
draft. Do not add a generic limitations/disclaimer paragraph or a standalone
"lack of evidence" section; include only caveats explicitly required by the
node constraints or the user.

Return `artifact_content=null` when the file was written and include
`node_id`, `draft_path`, and `completion_status:"complete"` in
`artifact_metadata`.

## Feedback Instead of a Fake Completion

If evidence is missing, conflicts with the spec, or the plan cannot be
implemented without a decision, do not put "lack of evidence" or similar
placeholder paragraphs in the body and do not claim completion. Return no
completion draft; put `node_feedback` in `artifact_metadata`:

```json
[{"node_id":"n3","kind":"material_missing|spec_conflict|evidence_mismatch|other",
  "message":"...","material_ids":[],"requirement_ids":[],
  "suggested_action":"..."}]
```

Feedback is accepted separately from a draft. It does not resolve a gap or
authorize a rewrite; the Outline decides.
