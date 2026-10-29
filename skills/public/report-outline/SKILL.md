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

An initial plan is a compact planning contract sufficient to direct the fixed
research round and later writing, not a first draft of the report prose. Keep
node arguments/conclusions to the detail needed for evidence and writing
decisions; do not add a hard node-count threshold, and do not delete a user
requirement merely to shorten the plan.

## Read Before Planning

1. Read the request and every user input in the packet, including corrections.
2. Read the saved-material catalog. Open the material paths whose exact wording,
   period, scope, or numbers the current decision depends on. For exact
   locations use `read_file(description=..., path=..., start_line=...,
   end_line=..., line_numbers=true)`; `description` is required and line
   numbers are opt-in so ordinary reads stay unchanged.
3. Read every research summary. A summary is a pointer, not a substitute for a
   material when the judgement depends on exact content.
4. Do not register a new research request until saved materials have been
   checked; state what was checked in `materials_checked`.

## Register User Materials

Use `report_save_user_material(input_id=...)` when the user's own input contains
evidence the plan needs. The host reads the saved original file; do not retype or
summarize it, invent a URL, or require a Researcher action. The returned
`material_id` and `source_id` are the identities to cite. A user input id is not
a material id.

## Register Research Requests

For a report that needs external evidence, the initial plan normally includes one
fixed supplementary-research target for each actual writing need. Do not wait
until you judge existing material insufficient; the default is to plan the
round. Related nodes may share one target or one Researcher task; do not create
one request per leaf and do not use a document-count threshold. Skip the round
only when the user explicitly restricts the report to supplied materials or
forbids web research. The initial plan is the normal registration phase; after
an accepted tree, add a target only when a new user input explicitly asks for
research, not as part of ordinary final consolidation.

Use `report_register_research_request` for every evidence need that is not
already satisfied.

- `mode: "new"` allocates a stable gap id. Never invent one.
- `mode: "continue"` with the exact `continue_gap_id` refines an existing open
  or awaiting-user gap only when a new user input explicitly authorizes more
  research; it preserves identity and round history. Do not use it to open a
  second automatic round during final consolidation.
- `requirement_refs` must quote the original user input. `why_insufficient` must
  explain what was checked and what is still missing. `stop_condition` must say
  what evidence would settle the question.
- Keep `question`, `constraints`, and `stop_condition` aligned: the question is
  the minimum question that must be answered; candidate directions belong in
  `constraints`/the question as candidates, not as additional required
  coverage; the stop condition settles that question and must not demand every
  candidate or be used to delete a user requirement. Prefer an already-located
  URL over a new search, reuse saved materials, and for long legal/policy texts
  summarize the relevant clauses/line ranges rather than pasting the whole
  text.
- A `mode:new` request names the user requirement it serves and whether it is
  user-required, necessary for the core argument, or self-added auxiliary
  scope. Self-added scope stays adjustable; do not encode a first-draft country
  list, source count, or named case as an irrevocable stop condition.
- If a prior Outline delivery was rejected, use its rejection reason when
  reviewing affected nodes and registered gaps; keep valid work and round
  history, and do not re-register merely to reset rounds.
- `node_ids` refers only to nodes in the accepted tree. The first plan, or a
  newly added section that is not accepted yet, registers with `node_ids: []`
  and describes the deeper requirement in the existing request fields. After
  the tool returns a gap id, write it into the corresponding section's
  `gap_ids` in the next tree; acceptance reuses `attach_nodes_to_gaps`, then
  research dispatch can proceed. Accepted sections can be passed directly by
  `node_ids`. Do not invent node or gap ids, and do not relax node validation.

### Complete research-request shape

Use the real nested objects, not strings or prose placeholders:

```json
{
  "questions": [{
    "mode": "new",
    "question": "What does the official specification say about X?",
    "constraints": {"must_be_primary": true, "need_locator": true},
    "why_insufficient": "Saved material m1 covers Y but not X at a locatable range.",
    "stop_condition": "The saved source answers the registered question at a locatable range, including the comparison this section needs.",
    "requirement_refs": [{"input_id": "u1", "quote": "original user text"}],
    "node_ids": ["n6", "n7"],
    "materials_checked": [{"material_id": "m1", "lines": [10, 32]}]
  }]
}
```

For `mode: "continue"`, include `continue_gap_id: "g1"` and keep the same
`requirement_refs`/`materials_checked` shapes. `constraints` is an object;
`requirement_refs` and `materials_checked` are arrays of objects. The same
shape is described in the `report_register_research_request` tool docstring.

When a marked section and one of its writable children both depend on the
research, associate both, for example:

```json
{"node_ids": ["n6", "n7"]}
```

