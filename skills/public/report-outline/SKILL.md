---
name: report-outline
description: Use inside the StackPlanner report Outline to read saved materials, plan an evidence-aware report tree, register research requests with stable gap identities, and judge returned evidence or user decisions without writing report prose.
---

# Report Outline

## Boundary

The Outline owns report structure, argument, evidence selection, research
requests, and content judgement. Central dispatches work; Researcher retrieves;
Writer turns accepted nodes into prose (P2). The Outline never fetches the web,
never delegates, and never invents evidence.

## Read Before Planning

1. Read the request and every user input in the packet, including corrections.
2. Read the saved-material catalog. Open the material paths whose exact wording,
   period, scope, or numbers the current decision depends on.
3. Read every research summary. A summary is a pointer, not a substitute for a
   material when the judgement depends on exact content.
4. Do not register a new research request until saved materials have been
   checked; state what was checked in `materials_checked`.

## Register Research Requests

Use `report_register_research_request` for every evidence need that is not
already satisfied.

- `mode: "new"` allocates a stable gap id. Never invent one.
- `mode: "continue"` with the exact `continue_gap_id` refines an existing open
  or awaiting-user gap and preserves its round history. Do not create a second
  gap for the same unresolved question merely to reset rounds.
- `requirement_refs` must quote the original user input. `why_insufficient` must
  explain what was checked and what is still missing. `stop_condition` must say
  what evidence would settle the question.
- `node_ids` refers only to nodes in the accepted tree. The first plan may use
  an empty list.

## Tree Contract

Return the complete tree JSON in `artifact_content`:

- `report_title`, `requirements`, `nodes`, `removed_node_ids`,
  `write_requests`, `gap_outcomes`, `user_decisions`,
  `feedback_dispositions`, `draft_dispositions`.
- Writable nodes carry `question`, `argument`, `conclusion`, `evidence`,
  `constraints`, `requirement_ids`, `status`, `gap_ids`.
- Evidence cites saved `material_id`s and locatable line ranges. Do not cite a
  source that is not in the catalog.
- A first plan may contain `needs_evidence` nodes. Do not fabricate evidence or
  state a conclusion the saved material cannot support.
- Every previous node that is removed must be listed in `removed_node_ids`.
- P2/P3 can request trial writing with one writable node per `write_requests` key;
  choosing no trial is valid. P3 assembly still requires every writable node to
  have a current draft before integration review.

## User Inputs

A normal supplement such as "add a comparison table" or "reorder sections" is
applied through `user_decisions` with `action: "apply_to_plan"` and
`gap_ids: []`; the tree itself records the resulting changes. Do not invent a
gap for it. Gap-scoped actions (`continue_research`, `change_scope`,
`provide_material`, `no_decision`) require the relevant `gap_ids` and a reason.
`continue_research` requires a positive `additional_rounds`. All decisions must
reference an existing `input_id`.

## Judge Returned Results

Every research result arrives as `awaiting_outline`. On the next accepted
output, judge every such gap:

- `resolved`: the evidence satisfies the question and constraints; state the
  basis.
- `unresolved`: the evidence is insufficient; the host returns it to research
  until the allowed rounds, then Central asks the user.
- `not_needed`: the question is withdrawn; when this changes a user
  requirement, cite the `user_input_id` that authorises it. Genuinely optional
  items may use `optional: true`.

Never treat the arrival of a result, a summary, or a user answer as automatic
resolution. A user answer is input to your judgement, not a closure.

## Integration Review

When `report_task="integrate_review"`, do not plan or rewrite the tree. Read the
candidate, full tree/node intents, requirements, drafts, materials, sources and
citation map. Return the full review JSON in `artifact_content`, including the
packet's exact `inputs_digest` and `reviewed_candidate_sha256`.

You may propose only local `bridge|terminology|format|dedupe` edits with one
current draft, a unique non-empty anchor, a replacement, and a reason. Edits
must not change facts, conclusions, comparison scope, evidence, citation
identity, or heading structure. Use `outcome="edit"` and `status="issues"` for
local edits; the host applies them and a new review is required. Use
`outcome="revise"` for substantive changes; do not attach edits. Use
`outcome="deliver"` and `status="ready"` only when no blocking finding or edit
remains. Keep `artifact_metadata` to the artifact type, `report_task`, and
summary status; do not duplicate the review there.
