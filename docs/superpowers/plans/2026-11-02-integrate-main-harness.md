# Integrate main Harness fixes into DCCv2

Bases: DCCv2 c632420; main 25062ca (code fix 6b17aa3).
User approved integration preserving both sets of fixes. No commit/push is authorized in this step.

## Decisions
- Merge full main history into the clean DCCv2 checkout; never replace the DCC dataset tree.
- Adopt main atomic run budget, independent graph/tool budgets, cancellation and context fixes.
- Canonical task metadata: task_kind; accept legacy coder_task_kind at the adapter boundary.
- Preserve DCC audit-negative findings, full artifacts, truncation/acceptance gates, non-interactive policy, scoped recovery and report file validation.
- Preserve explicit Reporter capacity and dataset delivery contracts from DCCv2.
- Keep both main regression tests and DCC-specific regressions; add crossed cases in existing test files.

## Execution
1. Fast-forward DCCv2, merge main without committing; inspect every conflict and auto-merged core caller.
2. Resolve task classification, recovery and verification with legacy compatibility; verify old and new tests.
3. Resolve budget/executor changes adopting main implementation and migrate DCC tests to independent budget contract.
4. Merge README and backend guidance, retaining dataset documentation; record old/new experimental budget differences.
5. Run focused backend regressions then broader available backend/offline suites; run repository Ruff and diff checks.
6. Review final diff and unresolved markers; report actual failures and unverified API/training scope. Do not commit/push.

## Evidence and rulings
- Initial main merge preview: 8 conflicting files, 15 overlapping files.
- Local DCCv2 was clean; original 5ea5262 fast-forwarded to c632420 before merge.
- Work in the explicitly requested checkout; no extra worktree or unrelated cleanup.

## Progress
- Main conflict resolution complete; canonical task_kind plus host-authored legacy alias.
- Watched RED: 7 crossed alias/audit tests failed; after integration they passed.
- Watched RED: 3 ambiguous-retry/contract-downgrade tests failed; after router repair focused393 passed.
- Local expanded run577 passed,5 skipped,1 missingFastAPI; server earlier snapshot1023 passed,1 missingFastAPI.
- Server verification runs in an isolated /tmp archive; live evaluation checkout remains untouched until verification.
- Budget decision: adopt main defaults480/50/8/200; CLAUSE configured Reporter128 remains in its example config.

- Independent reviewer reproduced two hazards: existing-patch verification accidentally reacquiring a mutation requirement, and configuration-only edits not counting as source changes. Both have RED/GREEN regressions in existing test files.
- Independent follow-up review found no additional concrete important issue in the seven integration files.
- Repository-configured Ruff check and format checks passed for all66 changed Python files.

## Final validation
- Server dcc-train, CUDA_VISIBLE_DEVICES empty, isolated archive of DCCv2 plus merge changes.
- Focused backend:1032 passed, exit0. Log /tmp/dccv2-integration-final.log.
- Extra model factory/summarization/config/memory suites:234 passed, exit0. Log /tmp/dccv2-integration-extra.log.
- Root: python -m pytest tests/ --no-sp-runtime -q --tb=short --color=no:316 passed,28 skipped,exit0. Log /tmp/dccv2-integration-root-final.log.
- Root initial4 compatibility failures were resolved: removed private budget helper, instance search guard, and native transport stream timeout defaults. Added explicit timeout override regression (37.0).
- Root test invocation from backend directory was invalid because relative configs resolve from repo root; reran from correct root. No test or assertion disabled.
- Initial dcc-harness and local Anaconda lacked FastAPI; existing dcc-train had all required dependencies. No installs performed.
- No live API, search provider, RAGFlow indexing or GPU parameter updates performed.
- Backend changed Python Ruff check/format passed. Root lint retains baseline-only broad-exception/import-order findings (confirmed with HEAD source); no unrelated cleanup performed.

## Complete delivery checklist
Paths below are relative to the DCCv2 repository. Main imports are integrated source, not newly invented implementations. New main modules/tests had no prior DCCv2 implementation.

