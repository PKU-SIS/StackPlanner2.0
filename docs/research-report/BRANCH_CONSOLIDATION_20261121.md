# Report branch consolidation — 2026-11-21

The report development branch is `sp2DeepResearch` (existing lowercase `s`). It inherits the unified DeepSeek 10000/20000/30000 runtime from `3769999b352978aaadce6780b8ed72231b642a51`, then adds the separately committed bounded empty-tree consolidation correction. No history rewrite is needed: the previous report tip `f8438738fb3c9cd30e3d746bb0c6e61a4e4acc74` is an ancestor.

Qwen v18 remains an independent branch at `7b45e9e84a10a76b9327a25cb8f0b1ab9b1b257c`. No Qwen code or frozen results are modified. The default repository branch `main`, DCC/DCCv2 and CollusionAudit stay unchanged. No service deployment is part of this Git operation.

## Preserved versions

| Archive tag | Original commit |
|---|---|
| `archive/report/zx-sp2research-20261029` | `b7988146d1a2f1fe2a5b83d396b45b1e1b8dd4b9` |
| `archive/report/handover-20261031` | `cf5d2ed73c270495bffb1a2c30bbea61cf6b3202` |
| `archive/report/sp2DeepResearch-before-threebands-20261121` | `f8438738fb3c9cd30e3d746bb0c6e61a4e4acc74` |
| `archive/report/qwen-v18-20261121` | `7b45e9e84a10a76b9327a25cb8f0b1ab9b1b257c` |
| `archive/report/deepseek-threebands-source-20261121` | `3769999b352978aaadce6780b8ed72231b642a51` |

After validation, redundant report branch refs `zx_sp2research`, `sp2-report-handover-20261031`, and `zmy/ds-threebands-unified-20261121` can be removed only while their expected SHA values still match these archives. Archive tags preserve the complete old commits; source histories are not squashed away. Qwen is retained. The old report branch name itself is retained and advanced, not deleted or recreated with different capitalization.

## Handover review

The eight handover-only commits were reviewed as a separate historical line. Its `fffb250`/`43f3a36`/`d05b384` staged partial-tree updates and recovery form an alternative protocol; the later report line uses complete structured submissions with `report_submit_outline`. They are not merged wholesale. Chunk-writing guidance (`a5ef0cd`) follows that historical delivery design. Candidate-at-a-time research guidance (`6a6f4b8`) predates the DeepSeek search/save and research delivery workflow; this consolidation does not change the selected retrieval policy. The two frontend commits (`ef51f38`, `109f685`) include failure-card filtering; existing report retry rendering and Writer visibility are retained. The archival documentation itself remains available through the handover tag.

## Scoped correction and validation

`27d6a55` ports the previously isolated-tested correction without replacing the three-band runtime: syntactically valid submissions with empty requirements/nodes get one additional submit-only corrective turn, with tool_choice required. Any second failure ends correction. Existing validation, budgets, cancellation, Researcher/Writer and Central recovery stay in place.

Report-related regression: 707 passed. Changed Python files pass Ruff lint/format checks; Git whitespace checks pass. Frozen `deliveries/` have no diff against the unified source.

Full native Central smoke passed on the integrated runtime: original low-code query case17, `--length-band 10000`, one sample and the native six-subagent window, no stage replay. All seven writable nodes delivered; 470.0 seconds native elapsed time (time.time wall-clock), 30 bibliography sources, final file SHA verified. Whole-document mixed-word-estimate-v1 count is 9,410 within 9,000–11,000; this is not a Microsoft Word count. The independent outer worker also records monotonic process elapsed time. No RACE was run, and 20k/30k were not newly generated. The old fourteen reports/RACE results remain historical. Publication and deletion are gated on this completed smoke and matching remote SHA/archive tags.
