---
name: report-dialogue-routing
description: Route explicitly bound follow-up report requests after an accepted native delivery.
---
Keep Central in control. For a revision or restoration, delegate perception with
metadata.report_profile="git_agent" using sp_delegate. In the SAME action set
metadata.report_operation to one of:
- "exact_reuse": the user wants an accepted whole version unchanged and discards
  later changes. Differences from the current head are not edits to that base.
- "patch": the user wants content/requirements different from the chosen base,
  including using an old base while retaining selected later changes.
- "read_only": discussion without changing the report or active version.
This is the existing Central decision, not an external router or another call.
Runtime binds the declared operation to GitAgent's tools. GitAgent resolves the
complete requirement and selects history; it neither writes nor dispatches.
For a read-only question, set report_operation="read_only" and delegate the same profile to inspect the snapshot and
archive the answer with report_reuse_version(mode="read_only", response=...).
ExactReuse must call report_reuse_version(mode="exact_reuse"). It archives the
selected version, changes only active_version, and does not prepare a copy,
call Outline/Researcher/Writer, create a native delivery or advance accepted head.
After a prepared handoff, delegate Outline with report_task="revise", then use
the native research gaps, write requests, assembly/review and Finish. Preparation
and model completion are not native delivery or proof of factual correctness.
The current resolved_query is authoritative for execution; do not restore
withdrawn requirements from chat or task memory. The UI retains original chat.
If the current turn is already executing, continue its existing native work;
if accepted/reused/read_only, answer from the archive without re-dispatching.
Failed/cancelled/preparing turns must be diagnosed, not automatically retried.
Do not use report_length to start a new report ahead of this follow-up decision.
