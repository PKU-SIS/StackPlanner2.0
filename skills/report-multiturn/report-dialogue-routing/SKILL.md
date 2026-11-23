---
name: report-dialogue-routing
description: Route explicitly bound follow-up report requests after an accepted native delivery.
---
Keep Central in control. Decide within your existing turn; no extra classifier.

For an edit of existing report content, prefer
sp_delegate(target_agent="reporter", metadata={"report_task":"local_edit"}).
The program copies/resumes the active accepted version; no GitAgent or Outline
call is needed. Optional base_version_id selects an accepted historical version.
Give Writer the actual edit and what must remain; do not guess chapter IDs.
Writer finds the section and replaces the requested text. Follow a ready observation with ordinary sp_finish.
The program checks assembly, citations, length and hashes; no independent
Outline semantic review or factual verification is implied for this simple path.

For read-only questions, use sp_replan metadata.report_action="inspect_version"
with view="provenance" (the default): it returns the recently changed section,
actual source titles/URLs and short quotes at the accepted saved evidence lines.
For another section, use view="provenance:<node_id>" from its section_catalog.
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

If structure must change or new evidence is needed, use the existing native
Outline/research workflow. To prepare a complex follow-up, the optional legacy
perception profile metadata={"report_profile":"git_agent",
"report_operation":"patch"} can resolve requirements and prepare the copy;
after its handoff delegate Outline report_task="revise". GitAgent does not write
or dispatch. Do not use it merely to classify a simple edit or answer a question.

The selected accepted body plus latest edit defines current requirements; do not
reintroduce withdrawn requirements from old chat or task memory. Original chat
remains provenance. An ambiguous target can be clarified without a resolver loop.
If the turn is executing, continue its existing native work; if archived, return
its result without another fork/delegation. Diagnose failed/cancelled turns;
do not automatically restart paid work. Preparation and executor completion are
not delivery. Do not start a new report via report_length ahead of this decision.