| File | Origin / responsibility / before to after |
|---|---|
| `README.md` | Integration: Merge Chinese main overview with DCC lifecycle/CLAUSE instructions; keep both documentation surfaces. |
| `backend/AGENTS.md` | Integration: Document unified task fields, budget migration and strict audit/runtime distinctions. |
| `backend/README.md` | Integration: Document new independent budgets and legacy task compatibility. |
| `backend/app/channels/manager.py` | Main fix: manager; budget/context/cancellation call-chain integration. |
| `backend/app/gateway/services.py` | Main fix: services; budget/context/cancellation call-chain integration. |
| `backend/packages/harness/deerflow/agents/lead_agent/agent.py` | Main fix: agent; budget/context/cancellation call-chain integration. |
| `backend/packages/harness/deerflow/agents/middlewares/loop_detection_middleware.py` | Main fix: loop detection middleware; budget/context/cancellation call-chain integration. |
| `backend/packages/harness/deerflow/agents/middlewares/subagent_limit_middleware.py` | Main fix: subagent limit middleware; budget/context/cancellation call-chain integration. |
| `backend/packages/harness/deerflow/agents/middlewares/summarization_middleware.py` | Main fix: summarization middleware; budget/context/cancellation call-chain integration. |
| `backend/packages/harness/deerflow/agents/middlewares/token_budget_middleware.py` | Main fix: token budget middleware; budget/context/cancellation call-chain integration. |
| `backend/packages/harness/deerflow/agents/middlewares/token_usage_middleware.py` | Main fix: token usage middleware; budget/context/cancellation call-chain integration. |
| `backend/packages/harness/deerflow/agents/middlewares/tool_call_budget_middleware.py` | Main fix: tool call budget middleware; budget/context/cancellation call-chain integration. |
| `backend/packages/harness/deerflow/agents/middlewares/tool_error_handling_middleware.py` | Main fix: tool error handling middleware; budget/context/cancellation call-chain integration. |
| `backend/packages/harness/deerflow/agents/middlewares/web_search_budget_middleware.py` | Main fix: web search budget middleware; budget/context/cancellation call-chain integration. |
| `backend/packages/harness/deerflow/agents/thread_state.py` | Main fix: thread state; budget/context/cancellation call-chain integration. |
| `backend/packages/harness/deerflow/client.py` | Main fix: client; budget/context/cancellation call-chain integration. |
| `backend/packages/harness/deerflow/config/agents_config.py` | Main configuration schema: agents config. |
| `backend/packages/harness/deerflow/config/app_config.py` | Main configuration schema: app config. |
| `backend/packages/harness/deerflow/config/loop_detection_config.py` | Main configuration schema: loop detection config. |
| `backend/packages/harness/deerflow/config/model_config.py` | Main configuration schema: model config. |
| `backend/packages/harness/deerflow/config/stream_bridge_config.py` | Main configuration schema: stream bridge config. |
| `backend/packages/harness/deerflow/config/subagents_config.py` | Main configuration schema: subagents config. |
| `backend/packages/harness/deerflow/config/summarization_config.py` | Main configuration schema: summarization config. |
| `backend/packages/harness/deerflow/models/factory.py` | Integration: Apply stream timeout defaults to actual OpenAI subclasses; preserve explicit overrides and non-OpenAI behavior. |
| `backend/packages/harness/deerflow/runtime/runs/worker.py` | Main fix: worker; budget/context/cancellation call-chain integration. |
| `backend/packages/harness/deerflow/runtime/tool_budget.py` | Main fix: tool budget; budget/context/cancellation call-chain integration. |
| `backend/packages/harness/deerflow/sp/actions/handlers/delegate.py` | Integration: Normalize task kind and host-author acceptance metadata; preserve full artifacts, language and source-reference checks. |
| `backend/packages/harness/deerflow/sp/actions/router.py` | Integration: Keep task type over retry; preserve existing-patch verification, prevent software contract downgrade. |
| `backend/packages/harness/deerflow/sp/agent_tools.py` | Integration: Combine native software-recovery predicate with DCC data recovery and noninteractive gates. |
| `backend/packages/harness/deerflow/sp/central/prompt.py` | Integration: Use canonical task_kind and preserve explicit data/software distinction. |
| `backend/packages/harness/deerflow/sp/central_resilience.py` | Main fix: central resilience; budget/context/cancellation call-chain integration. |
| `backend/packages/harness/deerflow/sp/runtime.py` | Integration: Merge upstream budgets/cancellation with DCC task tools and noninteractive factory policy. |
| `backend/packages/harness/deerflow/sp/subagents/adapter.py` | Main fix: adapter; budget/context/cancellation call-chain integration. |
| `backend/packages/harness/deerflow/sp/subagents/dr2_adapter.py` | Integration: Combine upstream issued-after-edit verification with DCC negative audits, safe pipe handling, complete artifacts and provider truncation. |
| `backend/packages/harness/deerflow/subagents/builtins/bash_agent.py` | Main fix: bash agent; budget/context/cancellation call-chain integration. |
| `backend/packages/harness/deerflow/subagents/builtins/general_purpose.py` | Main fix: general purpose; budget/context/cancellation call-chain integration. |
| `backend/packages/harness/deerflow/subagents/builtins/memory_recaller.py` | Main fix: memory recaller; budget/context/cancellation call-chain integration. |
| `backend/packages/harness/deerflow/subagents/builtins/sp_specialists.py` | Integration: Adopt480 graph defaults; preserve DCC prompts and caller Reporter128 override. |
| `backend/packages/harness/deerflow/subagents/config.py` | Main fix: config; budget/context/cancellation call-chain integration. |
| `backend/packages/harness/deerflow/subagents/executor.py` | Integration: Adopt atomic run budgets/cancellation and independent50-call ceilings; preserve token stop reasons/evidence. |
| `backend/packages/harness/deerflow/tools/builtins/task_tool.py` | Main fix: task tool; budget/context/cancellation call-chain integration. |
| `backend/packages/harness/deerflow/utils/token_estimation.py` | Main fix: token estimation; budget/context/cancellation call-chain integration. |
| `backend/tests/test_channels.py` | Regression: channels; retain upstream and applicable DCC assertions. |
| `backend/tests/test_client.py` | Regression: client; retain upstream and applicable DCC assertions. |
| `backend/tests/test_durable_context_middleware.py` | Regression: durable context middleware; retain upstream and applicable DCC assertions. |
| `backend/tests/test_final_harness_review.py` | Regression: final harness review; retain upstream and applicable DCC assertions. |
| `backend/tests/test_gateway_services.py` | Regression: gateway services; retain upstream and applicable DCC assertions. |
| `backend/tests/test_harness_interaction_regressions.py` | Regression: harness interaction regressions; retain upstream and applicable DCC assertions. |
| `backend/tests/test_harness_parameter_regressions.py` | Regression: harness parameter regressions; retain upstream and applicable DCC assertions. |
| `backend/tests/test_lead_agent_model_resolution.py` | Regression: lead agent model resolution; retain upstream and applicable DCC assertions. |
| `backend/tests/test_loop_detection_middleware.py` | Regression: loop detection middleware; retain upstream and applicable DCC assertions. |
| `backend/tests/test_run_tool_budget.py` | Regression: run tool budget; retain upstream and applicable DCC assertions. |
| `backend/tests/test_sp_action_system.py` | Regression: sp action system; retain upstream and applicable DCC assertions. |
| `backend/tests/test_sp_agent_tools.py` | Regression: sp agent tools; retain upstream and applicable DCC assertions. |
| `backend/tests/test_sp_dr2_subagent_adapter.py` | Regression: sp dr2 subagent adapter; retain upstream and applicable DCC assertions. |
| `backend/tests/test_sp_memory_recaller_subagent.py` | Regression: sp memory recaller subagent; retain upstream and applicable DCC assertions. |
| `backend/tests/test_sp_review_regressions.py` | Regression: sp review regressions; retain upstream and applicable DCC assertions. |
| `backend/tests/test_subagent_executor.py` | Regression: subagent executor; retain upstream and applicable DCC assertions. |
| `backend/tests/test_subagent_skills_config.py` | Regression: subagent skills config; retain upstream and applicable DCC assertions. |
| `backend/tests/test_subagent_timeout_config.py` | Regression: subagent timeout config; retain upstream and applicable DCC assertions. |
| `backend/tests/test_summarization_summary_text.py` | Regression: summarization summary text; retain upstream and applicable DCC assertions. |
| `backend/tests/test_task_tool_core_logic.py` | Regression: task tool core logic; retain upstream and applicable DCC assertions. |
| `backend/tests/test_token_budget_middleware.py` | Regression: token budget middleware; retain upstream and applicable DCC assertions. |
| `backend/tests/test_token_usage.py` | Regression: token usage; retain upstream and applicable DCC assertions. |
| `backend/tests/test_token_usage_middleware.py` | Regression: token usage middleware; retain upstream and applicable DCC assertions. |
| `backend/tests/test_tool_call_budget_middleware.py` | Regression: tool call budget middleware; retain upstream and applicable DCC assertions. |
| `backend/tests/test_tool_error_handling_middleware.py` | Regression: tool error handling middleware; retain upstream and applicable DCC assertions. |
| `backend/tests/test_tool_output_budget_middleware.py` | Regression: tool output budget middleware; retain upstream and applicable DCC assertions. |
| `backend/tests/test_web_search_budget_middleware.py` | Regression: web search budget middleware; retain upstream and applicable DCC assertions. |
| `config.example.yaml` | Main configuration: independent run/child limits and model-aware summarization defaults. |
| `dcc_harness/evaluation/benchmark_runner.py` | Integration: Shared transport timeout behavior for both native evaluation clients; preserve loop-local pools. |
| `tests/test_clause_ragflow.py` | Integration: Migrate obsolete private helper test to independent graph/tool budget contract, retaining Reporter128 checks. |
| `tests/test_sp_benchmarks.py` | Integration: Use native instance search guard; assert default and explicit timeout parity for both transports. |
| `docs/superpowers/plans/2026-11-02-integrate-main-harness.md` | New authorized integration plan, decisions, verification record and complete file checklist. |
