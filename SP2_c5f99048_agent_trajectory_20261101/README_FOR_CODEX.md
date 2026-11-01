# How to analyze this trajectory

Start with `TRAJECTORY.md`, then inspect:

1. `TRAJECTORY.md` for the case overview and subagent table.
2. `trajectory.json` for run metadata and a subagent index.
3. `subagent_summary.json` for status, stop reasons, token usage.
4. `subagent_steps.jsonl` for the full per-agent action/reasoning sequence.
5. `events.jsonl` for Central decisions, report handlers, and exact timing.
6. `artifacts/report/r1/` for the tree, drafts, materials, review, and final report.

The package is sanitized only for credentials/absolute runtime paths/user id; it does not remove source text, tool outputs, or advisory review findings. `token_capped` and review advisories are real events and should not be erased when summarizing.
