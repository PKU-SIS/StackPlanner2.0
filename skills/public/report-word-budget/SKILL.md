---
name: report-word-budget
description: Apply an explicit SP2 whole-report estimated Word budget inside a bound Outline or Writer delegation, preserving evidence and the normal acceptance lifecycle.
---

# Report Word Budget

Use this overlay only when `context_refs.report_packet.length` exists. The
ordinary report-outline/report-node-writing Skills still own evidence,
structure, paths, completion and source identity. No new research round or
execution budget is granted by this Skill.

## Common Counting Contract

Use the packet's versioned mixed-language word estimator, never raw characters,
CJK-only counts or tokens. Whole-report scope includes headings, tables, citation
labels and final references. This is an estimate; `word_verified=false` and
`measured_word_count=null` remain true until actual Word measurement is obtained
outside this tool flow. Do not claim a measured Word count from an estimate.

## Outline

When planning, consolidating or revising content with
`length.contract.outline_budget_selection=true`, the final whole-report
goal remains `length.contract.target_words`. Use the smaller
`length.body_budget_recommendation` for INTERNAL body generation. Start at its
default, then choose a total within its range using necessary nodes, total
arguments, distribution and compound tasks as references, never count quotas or
a fitted formula. More tasks do not automatically mean a larger budget. Remove
optional duplication; consider the lower half for tasks inviting excessive
expansion, the middle for focused required analysis, and upper half only for
necessary depth. These are heuristics. Allocate by chapter importance/workload,
not equally or by writing order. Sum native node targets to the chosen total;
sum node maxima within `body_max_words`. The initial `body_target_words` is a
default, not a fixed ceiling; after planning it reflects the selected sum.
No extra total field, model call, compression or truncation is needed. This
paragraph supersedes fixed-total allocation instructions below in this mode;
do not subtract heading/reference reserves from this internal range again.

Read `length.contract`, `body_min_words`, `body_target_words`, `body_max_words`
and the reference/heading reserves. Allocate positive integer `target_words`
and `max_words` to every writable node's `constraints`, target <= maximum. The
host checks target sums against the body range and maximum sums against the body
maximum. Source reserves are recalculated from selected source identities; do
not treat reserved space as actual written content when checking the minimum.

Plan substantive writable chapters before issuing write requests. For a
10,000-15,000-word report, about 6-10 writable chapters is a useful starting
point. This is guidance and the actual requirements determine the final count.
Plan the content of a short report from the original request. Reducing nodes
also reduces optional claims, examples, background and comparison dimensions;
do not move the entire long-report workload into a few larger nodes or recreate
all its subdivisions inside each chapter.

First preserve every explicit user requirement, then select the essential
arguments and representative evidence needed to answer it. Research material is
not a checklist of topics to include. As a starting point, aim for about 2-3
principal arguments per chapter and one short representative example when useful.
These are guidelines rather than hard caps: required countries, classifications
and distinctions must still be covered. Choose optional countries, theories,
cases and historical details only when they contribute something distinct.
Express the selected scope and optional-detail boundaries in the existing
question, argument, conclusion and constraints fields; no new schema is needed.
Divide the available body_target_words across the chapters. Bundle related
objects, countries, methods and comparison dimensions in one node, using
paragraphs, internal subheadings or tables inside its body. A comparison row,
source, metric or short subsection usually does not need its own Writer.
Grouping must select a shared argument and omit redundant optional detail,
rather than concatenate the complete arguments of every former node.

Make all required objects, actions and qualifications explicit in the merged
node's question, argument, requirement_ids and evidence. Split a chapter when
an independent argument or the user's structure requires it. Identify which
chapter develops each argument and develop it once. Section nodes can express
the navigation hierarchy. Preserve all requirements, evidence and limitations.

When `length.contract.paragraph_plan_required=true`, every writable node must
include `constraints.paragraph_plan` and `constraints.scope_exclusions`.
The plan is 3-6 ordered objects with `paragraph` (1,2,...), `purpose` (nonempty
text) and positive integer `target_words`; its target sum must be 80-100% of
the node target. Include space for the ending. Exclusions name optional details
not developed, preserving all explicit user requirements. Aim for 2-3 principal
judgements and one short case when useful; do not turn every fact or available
framework into compulsory content. The host validates structure and arithmetic,
not semantic adequacy. Revise both the selected content and the plan together.

For an overlong chapter, make explicit content choices in the normal revised
tree: which arguments stay developed, which become brief mentions, which optional
details are omitted, and which chapter owns repeated material. Adjust the actual
arguments and constraints while retaining necessary evidence. Changing only the
numeric budget and retaining the same workload is not a content-selection revision.