Here `n6` is a section with `needs_richer_research=true`; `n7` is its writable
child that needs the evidence. `n6` carries the section-level depth signal;
`n7` records the concrete evidence dependency. Do not associate only the child
when the section itself is marked, or the Researcher loses the deeper-research
context. If the section is not yet accepted, register with `node_ids=[]`, then
write the returned gap id into the section's `gap_ids` in the next tree (and the
child's `gap_ids` when it also depends on that evidence). The host associates
nodes after acceptance; no parent auto-inheritance or separate research queue is
added.

Research `node_ids` may contain a section and its child together. This is
different from `write_requests`: each writing entry targets exactly one
writable node.

## Tree Contract

Return the complete tree JSON in `artifact_content`:

- `report_title`, `requirements`, `nodes`, `removed_node_ids`,
  `write_requests`, `gap_outcomes`, `user_decisions`,
  `feedback_dispositions`, `draft_dispositions`.
- `requirements` records the user's actual request and the conditions genuinely
  necessary to complete the core task, preserving the original input basis for
  each item. Self-added auxiliary scope (representative countries, classification
  axes, analysis dimensions, cases, source directions) may shape the outline
  nodes, argument design, or existing notes, but must not automatically enter the
  hard-requirement list. Do not add a classification field, change the default
  `hard` value, or promote your own execution choices into a user requirement or
  whole-report gate.
  A condition counts as necessary only when you can explain how it serves the
  user's goal or a core conclusion; saying it is necessary is not enough.
  Do not decide from a connector alone whether an item is required: "如/例如"
  and alternatives joined by "或" need visible context; explicit coverage
  verbs/quantifiers and the deliverable's purpose can make examples mandatory,
  while a bare illustrative list can leave room to choose a subset or axis.
  You choose representative countries, taxonomy axes, dimensions, cases, and
  necessary research targets from the user's purpose and available evidence;
  record a brief basis. A self-chosen country list is a scope choice, not a
  hard requirement: adjust it to the evidence while still meeting the user's
  actual representative-country requirement. A single supported country does
  not satisfy a multi-country comparison. A source count, named
  institution/case, statistical dimension, or country list in the first draft
  is a planning choice unless the user's wording makes it mandatory; adjust it
  when evidence/budget shows different coverage is sufficient. Preserve real coverage; do not shrink it
  or turn every illustrative example into a mandatory gap.
  Judge evidence sufficiency in context; do not substitute a fixed quota
  ("one each" vs "two each") for that judgement. Still-necessary research
  keeps a concrete question/stop condition; substitutable or non-essential
  self-added scope uses the existing `continue`, outline adjustment, or
  `not_needed` + `optional: true` path with a reason. Do not delete a real user
  requirement; withdrawing your own auxiliary detail is not deleting one.
- Judge source nature before using it: first-party policy/spec/dataset,
  institutional page, personal experience, repost, aggregator snippet, and
  AI-generated commentary are not interchangeable. A title or organization name
  does not prove first-party status; use each source only for the claim it can
  support. Do not add a domain whitelist or citation-count threshold.
- Section nodes carry `needs_richer_research` as a boolean. `true` means the
  section needs deeper, broader retrieval support; it is not an unresolved-gap
  or completion flag. Omit it or use `false` when ordinary retrieval is enough;
  old trees without it default to `false`. Judge from task complexity, not word
  or node counts. When true, register the section's research need with its
  `node_ids` and describe the deeper themes, dimensions, or source directions in
  the existing request fields; merge related sections when useful. Research
  completion does not automatically reset the flag. This flag does not expand
  the user's task or make every discovered issue a delivery prerequisite.
- Writable nodes carry `question`, `argument`, `conclusion`, `evidence`,
  `constraints`, `requirement_ids`, `status`, `gap_ids`.
- Writable `status` is exactly one of:
  - `planned`: planned but not evidence-ready; do not send it to Writer.
  - `needs_evidence`: evidence is still required; do not send it to Writer.
  - `needs_user`: a genuine user decision or user-supplied evidence is required;
    do not send it to Writer.
  - `ready`: the required evidence locators are in place and the node may enter
    the writing queue. If a node's claims depend on sources and `evidence[]` is
    empty, it is not `ready`; use `needs_evidence` or `needs_user` instead.
  Only `ready` nodes may appear in `write_requests`. Do not invent other status
  values and do not auto-map an unknown status to one of these.
- Evidence cites saved `material_id`s and locatable line ranges. Do not cite a
  source that is not in the catalog.
