---
name: report-node-writing
description: Use inside the StackPlanner report Writer to write one node's supported Markdown body at the assigned draft path, omitting unsupported optional details and completing the writing request without a missing-evidence feedback loop.
---

# Report Node Writing

## Boundary

Write exactly one assigned node. The Outline supplies the plan; the host owns
the draft path and acceptance. Do not search, delegate,
re-plan the whole report, or write any other output file.

## Before Writing

1. Read the node spec, evidence paths/line ranges, adjacent context, and
   language/style requirements from `context_refs.report_packet`.
2. Read the cited material with `read_file(description=..., path=...,
   start_line=..., end_line=..., line_numbers=true)`; `description` is required
   and line numbers are opt-in. Use only supplied evidence.
3. Keep the node's core question, supported argument/conclusion boundary and
   requirement ids. Prioritize the user's original task over optional details
   added by the outline. Do not add a new section or broaden scope.

The node specification is guidance, not evidence. Dates, numbers, quotations
and conflicts in the plan still need support from assigned material text. For
an outline-added detail, even if a node constraint calls it required, omit the
unsupported claim when that support is absent. Do not repeat the missing detail
or invent an unassigned source; finish the supported body and return complete.

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

## Finish the Supported Body

Omit unsupported optional dates, numbers, examples or extra comparisons and
write the content supported by supplied evidence. Never invent a replacement
fact or citation. Missing evidence for an optional detail must not turn the
whole node into partial/blocked or trigger research, a user question or an
Outline review. Do not explain omissions in the prose or add "lack of evidence"
paragraphs. Save the usable body and return completion_status="complete";
node_feedback and an evidence-gap checklist are not required for that body.

Only if no substantive body can be written, return no completion draft and
use the existing `node_feedback` in `artifact_metadata`:

```json
[{"node_id":"n3","kind":"material_missing|spec_conflict|evidence_mismatch|other",
  "message":"...","material_ids":[],"requirement_ids":[],
  "suggested_action":"..."}]
```

The feedback-only fallback is not a completed draft. Do not use it merely
because a supported chapter omits an optional detail.
