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

Use `report_register_research_request` for every evidence need that is not
already satisfied.

- `mode: "new"` allocates a stable gap id. Never invent one.
- `mode: "continue"` with the exact `continue_gap_id` refines an existing open
  or awaiting-user gap and preserves its round history. Do not create a second
  gap for the same unresolved question merely to reset rounds.
- `requirement_refs` must quote the original user input. `why_insufficient` must
  explain what was checked and what is still missing. `stop_condition` must say
  what evidence would settle the question.
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
    "stop_condition": "A first-party source with a URL and line range.",
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
- `requirements` express the user's request and what is genuinely necessary to
  complete it, not every research direction you add yourself. Background,
  mechanism, and comparison dimensions may expand when they support the core
  analysis. A missing fact that is required by the user or indispensable to a
  core conclusion keeps the existing research/two-round/user path; an auxiliary
  detail you added may be withdrawn through `not_needed` + `optional: true`
  with a reason. Withdrawing your own optional detail is not deleting a user
  requirement. Do not fabricate facts or add a large disclaimer.
- Central's pre-research examples, and your own examples such as a list of
  countries, are candidates for comparison, not automatically user
  requirements. Choose the representative set from the user's goal and saved
  evidence; put a country or example in `constraints` only when the user
  specified it or the comparison genuinely requires it. Missing one self-added
  example must not block the whole section or turn "按代表性国家" into "all four
  named countries".
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
- Evidence cites saved `material_id`s and locatable line ranges. Do not cite a
  source that is not in the catalog.
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
read that saved candidate first and correct the same tree; preserve valid nodes,
requirements, evidence locators and completed updates. Do not prefill or reuse
the current delivery path from the candidate, and do not let a dispatch message
redefine this schema or move evidence preparation to Writer.

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
one leaves that returned result unjudged and invalid. Do not re-list a gap
already closed in the ledger. The normal transitions are:

- `resolved`: the evidence satisfies the question and constraints; state the
  basis.
- `unresolved`: the evidence is insufficient; the host returns it to research
  until the allowed rounds, then Central asks the user. Use this existing path
  instead of fabricating `user_decisions` or `continue_research` to reach
  `awaiting_user`.
- `not_needed`: the question is withdrawn; when this changes a user
  requirement, cite the `user_input_id` that authorises it. Genuinely optional,
  self-added auxiliary questions may use `optional: true` with a reason; this is
  not deletion of a user requirement.

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
