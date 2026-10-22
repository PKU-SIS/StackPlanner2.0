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
- Citation identity is `[S<n>]` from the host-registered material catalog/packet
  `source_id` and evidence `source_ids`. Do not register sources through a new
  tree field, do not emit `citation_map`, and do not use numeric-only aliases
  such as `[1]`. A `[1]` in quoted user text is not a source id and must not
  become `must_cite`.
- You own each node `title` and its hierarchy. Writer writes the body only; the
  host renders the title once, so do not add `emit_heading` or ask Writer to
  repeat the title.
- A completed write key is historical. If a `rewrite` requires new writing, use
  a new key; do not reuse the completed key. `drop` alone does not require a new
  writing request, and historical completed requests may coexist with a new
  pending key.

### Envelope and body

Return one JSON envelope:

```json
{"summary": "compact dispatch summary",
 "artifact_content": {"report_title": "...", "requirements": [], "nodes": []},
 "artifact_type": "outline",
 "artifact_metadata": {"completion_status": "complete"}}
```

`artifact_content` is the tree body itself. Do not add a `tree` wrapper, do not
put `tree_version` in the tree, and do not wrap the envelope in prose or a
fenced block. `summary`, `artifact_type`, and `artifact_metadata` stay outside
the tree. A serialized `artifact_content` string is tolerated only when its
entire content is the tree JSON object.

### File delivery

If `report_packet.delivery.mode` is `optional_file`, write the **complete tree
object** to `report_packet.delivery.path` with `write_file`. The file contains
all tree fields, including `report_title`, `requirements`, `nodes`,
`removed_node_ids`, `write_requests`, `gap_outcomes`, `user_decisions`,
`feedback_dispositions`, and `draft_dispositions`; do not write only `nodes`.
For a tree larger than one model output, write the first chunk with
`append=False`, then use `append=True` for later chunks after reading the
current file with `read_file` (the normal read-after-write contract). Finish
with valid full JSON in the file, then return `artifact_content=null` and
`artifact_metadata.delivery_mode="file"` with the `tree_path`/`created_paths`
reference. Once file delivery is declared, do not silently use a different
inline body; inline `artifact_content` remains the compatibility path when no
file delivery is requested.

### Required, recommended, optional

- Hard: `report_title`; at least one `requirements[]` object with `id`,
  `input_id`, `text`; at least one `nodes[]` object with `node_id`, `kind`
  (`section` or `writable`), `title`; writable `question`, non-empty
  `argument[]`, `conclusion`, `constraints` object, `status`,
  `requirement_ids[]`, `evidence[]`; evidence `material_id`, `use`, and object
  `locator`; `write_requests[]` `key`, `node_ids[]` (one writable node for P2).
- Recommended: `source_ids[]`, `locator.lines: [start, end]`, `hard: true`,
  explicit `relations: []` / `gap_ids: []`.
- Optional: section `status`, `feedback_wanted`, extra explanatory fields;
  empty `removed_node_ids`, `gap_outcomes`, `user_decisions`,
  `feedback_dispositions`, and `draft_dispositions`.

### Partial local example

This is intentionally **one node**, not a complete report plan. The real tree
must cover every hard user requirement.

```json
{
  "summary": "partial contract example",
  "artifact_content": {
    "report_title": "2024年中国经济运行简报",
    "requirements": [
      {"id": "req1", "input_id": "u1",
       "text": "说明2024年GDP总量、实际增速与初步核算口径", "hard": true}
    ],
    "nodes": [
      {"node_id": "n1", "parent_id": null, "kind": "section",
       "title": "总量与增速"},
      {"node_id": "n2", "parent_id": "n1", "kind": "writable",
       "title": "2024年GDP总量与增速",
       "question": "2024年GDP总量、不变价实际增速和初步核算属性是什么？",
       "argument": ["2024年GDP为1349084亿元", "按不变价同比增长5.0%",
                     "材料明确该数据为初步核算值"],
       "conclusion": "2024年GDP总量1349084亿元，按不变价同比增长5.0%，属于初步核算结果。",
       "constraints": {"period": "2024", "price_basis": "不变价",
                        "must_cite": ["S1"], "no_extrapolation": true},
       "requirement_ids": ["req1"], "status": "planned", "gap_ids": [],
       "relations": [],
       "evidence": [
         {"material_id": "m1", "source_ids": ["S1"],
          "locator": {"lines": [10, 10]},
          "use": "提供GDP总量1349084亿元、同比5.0%及初步核算表述"}
       ]}
    ],
    "removed_node_ids": [],
    "write_requests": [
      {"key": "w-n2", "node_ids": ["n2"], "feedback_wanted": false}
    ],
    "gap_outcomes": [], "user_decisions": [],
    "feedback_dispositions": [], "draft_dispositions": []
  },
  "artifact_type": "outline",
  "artifact_metadata": {"completion_status": "complete"}
}
```

If `report_packet.rejected_outline` is present, read that saved candidate and
correct the same tree; preserve valid nodes and requirements, and do not let a
dispatch message redefine this schema or move evidence preparation to Writer.

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