For budget-only updates use `report_patch_outline_budget` with the exact accepted
tree version/SHA from the packet. Each node update contains exactly `node_id`,
`target_words`, and `max_words`. Supply normal draft keep/rewrite dispositions,
writing requests, feedback/input dispositions where required. This stages a
complete proposal; return its normal file-delivery envelope, then the native
handler performs all business acceptance. Budget changes affect node
fingerprints. The tool cannot modify argument text, evidence, requirements,
source IDs, headings, research gaps or structure. Use complete tree submission
when actual content planning/evidence changes are needed.

## Writer

Read `length.node.target_words` and `max_words`; follow the assigned node's
existing evidence contract. If `length.generation` exists, its lower
`target_words` and `max_words` are the generation goals. They take precedence
over the allocated node target and prose length suggestions in the node.
For example, allocation 1000 and generation target 400 means draft toward 400;
do not expand back to 1000 or try to satisfy the whole-report minimum yourself.
The percentage is experimental guidance, not a predicted measured length or an
output-token cutoff. Whole-report acceptance remains unchanged. This mode omits
the model measurement tool and uses automatic host completion counting only.
Plan toward the applicable target before writing. Most visible
CJK glyphs count individually; do not assume a Chinese paragraph is counted like
one English word. Allocate space to the chapter's required claims, evidence and
qualifications before drafting. Generate the concise supported body directly.
Before writing, plan the full argument and reserve space for its answer or
conclusion and essential qualifications. Use concise, precise language and
integrate evidence into the argument. Fit the depth and optional detail to the
assigned maximum before generating; do not start an unrestricted long essay
and cut it off. The `<assigned-chapter-length>` system instruction repeats the
actual host target and maximum for this chapter. It is a content limit, not a
provider output-token cutoff.
When that block includes a paragraph plan, write one paragraph for each item,
fit its purpose and budget, omit the declared optional exclusions, and retain
the complete ending. Do not output the plan itself or a self-reported count.
The selected scope bounds development. Do not use the supporting evidence to
add topics or turn each argument into a complete background-example-risk-
recommendation sequence. A source or framework does not automatically need a
subsection. Keep the required analysis, evidence, distinctions and limitations.
State each required argument and its supporting evidence once. Related objects
share the chapter's introduction and conclusion.

Save the exact host-assigned file and return the normal Writer envelope
directly. The host records the saved file's actual estimate at completion.
When exposed, `report_measure_draft` is available for diagnostics when needed, with its existing
ceiling of two checks per attempt. Routine completion needs no measurement tool
call or model-authored word count. The whole-report acceptance gate still owns
any required length revision; it does not authorize intentionally ignoring the
chapter maximum.

Tables count as text. Never truncate a saved report, invent evidence, fill a
short report with generic background or relabel a content length issue as an API
failure. A genuine conflict between mandatory coverage and space, after trying
concise planning and removing optional detail, may use native node_feedback
kind=spec_conflict without a completed-draft claim. In this budgeted mode the
conflict can apply even when partial content is possible. Preserve essential
requirements and do not silently enlarge the budget. The host still retains
actual saved drafts and measures them rather than cutting or faking completion.

At completion the host records the bound saved file's estimate in the attempt
ledger without another model turn. Routine node-level compression and summary
rewrites are omitted. Global file-safety rules still apply to any modification
needed for factual or protocol correctness.

## Integration Review

Keep the normal evidence/coverage acceptance rules. The host independently checks
the whole final display against the explicit estimated range and can override a
ready review when it is out of range. It uses the existing single content
revision and rereview path; it does not grant indefinite retries. Actual Word
certification remains separate from estimate-based delivery.


## Optional submission check

When `length.contract.writer_submission_limit=true`, submit the complete chapter
with `write_file` and `append=false`. The host checks actual estimated length
before saving. If rejected, use the returned count and aim at the assigned target
for one complete concise resubmission. Preserve the argument, evidence, required
qualifications and ending. This is the explicit exception to the default
no-node-rewrite guidance. Two proposals total are allowed in the same native
Writer attempt. A rejected proposal is not a saved draft; never claim completion
without a successful write. Do not truncate, append fragments or request a larger
budget. The existing whole-report review still runs normally.


The optional `writer_submission_tolerance_percent` is a host saving tolerance
(0-5%, default zero). Keep aiming at the original target and maximum. It does
not enlarge the whole-report range or authorize extra content or resubmissions.


## Compact first-pass mode

