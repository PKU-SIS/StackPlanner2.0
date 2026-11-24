# DeepSeek unified three-band runtime

The current user authorized consolidating the original approved DS bands. Use `length.contract_for_band` and `evaluate_report_full.py --length-band` for one runtime; band-specific role prompt helpers live in `sp/report/prompts.py` and are selected in `hooks.py`. Keep the native report entry, fixed supplement lifecycle, evidence checks and acceptance. See `../docs/research-report/DS_THREEBANDS.md`. Frozen historical copies under `deliveries/` must not change. Do not add post-generation length processing or conflate old scores with unified-code results.

Historical experiment notes:

用户明确授权四道全新题各一次1万档，case3/17/27/36；沿用上一轮RACE评分。原问题保持不变。最终9000/10000/11000，Outline自选内部正文预算3000–4000，默认3500。节点/论点仅作经验参考，非配额。保留入口修复、一次缺章兜底、原生需求/证据校验与必要内容修复；Writer模型不变，恢复归档1万档compact Writer提示，移除3万专用展开句。不得套用旧固定4000运行器overlay，不新增模型规划、事后压缩、summary、硬截断、长度返修或自动付费重启。逐题生成并评分，评分失败不重发未知响应、不阻断其他题；不按前题结果调参。全部日志、临时、缓存、检查点、结果在本数据盘独立目录local-storage/runs/race；不改只读原仓库或历史结果。Token含缓存输入按万展示，评分另计。不用子代理。

`InvalidToolCallRepairMiddleware` handles empty requirements/nodes only under the existing structured-consolidation opt-in. Preserve the accepted tree in context and permit one additional `report_submit_outline` call with `tool_choice="required"`; a failed result, missing call or other tool ends the correction through existing failure handling. Token/tool budgets, cancellation and forced stops suppress correction. Syntax/source-ID repair, initial planning, Researcher, Writer and Central recovery retain their existing behavior. Tests: `tests/test_invalid_tool_call_repair_middleware.py`.

## Opt-in report dialogue

Post-delivery multi-turn code lives in `packages/harness/deerflow/sp/report/turns/`.
Read `../docs/research-report/MULTITURN_IMPLEMENTATION.md` before changing it.
Eligibility requires latest-message `report_dialogue.enabled=true`, a bound native
report ID, authenticated runtime ownership, and a validated accepted delivery.
Never infer eligibility from `report_task=revise`; first-round consolidation uses it.
For existing-section edits, Central now prefers reporter `report_task=local_edit`:
`turns/local_edit.py` forks/activates in program code, exposes find/read/exact-span
replacement tools, and accepts plain Writer summaries. Central can inspect or
reuse accepted versions through existing `sp_replan` metadata.report_action
`inspect_version` / `reuse_version`, without GitAgent. Complex reconciliation can
still select the perception `git_agent` profile through `sp_delegate`.
Simple edits use an explicitly labelled programmatic review receipt
(`review_origin=programmatic_local_edit`, semantic/factual verification false),
then native Finish. Never present it as an Outline semantic review. The original
first-report path and all evidence/citation/length/file bindings remain intact.
See `../docs/research-report/WRITER_FIRST_20261123.md`.
Dialogue Skills live outside default public discovery in `skills/report-multiturn/` and
are read only for validated follow-ups. Version metadata is not a second chapter
ledger. Always fork before native resume; never resume an accepted historical
version in place. Publish only from a new native delivered record and matching
turn/run/head bindings. Preserve evidence, length, citation and file-hash checks.
Use the new checkout's explicit PYTHONPATH when borrowing an existing environment.
No paid experiment or deployment is implied by offline test success.

Sealed native report IDs stay immutable even when a later message omits dialogue
metadata: never allow legacy resume/close to mutate them. Validate the active
working-copy ID before report dispatch; bound turns cannot use an unscoped Coder.
The lazy origin requirement is marked `native_accepted` and includes accepted
requirements/HITL provenance; its initial request is not a fully resolved current
requirement. Frozen offline experiment fixtures and their schema/SHA validator
are described in `../docs/research-report/multiturn-evaluation-20261122/PLAN.md`.
They do not authorize or launch API experiments.

Continuous rehearsal lives in `tests/_report_multiturn_rehearsal.py` and
`tests/test_sp_report_multiturn_rehearsal.py`. It drives the production graph and
real executors with scripted models and network-denying fixture retrieval.
A resumed dialogue copy must assign `acceptance.turn_namespace` from its native
report ID and turn ID; native new cycles retain it. This prevents copied fixed
review/Finish IDs from replaying an older turn. Reports without the namespace
retain their original fixed action IDs and budgets. See
`../docs/research-report/MULTITURN_REHEARSAL_20261122.md` for artifacts and limits.

