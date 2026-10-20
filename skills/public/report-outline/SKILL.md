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
- P1 does not write nodes: leave `write_requests` empty.

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
