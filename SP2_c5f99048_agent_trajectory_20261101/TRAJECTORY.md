# SP2 agent trajectory: c5f99048-1b1c-4d16-a02e-885caf51e05e

Run: `a7728c0a-a8f9-4cad-b4c1-ba0013c1d952`  
Status: **success**  
Model: `deep-research-model`  
Created: `2026-10-31 12:41:05.683812` → `2026-10-31 12:52:46.510099`  
Tokens: `1277936` total (`1232765` input / `45171` output)  
LLM calls: `14`  
Report: tree v2, 4 materials, 9 drafts, status `delivered`  
Final report: `/mnt/user-data/outputs/report/r1/final/report-1.md` (7133 chars; source list S1–S4)

## What this package contains

- `events.jsonl` — sanitized raw event stream, all 298 events.
- `trajectory.json` — run metadata, counts, subagent index, and artifact pointers.
- `subagents.json` / `subagent_steps.jsonl` — grouped subagent calls and every AI/tool step.
- `llm_responses.jsonl` — Central model responses.
- `actions.jsonl` — SP action/handler events.
- `run_end.json` — final message-list event.
- `timeline.md` — event timeline without the noisy steps.
- `subagent_summary.json` — status/stop reason/usage per subagent.
- `artifacts/report/r1/` — request, ledger, tree v1/v2, 9 drafts, 4 materials, review, final JSON/Markdown, assembly.
- `manifest.json` — file hashes and source identity.

## Subagents

| task_id | type | status | stop_reason | tokens | steps |
|---|---|---|---|---:|---:|
| spact_call_1d0fcff3c7df4d5aa3eb73af | memory_recaller | completed | — | 5578 | 1 |
| call_7eaed66837394b78a57947a7 | researcher | completed | — | 55034 | 13 |
| call_7168985c51f0404dbfdefb75 | outline | completed | — | 208750 | 16 |
| call_97936b7f7aeb45478f8bd318 | researcher | completed | token_capped | 169340 | 16 |
| call_2b9e6c93c3074e4aa1af5c98 | researcher | completed | — | 105907 | 11 |
| call_bcc66f48d1ab41acb99be482 | outline | completed | — | 173679 | 9 |
| call_04b308272e1043dc93e3976d | reporter | completed | — | 18768 | 6 |
| call_8e62461a2f43401393136a47 | reporter | completed | — | 25539 | 8 |
| call_5445e6a38c104163846a515a | reporter | completed | — | 18912 | 6 |
| call_4ed615e8ae314d2ba25c8f07 | reporter | completed | — | 22153 | 8 |
| call_07796ccc436446c8ae015d6b | reporter | completed | — | 24635 | 7 |
| call_2403c41b807b47309a152ca6 | reporter | completed | — | 22819 | 8 |
| call_a05424c6bf574631bf05bccf | reporter | completed | — | 26931 | 7 |
| call_f68f1c51bdba401b82e169d5 | reporter | completed | — | 25689 | 8 |
| call_a163f3120579411b940df474 | reporter | completed | — | 21518 | 7 |
| sp-fixed-c0-review-r1 | outline | completed | — | 80942 | 3 |

## Trajectory shape

1. memory recall
2. pre-research
3. initial Outline v1
4. g1/g2 research
5. Outline v2
6. nine sequential reporter writes
7. integration review + final delivery

One researcher (`call_97936b7f7aeb45478f8bd318`) completed with `stop_reason=token_capped`; the final run still delivered. The review contains two advisory findings (one horizontal/vertical wording inversion and one mild duplication); they are not hidden or converted into success. The final report has 4 cited materials; do not describe it as a 6-source report.

All absolute runtime paths and the user id were replaced with placeholders. Source text and URLs are retained for analysis.
