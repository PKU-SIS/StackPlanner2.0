---
name: report-dialogue-routing
description: Route explicitly bound follow-up report requests after an accepted native delivery.
---
Keep Central in control. Decide within your existing turn; no extra classifier.
First-report total/chapter word budgets are historical in follow-ups. Do not
add them to Writer tasks or ask for compression to fit them. Honor length or
compression requested in the latest message, including an explicit report_length.

Choose structure before choosing Writer. A new independent chapter/section
AFTER an existing section (for example after the conclusion), or removal of an
entire chapter/section, changes the Outline tree. Delegate directly with
sp_delegate(target_agent="outline", metadata={"report_task":"revise"}). The host
prepares the independent copy from the active accepted version; no GitAgent is
required for this ordinary structural edit. Outline adds/removes the actual
nodes and keeps unaffected drafts, then the native pipeline writes only needed
nodes. Do not simulate an independent section by appending Markdown headings
inside the conclusion. A subheading explicitly requested WITHIN an existing
section remains a Writer edit. Use Researcher only for genuinely missing evidence.

For a local deletion/undo, keep the active version as the baseline and undo only
the named span. Do not select a version from before the addition: that can lose
other edits that must remain. Historical resolved_query.mode/base_version_id
are provenance of the previous operation, not routing or baseline instructions.
The host honors current report_dialogue.base_version_id or a leading user
base_version_id=... directive. Natural-language old-version selection or complex
reconciliation can use the existing version-management workflow below.

For an edit of existing report content, prefer
sp_delegate(target_agent="reporter", metadata={"report_task":"local_edit"}).
The program copies/resumes the active accepted version; no GitAgent or Outline
call is needed. Do not carry an old base_version_id in delegation metadata;
the host defaults this new edit to the active version.
Give Writer the actual edit and what must remain; do not guess chapter IDs.
Readable current-message attachments are saved into native materials when the
copy is prepared. Existing-section edits using those files can also use Writer;
do not send a Coder just to read an upload.
Expansion, citation additions from saved material, compression and reorganization
within existing sections also use this Writer path. Outline is for changes to
the chapter tree, not merely different headings within a section.
Writer finds the section and replaces the requested text. Follow a ready observation with ordinary sp_finish.
The program checks assembly, citations, length and hashes; no independent
Outline semantic review or factual verification is implied for this simple path.

For read-only requests to quote a section, sentence or table cell, read the report
text directly with sp_replan metadata={"report_action":"inspect_version",
"view":"sections"} to locate the node ID, then view="draft:<node_id>".
Use start_line/end_line for a short window if needed. view="report" reads the
assembled report; it is usually unnecessary for one cell. These body views return
report text, not source evidence. Skip the catalog if the node ID is known; an
unchanged body already read in this conversation need not be read again. Quote
saved body text, not the resolved requirement alone. Central performs these reads itself; no
perception/read_file delegation, GitAgent or Writer is needed to copy saved text.

For an inventory of several known citations, read only the needed body window
(or use citations already visible in this unchanged version), then call sp_replan
metadata={"report_action":"inspect_version", "view":"sources",
"source_ids":["S12","S34"]}. This returns titles/URLs for up to 32 requested IDs;
missing and unreturned IDs are explicit. Use another batch only if needed.
Do not read the full catalog or delegate Coder to parse an offloaded tool result.
Put the actual source-to-claim mapping in the visible answer (a compact table is
appropriate), distinguish report synthesis/advice and unverified details, and
archive that answer. For read-only work, sp_finish.summary becomes the visible
final answer: put the actual answer there, and in reuse_version.response when
used. A short status saying that a list was produced is not providing the list.

For questions about evidence or source support, use sp_replan
metadata.report_action="inspect_version" with view="provenance" (the default):
it returns the recently changed section,
actual source titles/URLs and short quotes at the accepted saved evidence lines.
For another section, use view="provenance:<node_id>" from its section_catalog.
saved_line_range/saved_total_lines describe the entire locally saved file;
returned_line_ranges describe only this reply's excerpts. If 21–23 of 147 lines
are shown, the other lines are saved but not inspected. Say "not checked in this
excerpt", never "not saved" or "does not exist" from an omitted excerpt. Local
saved text is not necessarily the full original web source.
Separate quoted source claims, report synthesis, and recommendations. Answer
concisely once those distinctions are supported; say when evidence is missing.
Do not expand a local provenance question into an audit of every chapter or the
full source catalog. A suggested action need not have been prescribed by a source.
If a specific quote is incomplete, use view="material:<id>" with start_line and
end_line, only for that missing detail. Truncation must be disclosed.
No GitAgent/Coder/Researcher/Writer is needed for these program reads.
Archive via sp_replan metadata={"report_action":"reuse_version",
"mode":"read_only", "response":"<answer>"}, then sp_finish. A direct answer
also gets a response-only archive. Read-only never changes active version.

For unchanged whole-version restoration use sp_replan with
metadata={"report_action":"reuse_version", "mode":"exact_reuse",
"version_id":"<accepted version>"}, then sp_finish. No fork, Researcher or
Writer runs; active_version switches and accepted head stays unchanged.
Restoring only part of an old version while retaining other edits is local_edit,
not whole-version reuse. Writer can read historical sections for selective undo.

For structural edits use direct Outline revise as above. If new evidence is
needed, Outline registers native research gaps for Researcher. To prepare a complex follow-up, the optional legacy
perception profile metadata={"report_profile":"git_agent",
"report_operation":"patch"} can resolve requirements and prepare the copy;
after its handoff delegate Outline report_task="revise". GitAgent does not write
or dispatch. Do not use it merely to classify a simple edit or answer a question.

The selected accepted body plus latest edit defines current requirements; do not
reintroduce withdrawn requirements from old chat or task memory. Original chat
remains provenance. An ambiguous target can be clarified without a resolver loop.
If the turn is executing, continue its existing native work; if archived, return
its result without another fork/delegation. Failed/cancelled turns are ended;
do not re-delegate the same message. The program returns its failure notice.
A new user message can continue normally. Do not automatically restart paid work.
Preparation and executor completion are not delivery. Do not start a new report via report_length ahead of this decision.