When `length.contract.compact_outline=true`, the scoped compact role instructions
replace the broad length-planning guidance above. Initial and revised Outlines
normally aim for 6-8 chapters and 1-2 concise arguments each for the 10000/20000
bands. Only when `length.contract.target_words=30000`, use the role prompt's
long-report guidance instead: substantive writable nodes develop at least 4
distinct useful arguments, aim for 5, and normally at most 6; introductions,
brief methodological framing and conclusions may have fewer. Do not add
headings, synonymous arguments or unsupported scope merely to meet counts.
Explicit requirements and evidence limitations take precedence; keep all
selected arguments within the assigned budget. These are scope
suggestions, not hard validation limits. Explicit user requirements take
precedence; reduce optional topics, cases and duplicate discussion substantially.
Use normal evidence locators and requirement mappings. scope_exclusions is optional.
Group missing evidence into a small shared supplementary batch, normally 1-3
questions, allowing necessary support for all explicit requirements. Keep the
native fixed research/consolidation lifecycle; do not expand to every finding.

Writer follows the assigned chapter target and maximum, plans a complete ending,
and directly saves a concise supported first draft. Compact mode has no model
measurement tool, paragraph arithmetic requirement or local submission/rewrite
gate. Completion is counted locally. The original whole-report review and one
content revision remain. Local counts are estimates, never actual Word results.


### Reduce the tasks inside each judgement

Each judgement develops one central answer with essential support and an
implication. A compressed list of statistics, policies and standards remains
multiple tasks. Select the actual content in the question, argument, evidence
use and constraints together. An exclusion does not cancel conflicting expansion
instructions elsewhere in the node.
Unless explicitly required by the user, develop at most one framework or
evaluation approach per chapter and select only 2-3 relevant dimensions. Other
frameworks may be omitted or named briefly without full indicators, clauses,
methods or history. For unnamed-country comparisons, normally choose 2-3
representative countries; preserve every explicitly named country requirement.
Consolidation and revision remove optional tasks from the actual argument and
evidence-use text, alongside numeric budgets. Writer applies the same selection
and preserves the complete answer, necessary distinctions and limitations.
Research supports this selected answer rather than building a catalogue of all
standards. This is generation guidance, not a semantic guarantee or a new
per-chapter retry mechanism.


### Compact proposal serialization

Use report_submit_outline rather than write_file. When current drafts exist,
leave draft_dispositions empty and explicitly list all current node IDs in
keep_nodes, rewrite_nodes or drop_nodes with a decision_reason. The host supplies
the internal draft IDs and fixed action strings. Missing or conflicting decisions
are not guessed. The budget patch tool supports the same node-choice interface.

Compact review receives a single complete candidate body with draft identities,
paths and hashes. Only tree/citation-bound material previews are inlined; all
originals remain accessible through the complete catalog and material-read tool.
Read a specific original when a finding needs it. Keep the native review criteria;
the reduced inline context does not relax coverage or evidence checks.


### Positive Writer task cards

When length.contract.writer_task_cards=true, the host derives a small task card
from each native node's question, arguments, conclusion, required citations and
word budget. Do not generate writer_tasks, writer_framework or another evidence
selection schema. There are no task-card text-length, fact-count or excerpt-size
rejection gates. Evidence remains in the normal packet with saved-source locators;
raw passages are not duplicated in the card. Read relevant originals as needed.

Cover all original user requirements, then aggressively remove optional work and
write concisely within the assigned budget. Suggested task counts must never
remove required content. Native coverage/evidence review and immutable generation
basis remain. This is guidance, not a guarantee of first-draft length or savings.


### Requirement ownership and chapter depth

Compact Outline assigns each distinct required answer/comparison a primary chapter
using native requirement_ids. Broad requirements can span chapters with explicit,
nonduplicated subquestions. node.question states the chapter's answer boundary
and user-requested depth; argument lists only what to develop; conclusion gives
a brief takeaway. Refer to sibling chapters instead of repeating their profiles.
Required priorities receive detail; other required items receive concise answers;
optional background, extra cases and unrequested indicators are removed. Required
comparisons may use compact tables with cited evidence and short interpretation.
The host task card carries these existing fields. No added schema, count gate,
model stage or automatic length revision. Actual savings remain unverified.


### Approximately 30000-word Writer guidance

For this approximately 30000-word variant, develop every argument already selected in task_card.tasks with its essential reasoning, evidence and qualifications. Do not collapse distinct assigned arguments into a list of conclusions. Do not independently invent arguments to reach a count. Respect the assigned chapter budget and complete ending; no padding, truncation or length-only rewrite loop.