- After the fixed research round returns, judge from existing saved materials:
  no additional document does not by itself make prose impossible. Resolve a
  supported target, or close a self-added auxiliary target as
  `not_needed` + `optional: true`; do not fabricate `resolved` and do not turn
  every unmet supplementary target into `awaiting_user`. Only a hard user
  requirement genuinely unsupported by available evidence should ask for a user
  decision. Do not automatically start a second research round.
- In final consolidation, put the actually used material ids, source ids,
  locator lines and use strings into the corresponding writable node's
  `evidence[]`. A global material catalog alone is not Writer input; do not
  leave evidence preparation to Writer. When a gap has multiple necessary
  sub-requirements, judge them separately; support for one part does not make
  the whole gap resolved.
- A first plan may contain `needs_evidence` nodes. Do not fabricate evidence or
  state a conclusion the saved material cannot support.
- Every previous node that is removed must be listed in `removed_node_ids`.
- `write_requests` is the existing interface for formal report writing and
  optional trial writing, not a trial-only queue. Each entry must target exactly
  one writable node in `node_ids`; batch scheduling means multiple entries, each
  with its own `key`. For a normal report, arrange writing for every retained
  writable node that is currently `ready` and has no current valid draft. You
  may batch requests or deliberately trial a small batch; completing a batch
  does not mean the whole report is complete. Later,
  after Central returns with the queue exhausted, re-check the tree, existing
  requests and current drafts, then add missing ready nodes while reusing valid
  work. `feedback_wanted` requests local feedback only; it never gates other
  nodes, and you must not force feedback to repair scheduling. Do not rewrite a
  valid completed body without a content reason. Unready nodes still follow the
  saved-material, research, or user-input path; Writer is not an evidence
  discovery tool. The existing fixed acceptance still requires every writable
  node to have a current draft before delivery.
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
- Do not default to a standalone "资料局限", "证据不足", or "谨慎解读" section.
  Put necessary statistical scope/caveats beside the relevant data; use the
  existing gap or user-decision path when a hard requirement lacks evidence.
  When the user explicitly asks for a limitations/boundary section, plan it as
  a required node.
- Express emphasis, approximate length allocation, and ownership of repeated
  information through node scope, constraints, ordering, and retained detail;
  do not impose fixed prose proportions.

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

File delivery is preferred for every report Outline, including the first plan.
If `report_packet.delivery.mode` is `optional_file` or `required_file`, write
the **complete tree object** to `report_packet.delivery.path` with `write_file`.
When the mode is `required_file`, inline tree content is rejected even if it
looks complete. For a first plan or any tree larger than one response, use file
chunks rather than trying to fit the whole tree into one output. The file
contains all tree fields, including `report_title`, `requirements`, `nodes`,
`removed_node_ids`, `write_requests`, `gap_outcomes`, `user_decisions`,
`feedback_dispositions`, and `draft_dispositions`; do not write only `nodes`.
For a tree larger than one model output, write the first chunk with
`append=False`, then use `append=True` for later chunks after reading the
current file with `read_file` (the normal read-after-write contract). Never omit
an awaiting gap or a required field just to shorten the output. Finish with
valid full JSON in the file, then return `artifact_content=null` and
`artifact_metadata.delivery_mode="file"` with the `tree_path`/`created_paths`
reference. The delivery file contains only the tree object; never write
`summary`, `artifact_type`, `artifact_metadata`, or an `artifact_content`
wrapper into it. Do not rewrite an already-valid tree merely to restate it; a
read-back check is enough. Once file delivery is declared, do not silently use
a different inline body; inline `artifact_content` remains the compatibility
path when no file delivery is requested.

### Required, recommended, optional

- Hard: `report_title`; at least one `requirements[]` object with `id`,
  `input_id`, `text`; at least one `nodes[]` object with `node_id`, `kind`
  (`section` or `writable`), `title`; writable `question`, non-empty
  `argument[]`, `conclusion`, `constraints` object, `status`,
  `requirement_ids[]`, `evidence[]`; evidence `material_id`, `use`, and object
  `locator`; `write_requests[]` own `key`, exactly one writable node in
  `node_ids[]`.
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

If `report_packet.rejected_outline` is present on a `plan` or `revise` task,
follow `report_packet.delivery.instruction`. When the host has already copied the
candidate into the current delivery, read the relevant tail of that current
delivery first instead of requiring a full read of the old candidate. If only
the tail is missing, append the missing content; correct a mid-file error
normally. Preserve valid nodes, requirements, evidence locators and completed
updates; do not overwrite newer delivery progress, and leave the original
candidate unchanged. Only when the host did not prefill should the old candidate
be used as the recovery basis and written to the specified delivery path. The
final result must still be complete JSON passing tree, citation and business
validation; do not change this schema or move evidence preparation to Writer.

### gap_outcomes shape