Pilot preparation uses `scripts/report_multiturn_preflight.py` and
`scripts/report_pilot_audit.py` from the repo root. The builder and verifier deny
network connections and have no execute/start option. They use only public
projections and dummy SDK serialization credentials, never copied `.env` values.
The observer is experiment-scoped; do not wire it into default first-run paths.
Reconcile actual HTTP inventory separately from callbacks before declaring cost
complete; include direct Search/Fetch and background memory model calls.
For readonly follow-ups, inspect tree/source/material snapshot views with bounded
line ranges; a direct Central answer is archived as response_only, not delivery.

The experiment-only `scripts/report_pilot_worker.py` now installs send-time
reservations, source-bound sync/async/SSE receipts, native callback observers and
background-memory drainage. `run_turn` wraps the existing caller graph and keeps
case-wide limits; it never selects agents or publishes reports. Only effective
HTTPX MockTransport mounts are admitted; the spawn supervisor denies sockets
until child exit, retains numeric journals and enforces outer deadlines/cancel.
No production imports or live API launcher are added. The provider input-context
ceiling remains unverified/null in preflight. See the pilot preparation
`WORKER_OFFLINE.md` for validation and the remaining real-API boundary.

The optional preflight `--admission-review --budget-cny 50` applies the frozen
public review in `ADMISSION_REVIEW.json`: a 50 CNY total ceiling, 48 CNY operating
guard, and a conservative 2^20 input reservation derived from the documented 1M
context. `OfflinePilotWorker.from_preflight` verifies and binds those limits; it
still rejects live transports. Never equate budget approval with execution
authorization. Decode compressed HTTP receipts independently from SDK decoder
state; sync/async gzip SSE regression lives in `test_sp_report_pilot_admission.py`.

The explicitly authorized pilot entry is `scripts/report_pilot_entry.py`. Its
non-start mode only checks packet and redacted credential metadata. Actual
execution requires `--start --billing-currency CNY`, a fresh isolated run path,
and the user's separate execution authorization. Do not re-ask for the already
approved 50 CNY budget. OfficialPilotWorker shares the offline accounting core,
but admits only direct verified-TLS official transports with zero pool retries,
no redirects, bound Authorization, and scoped official-IP sockets. The ordinary
worker remains mock-only. Use native make_sp_agent/checkpoints and native
acceptance; record failure without automatic paid restarts. Factory-only role
probes do not cover native Researcher continuation's 65536-token override;
accounting must reserve from each actual request. See the live-pilot record.

The pilot entry also supports `--seed-source` with a frozen `--public-case`
preflight. Import verifies a read-only native delivery, uses the existing full
copy/restore/hash rebinding, retains its transfer archive as provenance, and
freezes the verified restored native report as the thread's sole origin. It
skips model generation for user turn 1 and starts opt-in follow-ups at turn 2.
`--evaluator-checks` runs only after each graph turn; never pass its contents to
models. `--prior-usage` carries prior reconciled costs/tokens into the same total
budget. The real worker allows up to 180 seconds to drain background memory;
model and native role budgets remain unchanged. See seeded-lowcode-20261122.

Real seeded follow-up evidence is in seeded-lowcode-20261122/RESULT.md: turns
2–5 pass; turn 6 chose prepare/review instead of ExactReuse, emitted an identical
new delivery and advanced head incorrectly; turn 7 was not executed. Do not
relabel identical content or zero Writer calls as correct reuse. The reuse tool
itself was not the successful path in this run. Runtime message IDs must come
from actual HumanMessage objects, and the pilot builds a fresh native graph per
user run like Gateway while retaining the checkpoint. Successful paid turns
were validated/recovered without replay; the known mode-selection defect remains.

Follow-up Central delegates may now bind metadata.report_operation to patch,
exact_reuse or read_only. Runtime persists this per source message and narrows
GitAgent's available terminal tools; reuse mode and terminal handoff must match.
Legacy absent/auto retains prior tools. ExactReuse must not fork/activate native
resume or advance head. The two conditional dialogue/version Skills distinguish
whole-version restoration from ancestor-based partial editing; default Skills
and first-turn routing remain unchanged. The pilot's --prefix-source imports a
verified linear history prefix into a fresh owner/thread, retaining raw transfer
archives and rebasing native paths; never rewind or edit old experiment results.

