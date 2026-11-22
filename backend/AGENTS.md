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
The Central still chooses the perception `git_agent` profile through `sp_delegate`.
Four Skills live outside default public discovery in `skills/report-multiturn/` and
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