When judging returned research, include complete objects, for example:

```json
"gap_outcomes": [{
  "gap_id": "g1",
  "outcome": "resolved",
  "reason": "The saved primary source states the needed fact at a locatable range.",
  "evidence_material_ids": ["m1"]
}]
```

`outcome` is one of `resolved`, `unresolved`, or `not_needed`; `reason` is a
non-empty string. `not_needed` needs `user_input_id` unless the item is
genuinely optional. Do not use bare strings or omit reasons.

`gap_outcomes` and `user_decisions` are actions for this output, not a replay of
the whole gap ledger. Keep the complete `nodes` (and other tree fields) so valid
planning and drafts survive, but include only gaps actually returned or decided
now; never repeat an already-closed gap. If there is no new user input, use
`"user_decisions":[]`. An intended question is not a user decision.

Short example with a complete two-node tree and only the current gap:

```json
{
  "report_title": "示例报告",
  "requirements": [
    {"id": "req1", "input_id": "u1", "text": "说明 A2A 与 MCP 的差异", "hard": true}
  ],
  "nodes": [
    {"node_id": "n1", "parent_id": null, "kind": "section",
     "title": "协议差异", "needs_richer_research": true},
    {"node_id": "n2", "parent_id": "n1", "kind": "writable",
     "title": "差异说明", "question": "差异是什么？", "argument": ["比较层级"],
     "conclusion": "MCP 负责代理接入工具与数据，A2A 负责代理之间的协作；两者层级互补。",
     "evidence": [{"material_id": "m6", "locator": {"lines": [1, 2]},
                   "use": "材料 m6 第 1–2 行给出层级与协作关系的可定位依据"}],
     "constraints": {"period": "2025"}, "requirement_ids": ["req1"],
     "relations": [], "gap_ids": ["g3"], "status": "ready"}
  ],
  "removed_node_ids": [],
  "write_requests": [{"key": "w-n2", "node_ids": ["n2"],
                      "feedback_wanted": false}],
  "gap_outcomes": [{"gap_id": "g3", "outcome": "resolved",
                    "reason": "材料 m6 给出了可定位依据",
                    "evidence_material_ids": ["m6"]}],
  "user_decisions": [],
  "feedback_dispositions": [],
  "draft_dispositions": []
}
```

Do not add an already-resolved gap to `gap_outcomes`. To withdraw a self-added
optional background question, use:

```json
{"gap_id": "g4", "outcome": "not_needed",
 "reason": "辅助背景；不是用户核心请求所必需", "optional": true}
```

## User Inputs

`user_decisions` records only decisions actually expressed by a new user input.
An intended question or a pending HITL request is not a decision; if the packet
has no new user decision, use `[]`. Do not invent `additional_rounds` or user
authorization to pass validation.

A normal supplement such as "add a comparison table" or "reorder sections" is
applied through `user_decisions` with `action: "apply_to_plan"` and
`gap_ids: []`; the tree itself records the resulting changes. Do not invent a
gap for it. Gap-scoped actions (`continue_research`, `change_scope`,
`provide_material`, `no_decision`) require the relevant `gap_ids` and a reason.
`continue_research` requires a positive `additional_rounds`. All decisions must
reference an existing `input_id`.

## Judge Returned Results

Research results arrive as `awaiting_outline`. On the next accepted output you
must give a disposition for **every gap currently awaiting_outline**; omitting
one leaves that returned result unjudged and invalid. If the output is close to
the model or host limit, write the tree to the delivery file in chunks; never
drop an awaiting gap to save space. Do not re-list a gap already closed in the
ledger. The normal transitions are:

- `resolved`: the evidence satisfies the question and constraints; state the
  basis.
- `unresolved`: use this only for a hard user requirement that remains genuinely
  unsupported by the saved materials. The fixed supplementary round has already
  ended; the host leaves it `awaiting_user` instead of starting another
  automatic round. Do not manufacture `continue_research` or a user decision to
  avoid that handoff.
- `not_needed`: the question is withdrawn; when this changes a user
  requirement, cite the `user_input_id` that authorises it. Genuinely optional,
  self-added auxiliary questions may use `optional: true` with a reason; this is
  not deletion of a user requirement.

Never treat the arrival of a result, a summary, or a user answer as automatic
resolution. A user answer is input to your judgement, not a closure.

A technical failure or empty result is still a returned fixed attempt, not an
instruction for another automatic round. Preserve saved material, do not claim
`searched`, a round, or a user decision that did not happen. If a hard
requirement remains unsupported, let the host hand the concrete gap to the
existing user-question path; if a tool/protocol block is persistent, use the
existing general HITL path without inventing a gap state.

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