Typed patch GitAgent profiles use 60 graph steps with the same nominal 8-tool
limit. Native perception's 40 steps otherwise clamp the actual loop guard to 5
proposed calls (6 reserved shutdown steps / 6 steps per tool cycle). The scoped
padding restores the intended bounded tool capacity; exact/read-only/legacy
profiles, shared executor, token limits and first-turn generation are unchanged.
Use a terminal-tool-specific loop warning so inspection cannot consume the
handoff allowance. Retain capped real trials as failures, never relabel them.

The same step-cap consistency fix now covers all explicit operation contracts
(patch/exact_reuse/read_only): 60 graph steps, unchanged 8-tool cap, with one
terminal call reserved by the scoped warning. Legacy auto remains unchanged.
A host-bound read_only tool defaults to read_only even when mode is omitted,
and rejects explicit mode changes. Failed budget trials remain archived.

Readonly version inspection supports evidence:<node_id>: a bound section plus
bounded saved reference excerpts, source/material IDs, SHA and character ranges.
It performs no network or writing; truncated/missing evidence must be disclosed.
This avoids spending the finite GitAgent call budget on repeated full catalogs.
The pilot official DNS guard refreshes only api.deepseek.com:443 public answers;
TCP still requires audited wire scope and TLS/credential binding. It does not
open arbitrary DNS/hosts. Prior uncertain requests remain stopped and reserved.
Only an explicit --carry-unknown-exposure on a fresh --prefix-source trial may
carry original journal-backed reservations; --resume-run cannot use it, unknown
requests are not replayed, and global_billing_complete stays false until holds
are reconciled. Never call held exposure zero or count it as an actual invoice.

2026-11-23 verification is recorded in exactreuse-retest-20261123/RESULT.md:
original/paraphrased 6–7 and partial-retention 4 plus continued 5–7 all pass their
target functional checks; 21 archives independently restore. Preserve three
intermediate stopped trials. Current final-trial usage reconciles, but one old
unknown memory request remains held at 2.162688 CNY and was not replayed;
global_billing_complete remains false. Do not convert functional acceptance,
standalone recovery, factual correctness or provider invoice reconciliation into
one success flag. Shared/default first-turn behavior remains unchanged.

The fixed first-pass batch observes the current runtime without tuning it:
report_firstpass_batch.py freezes cases/code and starts each conversation once,
records all failed/internal-recovery usage, and separates completion from clean
completion. PilotUsageAudit now persists numeric source-ID/role/turn records
and passive tool-name/error/argument-hash observations; no tool argument prose
or hidden reasoning is stored. The original observer remains composed/restored.
--experiment-limits changes only the predeclared whole-batch aggregate token
capacity; native role budgets, routing, tools, Skills and acceptance are fixed.
Cache is a subset of input. Missing usage/cache stays unknown. Do not infer a
false-positive guard rate automatically from any rejected tool action.

Frozen first-pass results (firstpass-frozen-20261123/RESULT.md): 1/4 conversations
complete, 0/4 clean; 12/15 attempted follow-ups pass, 9/24 planned follow-ups were
not attempted after prerequisites failed. Three GitAgent stops followed seven
inspection results with no exact duplicate-argument hashes in that execution;
loop_capped does not prove repeated identical calls. Do not tune this frozen
batch or reclassify it with post-fix outcomes. All current batch usage/cache
fields reconcile; old unknown exposure stays held. Token reports include failed
work, background memory and cache as an input subset, not an additive category.


Writer-first live first pass is frozen at
`../docs/research-report/writer-first-api-20261123/RESULT.md`: lowcode 6/6,
manufacturing 0/1 after Central guessed n5 for requested n4; native delivery
succeeded but scope evaluation failed. Preserve this failure. Local Writer
packets now include host-read section_catalog and reads return actual titles;
original user location outranks guessed delegate IDs. This is context repair,
not a new LLM, retry loop or rejection gate. Rehearsal covers the catalog/title
contract; real post-fix trials require separate frozen packets and fresh runs.


Post-location-fix live replication (`writer-first-retest-20261123/RESULT.md`):
manufacturing 6/6; lowcode edits 2–4 pass, readonly turn 5 ends with empty visible
content and finish_reason=length after ten inspections, no response archive;
turns 6–7 were not attempted. Do not report both conversations complete.
Preserve three recovered Central tool errors and the missing failed-turn archive
as known limitations. Next focus is concise scoped provenance reads and honest
empty-output diagnostics, not mandatory extra agents or guard relaxation.


Scoped readonly inspection now defaults to `provenance` (or
`provenance:<node_id>`): `turns/provenance.py` reads the accepted tree's evidence
locators, returning actual source metadata and compact quotes, with report_use
kept separate. Missing/truncated evidence is explicit, not a trigger for a new
agent. Direct material reads forward line bounds. Empty/truncated terminal
follow-up answers are archived as failed baseline snapshots; TaskMemory's
existing after_agent returns a user-visible notice without model retries.
Normal first turns and successful readonly archives retain their existing path.
See `../docs/research-report/READONLY_PROVENANCE_20261123.md`.

Report version reads return the actual per-call payload through optional
HandlerResult.tool_observation; SPControlActionMiddleware must not substitute
its 2000-character task-memory summary as the read result. This is scoped to
inspect_version; absent observations retain the original serialization and
first-turn behavior. Keep compact source views and the global memory bound.


Readonly live follow-ups are recorded in
`../docs/research-report/readonly-provenance-api-20261123/RESULT.md`: 5/5 single
questions archived, one recovered tool error; six-turn node/version checks pass
but strict last-cell scope initially fails. Preserve that failure; Writer's
single-cell guidance passes one fresh isolated turn-7 retest, not a new full-six
quality claim. Some citation/paragraph phrasing remains imprecise. Successful
readonly/reuse runs also retain actual final UI text as visible_response.md
separately from model-supplied response.md; do not rewrite frozen old results.


Three-topic fixed stability evaluation is described in
`../docs/research-report/three-topic-stability-20261123/PLAN.md`.
Its optional canonical_sha256_by_node checks are evaluator-only, after graph
execution; never send expected hashes to agents. Same-node extra edits fail.
The pilot inherits the source's native length contract, including its historical
absence; reports with a contract retain all original length checks. This changes
only the experiment adapter, not production acceptance or first-turn generation.

Imported report baselines may already contain completed local-tNNNN-* write
requests. Writer-first must allocate new local keys from all existing native
write_requests, not derive uniqueness from a reset dialogue turn number. Keep
legacy keys unchanged and duplicate tool receipts idempotent. The fixed first
three-topic pass in three-topic-stability-20261123/FIRST_PASS.md retains all
three first-turn failures; any post-fix replication is a separate new batch.


Post-key-fix fixed three-topic replication: all 12 version/exact-canonical
checks pass (three conversations); recovered tool errors remain 2/1/0 and only
one conversation is clean. Preserve the 0/3 original first pass. Readonly content
spot checks are 2/3: A2A incorrectly equates a returned line-23 excerpt with the
material ending there, although 147 lines are saved. A locator view is not proof
of source absence. See three-topic-stability-20261123/RESULT.md. This is an
unresolved source-boundary wording issue, not full factual-quality acceptance.


Report provenance now leads with material_boundaries: complete locally saved
line ranges vs ranges returned in the current reply, with explicit unread vs
unsaved wording. A saved file may itself be partial relative to the web original.
Only failed/cancelled *terminal dialogue turns* receive a program notice and
jump to end before another Central model call; executing/prepared and ordinary
first-turn work retain their original recovery paths. A new source message can
start another turn. Preserve the failed status, old accepted head and diagnostics;
never convert failure notice delivery into report delivery success.


source-boundary-terminal-20261123/RESULT.md records the frozen validation:
three original A2A questions correctly distinguish saved 147 lines from returned
excerpts, and one original edit passes exact SHA. One invalid stage is recovered;
this is not a terminal failed turn. Two scripted Writer-failure production-graph
probes stop after one Central request/delegation and preserve failed status/usage.
831 offline regressions pass. Do not claim provider-failure experiments were run
or that these few samples establish universal source-faithfulness accuracy.


Gateway report-dialogue presentation is a read-only projection in
`sp/report/turns/presentation.py`, exposed through authorized
`GET /api/threads/{thread_id}/report-dialogue`. Never invoke `session()` from
a read endpoint: it lazily freezes origins. The projection uses the checkpoint's
server-owned outputs path, authenticated user and URL thread identity, never
request-supplied paths/owners. Keep head, active version, current source-message
turn status and native delivery creation separate. Preserve the original
report-view/pipeline response and first-turn routing. No raw diagnostics/queries
or host paths belong in status responses. See Gateway dialogue API notes.
