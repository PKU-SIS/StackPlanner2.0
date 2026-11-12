# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, Codex, and others) when working with code in this repository. It is the source of truth; the sibling `CLAUDE.md` imports it via `@AGENTS.md`.

## Project Overview

DeerFlow is a LangGraph-based AI super agent system with a full-stack architecture. The backend provides a "super agent" with sandbox execution, persistent memory, subagent delegation, and extensible tool integration - all operating in per-thread isolated environments.

**Architecture**:
- **Gateway API** (port 8001): REST API plus embedded LangGraph-compatible agent runtime
- **Frontend** (port 3000): Next.js web interface
- **Nginx** (port 2026): Unified reverse proxy entry point
- **Provisioner** (port 8002, optional in Docker dev): Started only when sandbox is configured for provisioner/Kubernetes mode

**Runtime**:
- `make dev`, Docker dev, and production all run the agent runtime in Gateway via `RunManager` + `run_agent()` + `StreamBridge` (`packages/harness/deerflow/runtime/`). Nginx exposes that runtime at `/api/langgraph/*` and rewrites it to Gateway's native `/api/*` routers.
- Scheduled-task executions must reuse that same Gateway run lifecycle. The scheduler may decide *when* work runs, but it must dispatch through the existing run path rather than introducing a parallel execution stack.

The execution-debug trace exposes an explicit `stop_reason`, `stop_detail`,
`last_stage`, `last_action_id`, and `last_event_type`. These are safe execution
diagnostics rather than hidden chain-of-thought, and distinguish normal
completion, stalled perception, timeouts, action validation failures, recursion
limits, provider errors, and cancellation. The local SWE-bench runner writes the
same fields into each result so partial patches and no-progress timeouts cannot be
mistaken for passes.

**Project Structure**:
```
deer-flow/
├── Makefile                    # Root commands (check, install, dev, stop)
├── config.yaml                 # Main application configuration
├── extensions_config.json      # MCP servers and skills configuration
├── backend/                    # Backend application (this directory)
│   ├── Makefile               # Backend-only commands (dev, gateway, lint)
│   ├── langgraph.json         # LangGraph Studio graph configuration
│   ├── packages/
│   │   └── harness/           # deerflow-harness package (import: deerflow.*)
│   │       ├── pyproject.toml
│   │       └── deerflow/
│   │           ├── agents/            # LangGraph agent system
│   │           │   ├── lead_agent/    # Main agent (factory + system prompt)
│   │           │   ├── middlewares/   # middleware components (see Middleware Chain section)
│   │           │   ├── memory/        # Memory extraction, queue, prompts
│   │           │   └── thread_state.py # ThreadState schema
│   │           ├── sandbox/           # Sandbox execution system
│   │           │   ├── local/         # Local filesystem provider
│   │           │   ├── sandbox.py     # Abstract Sandbox interface
│   │           │   ├── tools.py       # bash, ls, read/write/str_replace
│   │           │   └── middleware.py  # Sandbox lifecycle management
│   │           ├── subagents/         # Subagent delegation system
│   │           │   ├── builtins/      # general-purpose, bash agents
│   │           │   ├── executor.py    # Background execution engine
│   │           │   └── registry.py    # Agent registry
│   │           ├── tools/builtins/    # Built-in tools (present_files, ask_clarification, view_image)
│   │           ├── mcp/               # MCP integration (tools, cache, client)
│   │           ├── models/            # Model factory with thinking/vision support
│   │           ├── skills/            # Skills discovery, loading, parsing
│   │           ├── config/            # Configuration system (app, model, sandbox, tool, etc.)
│   │           ├── community/         # Community tools (search/fetch/scrape, image search, AIO sandbox)
│   │           ├── reflection/        # Dynamic module loading (resolve_variable, resolve_class)
│   │           ├── utils/             # Utilities (network, readability)
│   │           └── client.py          # Embedded Python client (DeerFlowClient)
│   ├── app/                   # Application layer (import: app.*)
│   │   ├── gateway/           # FastAPI Gateway API
│   │   │   ├── app.py         # FastAPI application
│   │   │   └── routers/       # FastAPI route modules (models, mcp, memory, skills, uploads, threads, artifacts, agents, suggestions, channels)
│   │   └── channels/          # IM platform integrations
│   ├── tests/                 # Test suite
│   └── docs/                  # Documentation
├── frontend/                   # Next.js frontend application
└── skills/                     # Agent skills directory
    ├── public/                # Public skills (committed)
    └── custom/                # Custom skills (gitignored)
```

## Important Development Guidelines

### Documentation Update Policy
**CRITICAL: Always update README.md and AGENTS.md after every code change**

When making code changes, you MUST update the relevant documentation:
- Update `README.md` for user-facing changes (features, setup, usage instructions)
- Update `AGENTS.md` for development changes (architecture, commands, workflows, internal systems). `CLAUDE.md` imports it via `@AGENTS.md`, so editing `AGENTS.md` updates both.
- Keep documentation synchronized with the codebase at all times
- Ensure accuracy and timeliness of all documentation

## Commands

**Root directory** (for full application):
```bash
make check      # Check system requirements
make install    # Install all dependencies (frontend + backend)
make dev        # Start all services (Gateway + Frontend + Nginx), with config.yaml preflight
make start      # Start production services locally
make stop       # Stop all services
```

**Backend directory** (for backend development only):
```bash
make install            # Install backend dependencies
make dev                # Run Gateway API with reload (port 8001)
make gateway            # Run Gateway API only (port 8001)
make test               # Run all backend tests
make test-blocking-io   # Run strict Blockbuster runtime gate on tests/blocking_io/
make lint               # Lint with ruff
make format             # Format code with ruff
make migrate-rev MSG="..."  # Autogenerate a new alembic revision (see Schema Migrations section)
```

The `detect-blocking-io` target parses `app/`, `packages/harness/deerflow/`,
and `scripts/` with AST. By default it reports only blocking IO candidates that
are inside async code, reachable from async code in the same file, or reachable
from sync-only `AgentMiddleware` before/after hooks that LangGraph can execute
on the async graph path. It prints a concise summary and writes complete JSON
findings to `.deer-flow/blocking-io-findings.json` at the repository root
(both `make detect-blocking-io` from the repo root and `cd backend && make
detect-blocking-io` resolve to the same repo-root path). JSON findings include
`priority`, `location`, `blocking_call`, `event_loop_exposure`, `reason`, and
`code` for model-assisted or manual review. `priority` is a deterministic
review ordering from operation type, not proof of a bug. Bare-name same-file
calls are resolved by function name, so duplicate helper names in one file can
conservatively over-report async reachability. It is intentionally
informational and is not run from CI in this round.

For a diff-scoped view of the same findings, `scripts/scan_changed_blocking_io.py`
(repo root) reports findings on the added lines of `git diff <base>...HEAD`
plus findings new versus the merge base (so a new async caller exposing an
untouched sync helper in the same file is still reported) — used by the
`blocking-io-guard` skill (`.agent/skills/blocking-io-guard/`) as the
deterministic scope step before routing each candidate to a fix and/or a
`tests/blocking_io/` runtime anchor.

Regression tests related to Docker/provisioner behavior:
- `tests/test_docker_sandbox_mode_detection.py` (mode detection from `config.yaml`)
- `tests/test_provisioner_kubeconfig.py` (kubeconfig file/directory handling)
- `tests/test_provisioner_request_threading.py` (keeps provisioner sandbox CRUD
  endpoints as sync FastAPI handlers so synchronous K8s client calls run in the
  Starlette worker pool instead of on the ASGI event loop)

Blocking-IO runtime gate (`tests/blocking_io/`):
- Wraps every item under `tests/blocking_io/` with a strict Blockbuster
  context scoped to `app.*` and `deerflow.*` (see
  `tests/support/detectors/blocking_io_runtime.py`). Any sync blocking IO
  call whose stack passes through DeerFlow business code while running on
  the asyncio event loop raises `BlockingError` and fails the test.
- Regression anchors live there: `test_skills_load.py` (locks the
  `asyncio.to_thread` offload around `LocalSkillStorage.load_skills`, fix
  for #1917); `test_sqlite_lifespan.py` (locks the offload around
  SQLite path resolution plus `ensure_sqlite_parent_dir`, fix for #1912);
  `test_jsonl_run_event_store.py` (locks `JsonlRunEventStore`'s async
  API offloading its file IO via `asyncio.to_thread`);
  `test_uploads_middleware.py` (locks `UploadsMiddleware.abefore_agent`
  offloading the uploads-directory scan off the event loop); and
  `test_uploads_router.py` (locks Gateway upload/list/delete endpoints
  offloading upload directory creation, staged writes, chmod/cleanup,
  directory scans/deletes, and remote sandbox sync off the event loop).
- `test_gate_smoke.py` is a meta-test asserting the gate actually catches
  unoffloaded blocking IO and that the `@pytest.mark.allow_blocking_io`
  opt-out works.
- Coverage boundary: the gate only sees code that test execution actually
  touches. Static AST coverage is a separate concern (out of scope for
  this PR).
- CI: runs on every PR via `.github/workflows/backend-blocking-io-tests.yml`,
  hard-fail.

Boundary check (harness → app import firewall):
- `tests/test_harness_boundary.py` — ensures `packages/harness/deerflow/` never imports from `app.*`

CI runs these regression tests for every pull request via [.github/workflows/backend-unit-tests.yml](../.github/workflows/backend-unit-tests.yml).

## Architecture

### Harness / App Split

The backend is split into two layers with a strict dependency direction:

- **Harness** (`packages/harness/deerflow/`): Publishable agent framework package (`deerflow-harness`). Import prefix: `deerflow.*`. Contains agent orchestration, tools, sandbox, models, MCP, skills, config — everything needed to build and run agents.
- **App** (`app/`): Unpublished application code. Import prefix: `app.*`. Contains the FastAPI Gateway API and IM channel integrations (Feishu, Slack, Telegram, DingTalk).

**Dependency rule**: App imports deerflow, but deerflow never imports app. This boundary is enforced by `tests/test_harness_boundary.py` which runs in CI.

**Import conventions**:
```python
# Harness internal
from deerflow.agents import make_lead_agent
from deerflow.models import create_chat_model

# App internal
from app.gateway.app import app
from app.channels.service import start_channel_service

# App → Harness (allowed)
from deerflow.config import get_app_config

# Harness → App (FORBIDDEN — enforced by test_harness_boundary.py)
# from app.gateway.routers.uploads import ...  # ← will fail CI
```

Package import hygiene: the `deerflow.agents` and `deerflow.subagents` package
roots expose heavyweight graph/executor entrypoints lazily. Internal modules
that only need lightweight types, config, or registries should import the
concrete submodule instead of adding eager package-root imports that pull in the
tool graph or subagent executor during state/schema imports.

### Agent System

**Lead Agent** (`packages/harness/deerflow/agents/lead_agent/agent.py`):
- Entry point: `make_lead_agent(config: RunnableConfig)` registered in `langgraph.json`
- Dynamic model selection via `create_chat_model()` with thinking/vision support
- Tools loaded via `get_available_tools()` - combines sandbox, built-in, MCP, community, and subagent tools
- System prompt generated by `apply_prompt_template()` with skills, memory, and subagent instructions

**ThreadState** (`packages/harness/deerflow/agents/thread_state.py`):
- Extends `AgentState` with: `sandbox`, `thread_data`, `title`, `artifacts`, `todos`, `uploaded_files`, `viewed_images`, `goal`, `promoted`, `delegations`, `skill_context`, `summary_text`, plus the `sp_*` StackPlanner orchestration state.
- Uses custom reducers: `merge_artifacts` (deduplicate), `merge_viewed_images` (merge/clear), `merge_goal` (preserve the active goal across ordinary state updates unless the goal writer replaces it), `merge_promoted` (catalog-hash-scoped deferred tool promotions), `merge_delegations` (append task delegation entries, same id latest wins, terminal status never downgraded, capped to the most recent entries), and `merge_skill_context` (dedupe active-skill references by path, keep the most recently read entries; entries store a name/path/description reference, not the SKILL.md body). `summary_text` is a LastValue channel updated by summarization and projected into model requests as durable context data instead of being stored as a `messages` item.
- StackPlanner action branches use reducer-safe `sp_*` channels instead of LangGraph last-value channels. Parallel delegation results union task-memory entries, artifact history, idempotency records, and consumed-observation tombstones; summary `parent_ids` remain tombstones so a stale sibling branch cannot push compacted entries back onto the task-memory stack. Handler results retain all sibling outcomes and let any non-terminal/error action outrank a simultaneous `FINISH`; the control middleware also rejects `FINISH` beside a non-terminal sibling before LangGraph can preserve its `goto=END`.
- StackPlanner chooses direct, bounded-execution, or deliberate control profiles. Deliberate tasks can produce a normalized perception brief, one batch of decision-critical clarification questions, a minimum-node dependency-aware outline, staged execution/research, verification, and versioned human review without imposing that pipeline on simple requests. `PromptContextBuilder.workflow_status` exposes deduplicated artifact-stage counts, pinned feedback, and partial/blocked state to prevent redundant stages.
- SP-to-subagent delegation uses an additive internal A2A envelope at the Central/DR2 boundary. `SPSubagentTask` sends a `delegate` message with protocol version, stage, receiver, bounded input references, child-tool allowlist, acceptance criteria, and positive integer budgets; `SPSubagentResult` returns a `task_result` with completion status, retry recommendation, observable changed files/tests, and bounded artifact metadata. Progress and terminal events carry the same A2A task ID so the UI/debug trace can correlate dispatch, Skill/tool steps, and result. This is a message/context/artifact protocol adapter inspired by the original Stack-Planner A2A design; it does not expose ordinary tools to Central, and it deliberately excludes provider-private chain-of-thought.
- `SPCentralResilienceMiddleware` is installed only on the Central control-plane model. For the initial perception/planning decision it applies a bounded request timeout, one lower-token retry, and at most one synthetic `sp_think` recovery per run; it emits observable timeout/fallback events and never grants ordinary tools or changes subagent policy. This addresses provider stalls without turning the outer graph watchdog into the primary recovery mechanism.
- StackPlanner keeps large evidence out of `ThreadState`: `DelegateHandler` passes a separately bounded `mandatory_requirements.human_feedback` channel to every specialist, materializes perception/research bodies for Outline, outline/perception/evidence bodies for Researcher, and the full bounded evidence/parent-report bundle for Reporter. A revision also promotes the current run's cleaned user request (without the injected `<uploaded_files>` inventory) into Reporter's mandatory feedback. Source artifact IDs are retained across all materialized stage artifacts; report versions additionally retain parent and feedback lineage. `sp_finish` accepts `final_artifact_ref` plus `required_artifact_type` and rejects a mismatched artifact family. Repeated Reporter delegation without a revision reason is recoverable rather than silently terminating the run.
- Opt-in execution debugging travels as `context.debug_trace_enabled`. `services.start_run` marks the run metadata, `TaskMemoryMiddleware` records the exact bounded task-context block sent to CentralAgent, SP action events retain their original `ts`, and subagent `task_*` events carry `occurred_at`, role, prompt, stop reason, and aggregate usage before batch persistence. `app.gateway.debug_trace` pages and aggregates those events for `GET /api/threads/{thread_id}/runs/{run_id}/debug-trace`, pairs action/subagent start and terminal events, computes latency/offsets, and redacts credential-shaped values. It deliberately drops provider-private reasoning fields; the debug contract is observable output plus explicit action reasons and context, not hidden chain-of-thought.
- The no-Docker SWE-bench pilot uses `scripts/run_swebench_sp2.py`. It records the last observable stage/action and applies a per-instance `--no-progress-timeout` watchdog (default 180 seconds) in addition to the overall `--timeout`, so a graph that emits no further events is cancelled and reported as `no_progress_timeout` instead of consuming the full wall-clock budget. It subscribes to both graph values and custom subagent lifecycle events, so diagnostics distinguish a parent stall from a child that only reached `task_started`. The Qwen vLLM adapter must normalize both `reasoning` and `reasoning_content`; the current endpoint emits text-protocol `<tool_call>` blocks in the latter, which are recovered into LangChain tool calls without exposing the private reasoning stream. The benchmark explicitly disables CentralAgent and subagent extended thinking because the current Qwen endpoint can hang on the long tool-bound reasoning request; this is isolated to the benchmark runner, not the normal web configuration.
- `sp-reporter` loads the `stackplanner-reporting` Skill and may create a unique Markdown file with `write_file`; it remains unable to search or run shell commands. Transport-wrapper cleanup prevents nested JSON or outer Markdown fences from becoming the persisted report body. Report acceptance recognizes both backticked and bare identifier-shaped preservation lists, accepts a current action's full ID or unique opaque suffix in a created filename, and enforces explicit user-supplied percentage-point boundary adjustments instead of accepting implied table arithmetic.
- A user correction forces at most one targeted REFLECT/BACKTRACK per run before corrected work resumes; the reservation prevents a deep prior stack from being peeled in repeated batches. For REFLECT/REVISE/BACKTRACK, a unique one-character typo in a long opaque `spmem_...` target is normalized and traced as `sp.action.target_normalized`; ambiguous or larger mismatches still fail closed.
- Non-report specialist recovery is bounded to three attempts per role and run. After the cap, `SPFinishAvailabilityMiddleware` removes SP control actions and requires an honest direct gap report instead of allowing a recursion-limit/token loop. Its post-model guard also converts a plain-text success claim for a still-partial requested artifact into a recovery `sp_delegate`; at the cap, it replaces the claim with a deterministic incomplete response. A complete current-run non-report deliverable is forced directly through `sp_finish`. A blocked consecutive same-role delegation is recorded as an effective `THINK` policy checkpoint, so it delays one retry without permanently deadlocking that role. `ActionRouter` normalizes a mistaken Researcher delegation for self-contained exact arithmetic or exhaustive assignment/combinatorial work to Coder and emits `sp.delegate.target_normalized`; requests that explicitly need web/source/current evidence remain Researcher work. Coder delegations default to `read_file`, `write_file`, `str_replace`, and `bash`; Central must explicitly request network tools. A narrowed allowlist retains those local tools, and reporter narrowing always retains both `read_file` and `write_file` so a safe revision retry can inspect an existing candidate before changing it. Common interpreter aliases (`python`, `python3`, `node`, shells) normalize to `bash` before validation. The coder contract forbids redirecting program stdout into a source path, requires source creation and execution as separate operations, uses canonical backtracking instead of factorial full permutations for combinatorial enumeration, and requires assertion/exact-comparison tests for supplied expected outputs instead of accepting printed examples. `DR2SubagentExecutorAdapter` downgrades a coder's `complete` claim to `partial` when an execution/test/verification task has no successful `bash` result after the latest source edit. Registered structured result files retain a bounded `finalization_preview`; `FinishHandler` uses it for the finish memory entry and visible terminal response and emits `sp.finish.summary_grounded`, so stale model prose cannot contradict an authoritative generated result.

**Runtime Configuration** (via `config.configurable`):
- `thinking_enabled` - Enable model's extended thinking
- `model_name` - Select specific LLM model
- `is_plan_mode` - Enable TodoList middleware
- `subagent_enabled` - Enable task delegation tool
- `debug_trace_enabled` - Opt a run into enhanced execution-context capture for the Web UI trace page

### Middleware Chain

Lead-agent middlewares are assembled in strict order across three functions: the shared base in `packages/harness/deerflow/agents/middlewares/tool_error_handling_middleware.py` (`_build_runtime_middlewares`, exposed via `build_lead_runtime_middlewares`), then the lead-only middlewares appended in `packages/harness/deerflow/agents/lead_agent/agent.py` (`build_middlewares`). Items marked *(optional)* are appended only when their config/runtime condition holds, so the live chain length varies.

**Shared runtime base** (`build_lead_runtime_middlewares`; subagents reuse most of this via `build_subagent_runtime_middlewares`):

1. **InputSanitizationMiddleware** - First, so it is the outermost `wrap_model_call` wrapper; every inner middleware (including LLM retries) sees sanitized messages
2. **ToolOutputBudgetMiddleware** - Caps tool output size (per app config) before it re-enters the model context
3. **ToolResultSanitizationMiddleware** - Neutralizes framework/injection tags (e.g. `<system-reminder>`) and boundary markers in *remote-content* tool results (`web_fetch`/`web_search`/`image_search`) so attacker-controlled fetched pages cannot forge trusted framework context. Mirrors `InputSanitizationMiddleware`'s user-input guardrail for the other untrusted-content entry point; sits inner of `ToolOutputBudgetMiddleware` (neutralizes the raw output, then the budget truncates). Local tool output (bash/read_file) is left untouched. Scope is a name-based allowlist, so MCP remote-content tools registered under other names (e.g. `fetch_url`) are not yet covered — a metadata-tagging follow-up is tracked in the middleware source
4. **UploadedFileSearchGuardMiddleware** - Detects when the current task already has uploaded files and tells delegated agents to inspect local evidence before spending time on redundant web searches
5. **WebSearchBudgetMiddleware** - Enforces search policy where subagents actually execute tools: exact-query reuse, duplicate-batch suppression, at most 4 parallel searches per model turn, at most 8 completed searches per visible user turn, and a deterministic stop after 2 consecutive failures
6. **ThreadDataMiddleware** - Creates per-thread directories under the user's isolation scope (`backend/.deer-flow/users/{user_id}/threads/{thread_id}/user-data/{workspace,uploads,outputs}`); resolves `user_id` via `get_effective_user_id()` (falls back to `"default"` in no-auth mode)
7. **UploadsMiddleware** - Tracks and injects newly uploaded files into conversation (lead agent only)
8. **SandboxMiddleware** - Acquires sandbox, stores `sandbox_id` in state
9. **DanglingToolCallMiddleware** - Injects placeholder ToolMessages for AIMessage tool_calls that lack responses (e.g., user interruption), preserving raw provider tool-call payloads in `additional_kwargs["tool_calls"]`
10. **LLMErrorHandlingMiddleware** - Normalizes provider/model invocation failures into recoverable assistant-facing errors before later stages run
11. **GuardrailMiddleware** - *(optional, if `guardrails.enabled`)* Pre-tool-call authorization via pluggable `GuardrailProvider`; returns an error ToolMessage on deny. Providers: built-in `AllowlistProvider` (zero deps), OAP policy providers (e.g. `aport-agent-guardrails`), or custom. See [docs/GUARDRAILS.md](docs/GUARDRAILS.md)
12. **SandboxAuditMiddleware** - Audits sandboxed shell/file operations for security logging before tool execution
13. **ReadBeforeWriteMiddleware** - *(optional, if `read_before_write.enabled`, default on)* Outermost write gate (issue #3857): `read_file` stamps a content hash onto its ToolMessage; `write_file` (append/overwrite-existing) and `str_replace` are blocked unless the newest mark for that path matches the file's current hash. Sits outside ToolProgressMiddleware and ToolErrorHandlingMiddleware so a blocked write returns immediately without consuming a ToolProgress slot. Blocked results call `normalize_tool_result` directly to stamp `deerflow_tool_meta` (`recoverable_by_model=True`) before returning, keeping the result well-formed for any outer consumer. Marks live on messages, so summarization dropping the read result invalidates the gate automatically; writes never refresh marks, forcing a re-read between consecutive edits. Gate check + tool execution are serialized per (thread, path) so same-turn parallel writes cannot reuse one stale mark; on sandboxes whose `read_file` reports failures as `"Error: ..."` strings instead of raising (AIO/E2B), uninspectable targets fail open (creation proceeds, no mark stamped)
14. **ToolProgressMiddleware** - *(optional, if `tool_progress.enabled`)* State-machine-based stagnation guard (RFC #3177). Outer wrapper around ToolErrorHandlingMiddleware so its `wrap_tool_call` receives results already stamped with `deerflow_tool_meta`. Tracks per-(thread, tool) consecutive "no-new-info" calls across three error categories: (a) `recoverable_by_model=True` (no_results, not_found, permission, Jaccard-duplicate success): ACTIVE → WARNED (terminal — hint re-injected on each subsequent problem); (b) `recoverable_by_model=False, action≠stop` (rate_limited, transient): ACTIVE → WARNED → BLOCKED after `warn_escalation_count` more problems; (c) `recoverable_by_model=False, action=stop` (auth, config, internal): immediately BLOCKED on first occurrence. **Division of labor with LoopDetectionMiddleware:** ToolProgressMiddleware is a result-quality guard — fires after tool execution and blocks specific tools that stop producing new information; LoopDetectionMiddleware is a call-pattern guard — fires after the model responds and hard-stops the whole turn when the model repeatedly issues identical tool_calls. Both can inject HumanMessage hints in the same model call without conflict; neither reads the other's internal state.
15. **ToolErrorHandlingMiddleware** - Receives `AppConfig`, converts tool exceptions into error `ToolMessage`s so the run can continue instead of aborting, stamps every result with `deerflow_tool_meta` (status / error_type / recoverable_by_model / recommended_next_action / source) via `tool_result_meta.normalize_tool_result`, stamps structured metadata for task exception wrappers, and stamps skill-read metadata for downstream durable-context capture. Task tool result text is generated from the same status/result/error inputs as the structured metadata so callers do not hand-write a second protocol string.

**Lead-only middlewares** (`build_middlewares`, appended after the base):

16. **DynamicContextMiddleware** - Injects the current date (and optionally memory) as a `<system-reminder>` into the first HumanMessage, keeping the base system prompt fully static for prefix-cache reuse
17. **SkillActivationMiddleware** - Detects strict `/skill-name task` syntax on the latest real user message, resolves only enabled and runtime-allowed skills, injects the `SKILL.md` body as hidden current-turn context, and records a `middleware:skill_activation` audit event
18. **DurableContextMiddleware** - Captures `task` delegations into `ThreadState.delegations` (including in-progress dispatches and terminal result summaries) and loaded skill-file references (name/path/description, parsed in-memory - not the body) into `ThreadState.skill_context` before summarization can compact the paired tool-call/result messages, then projects durable context into each model request. Static authority rules are injected as a `SystemMessage`; untrusted field values (`summary_text`, delegation results, skill descriptions) are injected separately as a hidden `HumanMessage` data block so compressed history, delegated work, and which skills are active stay visible without being stored as `messages` or promoted to system-role instructions.
19. **SummarizationMiddleware** - *(optional, if enabled)* Context reduction when approaching token limits
20. **TodoListMiddleware** - *(optional, if `is_plan_mode`)* Task tracking with the `write_todos` tool
21. **TokenUsageMiddleware** - *(optional, if `token_usage.enabled`)* Records token usage metrics; subagent usage is merged back into the dispatching AIMessage by message position
22. **TitleMiddleware** - Auto-generates the thread title after the first complete exchange and normalizes structured message content before prompting the title model. If a first-turn run is interrupted before this middleware can write a title, `runtime/runs/worker.py` keeps the run in a finalizing state, persists a local fallback title from the latest checkpoint or original run input, and then syncs it to `threads_meta.display_name`. Replacement runs admitted by `multitask_strategy="interrupt"` / `"rollback"` wait for older same-thread finalization before entering the graph; the interrupted run only skips the fallback title write once a later run has started and may have advanced the checkpoint.
23. **MemoryMiddleware** - Queues conversations for async memory update (filters to user + final AI responses)
24. **ViewImageMiddleware** - *(optional, if the model supports vision)* Injects base64 image data before the LLM call
25. **DeferredToolFilterMiddleware** - *(optional, if `tool_search.enabled`)* Hides deferred (MCP) tool schemas from the bound model until `tool_search` promotes them (reads per-thread promotions from `ThreadState.promoted`, hash-scoped)
26. **SystemMessageCoalescingMiddleware** - Merges every SystemMessage into a single leading SystemMessage per request; provider-agnostic fix for strict backends (vLLM/SGLang/Qwen/Anthropic) that reject non-leading system messages. Touches the per-request payload only (checkpoint state unchanged); on midnight crossings only the latest `dynamic_context_reminder` SystemMessage survives
27. **SubagentLimitMiddleware** - *(optional, if `subagent_enabled`)* Truncates excess `task` tool calls to enforce the `MAX_CONCURRENT_SUBAGENTS` limit
28. **LoopDetectionMiddleware** - *(optional, if `loop_detection.enabled`)* Detects repeated tool-call loops; hard-stop clears both structured `tool_calls` and raw provider tool-call metadata before forcing a final text answer; stamps `loop_capped` via `consume_stop_reason` (#3875 Phase 2), symmetric to `TokenBudgetMiddleware`
29. **TokenBudgetMiddleware** - *(optional, if `token_budget.enabled`)* Enforces per-run token limits
30. **Custom middlewares** - *(optional)* Any `custom_middlewares` passed to `build_middlewares` are injected here, before the safety/clarification tail
31. **SafetyFinishReasonMiddleware** - *(optional, if `safety_finish_reason.enabled`)* Suppresses tool execution when the provider safety-terminated the response (e.g. `finish_reason=content_filter`); registered after custom middlewares so LangChain's reverse-order `after_model` dispatch runs it first
32. **ClarificationMiddleware** - Intercepts `ask_clarification` tool calls, writes a readable `ToolMessage.content` fallback plus structured `ToolMessage.artifact.human_input` request payload, and interrupts via `Command(goto=END)` (must be last). Because this middleware can short-circuit tool execution before LangChain emits `on_tool_end`, `RunJournal` performs a root-run final reconciliation for allowlisted clarification `ToolMessage`s whose `tool_call_id` was produced by the current run, so human-input request cards remain recoverable from `run_events` after checkpoint compaction. Human Input Card replies are submitted as `hide_from_ui` `HumanMessage`s with `additional_kwargs.human_input_response`; `RunJournal` persists only allowlisted hidden response sources (currently `ask_clarification`) as `llm.human.input`, which preserves answered-card state after compaction without exposing generic internal hidden context.

### Configuration System

**Main Configuration** (`config.yaml`):

Setup: Copy `config.example.yaml` to `config.yaml` in the **project root** directory.

**Config Versioning**: `config.example.yaml` has a `config_version` field. On startup, `AppConfig.from_file()` compares user version vs example version and emits a warning if outdated. Missing `config_version` = version 0. Run `make config-upgrade` to auto-merge missing fields. When changing the config schema, bump `config_version` in `config.example.yaml`.

**Config Caching**: `get_app_config()` caches the parsed config, but automatically reloads it when the resolved config path or file content signature changes. The signature includes file metadata and a content digest, so Gateway and LangGraph reads stay aligned with `config.yaml` edits even on object-store or network mounts where mtime can remain stale.

**Config Hot-Reload Boundary**: Gateway dependencies route through `get_app_config()` on every request, so per-run fields like `models[*].max_tokens`, `summarization.*`, `title.*`, `memory.*`, `subagents.*`, `tools[*]`, and the agent system prompt pick up `config.yaml` edits on the next message. `AppConfig` is intentionally **not** cached on `app.state` — `lifespan()` keeps a local `startup_config` variable for one-shot bootstrap work and passes it to `langgraph_runtime(app, startup_config)`.

Infrastructure fields are **restart-required**. The authoritative list lives in `packages/harness/deerflow/config/reload_boundary.py::STARTUP_ONLY_FIELDS` and is mirrored by the standardised `"startup-only:"` prefix on the corresponding `Field(description=...)` in `AppConfig`, so IDE hover on those fields surfaces the reason inline (no need to context-switch into this table). Currently registered: `database`, `checkpointer`, `run_events`, `stream_bridge`, `sandbox`, `log_level`, `logging`, `channels`, `channel_connections`. Adding a new restart-required field requires updating the registry; drift is pinned by `tests/test_reload_boundary.py`.

**Persistence backend resolution**: the unified `database` section selects the
Gateway's LangGraph checkpointer, LangGraph Store, and DeerFlow SQL repositories.
The deprecated `checkpointer` section remains backward compatible and, when
present, overrides `database` for the LangGraph checkpointer and Store only;
application repositories continue to use `database`.

Configuration priority:
1. Explicit `config_path` argument
2. `DEER_FLOW_CONFIG_PATH` environment variable
3. `config.yaml` in current directory (backend/)
4. `config.yaml` in parent directory (project root - **recommended location**)

Config values starting with `$` are resolved as environment variables (e.g., `$OPENAI_API_KEY`).
`ModelConfig` also declares `use_responses_api` and `output_version` so OpenAI `/v1/responses` can be enabled explicitly while still using `langchain_openai:ChatOpenAI`.

**Extensions Configuration** (`extensions_config.json`):

MCP servers and skills are configured together in `extensions_config.json` in project root:

Docker development mounts the project directory at `/app/project` and points
`DEER_FLOW_CONFIG_PATH` / `DEER_FLOW_EXTENSIONS_CONFIG_PATH` into that directory.
Keep mutable config files behind a directory bind mount: single-file bind mounts
can become stale or inaccessible when a host editor replaces a file on save.

Configuration priority:
1. Explicit `config_path` argument
2. `DEER_FLOW_EXTENSIONS_CONFIG_PATH` environment variable
3. `extensions_config.json` in current directory (backend/)
4. `extensions_config.json` in parent directory (project root - **recommended location**)

### Gateway API (`app/gateway/`)

FastAPI application on port 8001 with health check at `GET /health`. Set `GATEWAY_ENABLE_DOCS=false` to disable `/docs`, `/redoc`, and `/openapi.json` in production (default: enabled).

CORS is same-origin by default when requests enter through nginx on port 2026. Split-origin or port-forwarded browser clients must opt in with `GATEWAY_CORS_ORIGINS` (comma-separated exact origins); Gateway `CORSMiddleware` and `CSRFMiddleware` both read that variable so browser CORS and auth-origin checks stay aligned.

**Routers**:

| Router | Endpoints |
|--------|-----------|
| **Models** (`/api/models`) | `GET /` - list models; `GET /{name}` - model details |
| **Features** (`/api/features`) | `GET /` - report config-gated feature availability (currently `agents_api.enabled`) for frontend UI gating |
| **Console** (`/api/console`) | Read-only cross-thread observability for the current user (the data layer for an operations dashboard or external monitoring): `GET /stats` - headline counters (runs/threads/agents/tokens/cost); `GET /runs` - paginated run history joined with thread titles (per-run cost); `GET /usage` - zero-filled daily token series + per-model breakdown with spend. Queries `runs`/`threads_meta` directly as a reporting layer (no new `RunStore` methods); requires a SQL database backend — returns 503 on `database.backend: memory`. Real-cost estimation reads optional `models[*].pricing` (`currency`, `input_per_million`, `output_per_million`, `input_cache_hit_per_million`; `ModelConfig` is `extra="allow"`, so no schema change) and prices each run from its `token_usage_by_model` input/output split. Pricing is **cache-aware**: `RunJournal` accumulates prompt-cache hits from `usage_metadata.input_token_details.cache_read` into a sparse `cache_read_tokens` bucket key (also threaded through `SubagentTokenCollector` → `record_external_llm_usage_records`), and cache-hit input tokens are billed at `input_cache_hit_per_million` (omitted → billed at the miss price, a conservative upper bound). Legacy rows fall back to run-level totals at `model_name`; unpriced models yield `cost: null` and cost fields are null when no pricing is configured |
| **MCP** (`/api/mcp`) | `GET /config` - get config; `PUT /config` - update config (saves to extensions_config.json) |
| **Skills** (`/api/skills`) | `GET /` - list skills; `GET /{name}` - details; `PUT /{name}` - update enabled; `POST /install` - install from .skill archive (accepts standard optional frontmatter like `version`, `author`, `compatibility`) |
| **Memory** (`/api/memory`) | `GET /` - memory data; `POST /reload` - force reload; `GET /config` - config; `GET /status` - config + data |
| **Uploads** (`/api/threads/{id}/uploads`) | `POST /` - upload files (auto-converts PDF/PPT/Excel/Word); `GET /list` - list; `DELETE /{filename}` - delete |
| **Threads** (`/api/threads/{id}`) | `DELETE /` - remove DeerFlow-managed local thread data after LangGraph thread deletion; `POST /branches` - create a new main-thread branch from a completed assistant turn checkpoint. Workspace files are not checkpointed, so the branch only best-effort copies the current workspace when branching from the **latest** turn (`workspace_clone_mode="current_thread_best_effort"`); branching from an older/historical turn skips the copy (`workspace_clone_mode="skipped_historical_turn"`) so the branch never inherits files that only exist in a later timeline; `GET /goal`, `PUT /goal`, `DELETE /goal` - read, set, and clear the active thread goal; `POST /compact` - manually summarize older active context into `summary_text` and retain the recent message window, blocked while a run is in flight; unexpected failures are logged server-side and return a generic 500 detail |
| **Artifacts** (`/api/threads/{id}/artifacts`) | `GET /{path}` - serve artifacts; active content types (`text/html`, `application/xhtml+xml`, `image/svg+xml`) are always forced as download attachments to reduce XSS risk; `?download=true` still forces download for other file types |
| **Suggestions** (`/api/suggestions`) | `GET /config` - returns global suggestions config boolean; `POST /threads/{id}/suggestions` - generate follow-up questions; rich list/block model content is normalized and inline reasoning (`<think>...</think>`, including unclosed/truncated blocks from reasoning models like MiniMax-M3) is stripped before JSON parsing |
| **Input Polish** (`/api/input-polish`) | `POST /` - rewrite a composer draft before it is sent. This is a short authenticated `runs:create` LLM request using `input_polish` config; it does not create a LangGraph run, persist a message, or modify thread state. Shares the non-graph one-shot LLM path (`deerflow.utils.oneshot_llm.run_oneshot_llm`) with the suggestions route so model build + Langfuse metadata + invoke stay in one place; validates the same stripped view of the draft it sends to the model, and preserves literal `<think>` substrings in the rewrite (`strip_think_blocks(truncate_unclosed=False)`) |
| **Thread Runs** (`/api/threads/{id}/runs`) | `POST /` - create background run; `POST /stream` - create + SSE stream; `POST /wait` - create + block; `POST /regenerate/prepare` - prepare clean input + checkpoint metadata for regenerating the latest assistant answer; `GET /` - list runs; `GET /{rid}` - run details; `POST /{rid}/cancel` - cancel; `GET /{rid}/join` - join SSE; `GET /{rid}/messages` - paginated messages `{data, has_more}`; `GET /{rid}/events` - full event stream; `GET /{rid}/workspace-changes` - workspace/output file change summary and optional diffs; `GET /../messages` - thread messages with feedback; `GET /../token-usage` - aggregate tokens |
| **Feedback** (`/api/threads/{id}/runs/{rid}/feedback`) | `PUT /` - upsert feedback; `DELETE /` - delete user feedback; `POST /` - create feedback; `GET /` - list feedback; `GET /stats` - aggregate stats; `DELETE /{fid}` - delete specific |
| **Runs** (`/api/runs`) | `POST /stream` - stateless run + SSE; `POST /wait` - stateless run + block; `GET /{rid}/messages` - paginated messages by run_id `{data, has_more}` (cursor: `after_seq`/`before_seq`); `GET /{rid}/feedback` - list feedback by run_id |
| **GitHub Webhooks** (`/api/webhooks/github`) | `POST /` - receive GitHub App / repo webhook deliveries. Verifies `X-Hub-Signature-256` against `GITHUB_WEBHOOK_SECRET`; exempt from auth + CSRF because authenticity is enforced by HMAC. The route is fail-closed: mounted only when `GITHUB_WEBHOOK_SECRET` is set, or when explicit dev opt-in `DEER_FLOW_ALLOW_UNVERIFIED_GITHUB_WEBHOOKS=1` is set. Recognized events include `ping`, `issues`, `issue_comment`, `pull_request`, `pull_request_review`, and `pull_request_review_comment`; unknown events return 200 with `handled=false`. Fan-out runtime failures return 503 so GitHub retries; permanent/non-retryable conditions such as `channels.github.enabled: false`, unknown events, malformed payloads, or unavailable channel service return 200 with a skipped/handled response. |
| **GitHub Event-Driven Agents** | Custom agents can declare a `github:` block in their `config.yaml` to bind to repos and event triggers. Webhook fan-out publishes one `InboundMessage` per matching binding to the channel bus; `GitHubChannel` routes those messages through `ChannelManager`. The response `dispatch` summarizes matched/fired/skipped agents. |

**Workspace change review**: `packages/harness/deerflow/workspace_changes/`
captures a pre-run and post-run snapshot of the thread-owned `workspace` and
`outputs` directories. `runtime/runs/worker.py` performs the filesystem scan via
`asyncio.to_thread` and writes a `workspace_changes` event with category
`workspace` when changes exist. Uploads are intentionally excluded. Text diffs
are size-limited; binary, large, and sensitive-looking paths are persisted as
metadata only.

**RunManager / RunStore contract**:
- `RunManager.get()` is async; direct callers must `await` it.
- When a persistent `RunStore` is configured, `get()` and `list_by_thread()` hydrate historical runs from the store. In-memory records win for the same `run_id` so task, abort, and stream-control state stays attached to active local runs.
- `cancel()` and `create_or_reject(..., multitask_strategy="interrupt"|"rollback")` persist interrupted status through `RunStore.update_status()`, matching normal `set_status()` transitions.
- Store-only hydrated runs are readable history. If the current worker has no in-memory task/control state for that run, cancellation APIs can return 409 because this worker cannot stop the task.
- `POST /wait` (both thread-scoped and `/api/runs/wait`) drains the stream bridge via `wait_for_run_completion()` instead of bare `await record.task`, so it honours the run's `on_disconnect` setting and cancels the background run on real client disconnect rather than returning a stale checkpoint (issue #3265).
- Redis `StreamBridge` keys use a rolling retained-buffer TTL (`stream_bridge.stream_ttl_seconds`, refreshed on `publish()` / `publish_end()`) as a leak safety net, not as a run timeout. Startup orphan recovery publishes `END_SENTINEL` and schedules stream cleanup for recovered runs; do not broaden this into a shared-database multi-pod reaper without adding worker ownership/liveness first.
- Thread-scoped run creation accepts `checkpoint` / `checkpoint_id`; Gateway validates the checkpoint belongs to the request thread before writing `checkpoint_id` / `checkpoint_ns` into `config.configurable` for LangGraph branching.
- Thread-scoped Gateway runs evaluate an active `ThreadState.goal` after the visible turn completes. `runtime/goal.py` asks a non-thinking evaluator model to judge only visible conversation evidence and return a typed blocker; the evaluator model is created once per run and reused across hidden continuation checks. Satisfied goals are cleared; every non-satisfied evaluation — continuable or stand-down — is persisted with `last_evaluation` (the blocker, reason, and evidence summary; outcomes that stop the loop additionally record a `stand_down_reason` for observability), but only `goal_not_met_yet` evaluations are streamed as hidden `HumanMessage` continuations, and only when a durable assistant end-of-turn checkpoint exists, the run has not been aborted, the thread did not change during evaluation, and the no-progress breaker has not fired. The continuation cap is 8 — a hard maximum in the `0`–`8` range; callers requesting more are clamped (`set_goal`/TUI) or rejected with 422 (`PUT /goal`). The no-progress breaker keys on the latest visible assistant evidence (not the evaluator's free-text reason, which an LLM rewords every turn), so two consecutive continuations that add no new visible assistant output stop the loop after 2 attempts. Model-response cleanup helpers such as think-block stripping and code-fence stripping live in `deerflow.utils.llm_text` so `runtime/goal.py` and Gateway suggestion parsing share the same JSON-prep behavior.

Proxied through nginx: `/api/langgraph/*` → Gateway LangGraph-compatible runtime, all other `/api/*` → Gateway REST APIs.

### Sandbox System (`packages/harness/deerflow/sandbox/`)

**Interface**: Abstract `Sandbox` with `execute_command(command, env=None)`, `read_file`, `write_file`, `list_dir`. The optional `env` injects per-call environment variables (request-scoped secrets — see Request-Scoped Secrets below); `LocalSandbox` merges it via `subprocess.run(env=...)` and `AioSandbox` routes env-bearing commands through the `bash.exec(env=...)` API on a fresh session.
**Provider Pattern**: `SandboxProvider` with `acquire`, `acquire_async`, `get`, `release` lifecycle. Async agent/tool paths call async sandbox lifecycle hooks so Docker sandbox creation, discovery, cross-process locking, readiness polling, and release stay off the event loop.
**Environment policy** (`sandbox/env_policy.py`): `execute_command` no longer inherits the full `os.environ`. `build_sandbox_env()` scrubs secret-looking names (`*KEY*`/`*SECRET*`/`*TOKEN*`/`*PASSWORD*`/`*CREDENTIAL*`) from the inherited environment before layering injected request secrets on top, so platform credentials (e.g. `OPENAI_API_KEY`) never leak into skill subprocesses. Benign vars (`PATH`, `HOME`, `LANG`, `VIRTUAL_ENV`, ...) are preserved.
**Implementations**:
- `LocalSandboxProvider` - Local filesystem execution. `acquire(thread_id)` returns a per-thread `LocalSandbox` (id `local:{thread_id}`) whose `path_mappings` resolve `/mnt/user-data/{workspace,uploads,outputs}` and `/mnt/acp-workspace` to that thread's host directories, so the public `Sandbox` API honours the `/mnt/user-data` contract uniformly with AIO. `acquire()` / `acquire(None)` keeps the legacy generic singleton (id `local`) for callers without a thread context. Per-thread sandboxes are held in an LRU cache (default 256 entries) guarded by a `threading.Lock`. Legacy global-custom mounts are gated by the same user-scoped skill discovery rule used for prompt/list visibility; providers must not infer visibility from raw directory presence alone.
- `AioSandboxProvider` (`packages/harness/deerflow/community/`) - Docker-based isolation. Active-cache and warm-pool entries are checked with the backend during acquire/reuse; definitively dead containers are dropped from all in-process maps so the thread can discover or create a fresh sandbox instead of reusing a stale client. Backend health-check failures are treated as unknown, not dead; local discovery likewise treats an unverifiable container as not adoptable and falls through to create rather than failing acquire. `get()` remains an in-memory lookup for event-loop-safe tool paths. Legacy global-custom mounts follow the same shared visibility helper as local and remote providers.
- `BoxliteProvider` (`packages/harness/deerflow/community/boxlite/`) - BoxLite micro-VM isolation. The `boxlite` runtime is optional (`deerflow-harness[boxlite]`) and lazy-imported only when this provider is selected. The provider owns one private asyncio event loop on a daemon thread because BoxLite handles are loop-affine; sync `Sandbox` calls marshal onto that loop with `run_coroutine_threadsafe`.
  Boxes are named deterministically from `user_id:thread_id`, released into an in-process warm pool after each agent turn, and reclaimed only by the same user/thread. Warm-pool health checks use a short explicit timeout and forward that timeout through both BoxLite `exec(timeout=...)` and the private-loop `.result(timeout)` bridge so a hung VM cannot pin the per-thread acquire lock indefinitely.
  `sandbox.replicas` caps active + warm VMs per gateway process; if capacity is exhausted, only warm-pool VMs are evicted. `sandbox.idle_timeout` stops idle warm VMs after the configured seconds. `reset()` is intentionally a lightweight registry clear for `reset_sandbox_provider()` and does not close boxes, stop the idle reaper, or close the private loop; full teardown remains `shutdown()`.


**Shared warm-pool lifecycle:** community sandbox providers that keep released sandboxes alive for fast reuse share `deerflow.community.warm_pool_lifecycle.WarmPoolLifecycleMixin`. The mixin owns the common `DEFAULT_IDLE_TIMEOUT=600`, `IDLE_CHECK_INTERVAL=60`, `DEFAULT_REPLICAS=3`, idle-checker loop, warm-pool expiry, oldest-warm eviction, replica counting, and soft-cap logging. Providers remain responsible for their own active registries, creation/discovery, health checks, and destroy hook (`_destroy_warm_entry`): AIO destroys `SandboxInfo` through its backend; Boxlite closes loop-affine `BoxliteBox` handles. AIO keeps active-idle cleanup outside the mixin and delegates only warm-pool expiry to the shared helper.

**Virtual Path System**:
- Agent sees: `/mnt/user-data/{workspace,uploads,outputs}`, `/mnt/skills`
- Physical: `backend/.deer-flow/users/{user_id}/threads/{thread_id}/user-data/...`, `deer-flow/skills/`
- Translation: `LocalSandboxProvider` builds per-thread `PathMapping`s for the user-data prefixes at acquire time; `tools.py` keeps `replace_virtual_path()` / `replace_virtual_paths_in_command()` as a defense-in-depth layer (and for path validation). AIO has the directories volume-mounted at the same virtual paths inside its container, so both implementations accept `/mnt/user-data/...` natively.
- Detection: `is_local_sandbox()` accepts both `sandbox_id == "local"` (legacy / no-thread) and `sandbox_id.startswith("local:")` (per-thread)

**Sandbox Tools** (in `packages/harness/deerflow/sandbox/tools.py`):
- `bash` - Execute commands with path translation and error handling. For `LocalSandbox` (host bash), POSIX output is captured through bounded pipe-drain threads and stdin is `/dev/null`, so a backgrounded long-lived server (`server &`) returns immediately instead of blocking the turn on an inherited pipe, while unredirected background output is drained without growing anonymous temp files. Commands that read stdin get immediate EOF. The command runs in its own process group with a wall-clock timeout (`sandbox.bash_command_timeout`, default 120s); on timeout the whole group is killed. At the tool boundary, an unquoted background operator is rejected for calculations, scripts, tests, and verification, preventing finite work from escaping timeout/exit-code observation and leaving orphan processes; only an explicit long-lived server may run in the background. The parser does not misclassify `&&`, `2>&1`, `&>`, escaped ampersands, or ampersands inside quotes. See `LocalSandbox.execute_command` / `_run_posix_command`, `_background_execution_error`, and `bash_tool`'s docstring.
- `ls` - Directory listing (tree format, max 2 levels)
- `read_file` - Read file contents with optional line range
- `write_file` - Write/append to files, creates directories; overwrites by default and exposes the `append` argument in the model-facing schema for end-of-file writes; subject to the read-before-write gate when `read_before_write.enabled` (see Middleware Chain)
- `str_replace` - Substring replacement (single or all occurrences); same-path serialization is scoped to `(sandbox.id, path)` so isolated sandboxes do not contend on identical virtual paths inside one process; subject to the read-before-write gate when `read_before_write.enabled` (see Middleware Chain)

### Subagent System (`packages/harness/deerflow/subagents/`)

**Built-in Agents**: `general-purpose` (all tools except `task`) and `bash` (command specialist)
**Execution**: Dual thread pool - `_scheduler_pool` (3 workers) + `_execution_pool` (3 workers)
**Concurrency**: `MAX_CONCURRENT_SUBAGENTS = 3` enforced by `SubagentLimitMiddleware` (truncates excess tool calls in `after_model`); default subagent timeout `subagents.timeout_seconds=1800` (30 min) and built-in `general-purpose` `max_turns=150` (raised from 100/15-min so deep-research subtasks stop hitting `GraphRecursionError` out of the box)
**Flow**: `task()` tool → `SubagentExecutor` → background thread → poll 5s → SSE events → result
**Events**: `task_started`, `task_running`, `task_completed`/`task_failed`/`task_timed_out`
**Guardrail caps & `stop_reason` (#3875 Phase 2)**: three independent axes can end a subagent run early, and all now surface *why* through one additive field rather than a new status enum. **Turn axis**: `recursion_limit` on the subagent `run_config` equals `max_turns`, so exhausting the turn budget raises `GraphRecursionError` from `agent.astream`; `executor.py::_aexecute` catches it specifically (before the generic `except Exception`). **Token axis**: `TokenBudgetMiddleware` is attached per-agent via `build_subagent_runtime_middlewares` from `subagents.token_budget` (default 2,000,000 tokens, warn at 0.7, hard-stop at 1.0 — a backstop against a subagent that burns tokens on trivial work). It does *not* raise: at the hard-stop threshold it strips the in-flight turn's tool calls, forces `finish_reason="stop"`, and lets the run complete naturally with a final answer. **Loop axis**: `LoopDetectionMiddleware` (attached at the same point) catches repeated identical tool-call sets — or one tool *type* called many times with varying args — and its hard-stop likewise strips `tool_calls` and forces a final answer without raising, recording `loop_capped`. Each guard exposes its cap on a per-`run_id` `consume_stop_reason(run_id)` accessor; `_aexecute` collects **every** middleware with that method (duck-typed via `hasattr`, so the executor has no import coupling to the guard classes) and surfaces the first non-`None` reason — adding a future guard needs no executor change. **Surfacing**: whichever axis fired, `_aexecute` stamps a normal status plus an additive reason — `completed` + `stop_reason=token_capped|turn_capped|loop_capped` when a usable final answer (or partial recovered from the last streamed chunk via `_extract_final_result` → `utils/messages.py::message_content_to_text`, returning a `"No response Generated"` sentinel when no text survived) was produced; `failed` + `stop_reason=turn_capped` when nothing usable survived. `SubagentResult.stop_reason` flows through `task_tool.py::_task_result_command` → `format_subagent_result_message` (renders `Task Succeeded (capped: ...)` / `Task failed (capped: ...)`) and `make_subagent_additional_kwargs`, which stamps the additive `subagent_stop_reason` key alongside the normal `subagent_status`. **Why additive, not an enum**: a new status value would break v1 consumers; an optional field is ignored by older frontends and ledger readers, so the cross-language contract (`contracts/subagent_status_contract.json` v2 + `subagents/status_contract.py` + `frontend/.../subtask-result.ts`, pinned by `test_status_values_match_contract` / `test_stop_reason_values_match_contract`) stays backward-compatible. The durable delegation ledger captures `stop_reason` onto the entry and renders model-facing guidance ("hit a guardrail cap with a partial result; reuse it, retry tighter, or raise the per-agent budget (`max_turns` / `token_budget`)") so the lead reuses a capped completion knowingly instead of mistaking it for a clean one. (Phase 1 shipped this surfacing as a `MAX_TURNS_REACHED` status enum in #3949; Phase 2 replaced that enum with the additive `stop_reason` field per the agreed design — the `max_turns_reached` status value and `SubagentStatus.MAX_TURNS_REACHED` are gone.)
**Step capture & persistence (#3779)**: `executor.py` captures both assistant turns (`AIMessage`) **and** tool outputs (`ToolMessage`) via `subagents/step_events.py::capture_new_step_messages`, which walks the *newly-appended tail* of each `stream_mode="values"` chunk (not just `messages[-1]`) so a multi-tool-call turn — where LangGraph's `ToolNode` appends several `ToolMessage`s in one super-step — keeps every tool output instead of dropping all but the last. `runtime/runs/worker.py::_SubagentEventBuffer` additionally persists these `task_*` custom events to the `RunEventStore` as `subagent.start`/`subagent.step`/`subagent.end` (`category="subagent"`, `task_id` in `metadata`). It **batches** writes via `put_batch` (flushing on a terminal `subagent.end`, at `FLUSH_THRESHOLD` events, and in the worker's `finally`) rather than one `put()` per step, since `put()` is a documented low-frequency path (per-thread advisory lock per call) and a deep subagent (`max_turns=150`) emits hundreds of steps on the hot stream loop. `build_subagent_step` caps both the per-step `text` and each tool call's serialized `args` at `SUBAGENT_STEP_MAX_CHARS` (flagged `truncated` / `args_truncated`) so a large `write_file`/`bash` payload can't produce an unbounded row. The dedicated category keeps them out of `list_messages` (the thread feed) while `list_events` returns them for the frontend's fetch-on-expand backfill. `list_events` accepts `task_id` (filters on `metadata["task_id"]` — SQL-side in `DbRunEventStore` via `event_metadata["task_id"].as_string()`, in-memory in the JSONL/memory stores) plus an `after_seq` forward cursor, so the card pages through one subagent's steps without the run-wide `limit` truncating the tail (no schema migration: the filter rides the existing run-scoped index). `step_events.py` is a pure, unit-tested layer (`build_subagent_step` / `subagent_run_event`).
**Deferred MCP tools** (if `tool_search.enabled`): `SubagentExecutor._build_initial_state` assembles deferral after policy filtering via the shared `assemble_deferred_tools` (fail-closed), appends the `tool_search` tool, injects the `<available-deferred-tools>` section into the subagent's `SystemMessage`, and threads the setup to `_create_agent`, which attaches `DeferredToolFilterMiddleware` through `build_subagent_runtime_middlewares(deferred_setup=...)`. Subagents thus withhold full MCP schemas until promotion, same as the lead agent; each task run gets a fresh `ThreadState` so promotion is isolated per run
**Checkpointer isolation**: Subagent graphs are compiled with `checkpointer=False` to avoid inheriting the parent run's checkpointer, since subagents are one-shot and never resume.

### Tool System (`packages/harness/deerflow/tools/`)

`get_available_tools(groups, include_mcp, model_name, subagent_enabled)` assembles:
1. **Config-defined tools** - Resolved from `config.yaml` via `resolve_variable()`
2. **MCP tools** - From enabled MCP servers (lazy initialized, cached with mtime invalidation)
3. **Built-in tools**:
   - `present_files` - Make output files visible to user (only `/mnt/user-data/outputs`)
   - `ask_clarification` - Request clarification (intercepted by ClarificationMiddleware, which preserves text fallback and adds `artifact.human_input` for Web UI Human Input Cards)
   - `view_image` - Read image as base64 (added only if model supports vision)
   - `setup_agent` - Bootstrap-only: persist a brand-new custom agent's `SOUL.md` and `config.yaml`. Bound only when `is_bootstrap=True`.
   - `update_agent` - Custom-agent-only: persist self-updates to the current agent's `SOUL.md` / `config.yaml` from inside a normal chat (partial update + atomic write). Bound when `agent_name` is set and `is_bootstrap=False`.
4. **Subagent tool** (if enabled):
   - `task` - Delegate to subagent (description, prompt, subagent_type)

Scheduled-task runtime note:
- Scheduled background runs set `context.non_interactive=true` and therefore exclude `ask_clarification` from the lead-agent tool list. This keeps scheduler-triggered runs from stalling on human confirmation mid-execution. `non_interactive` is an internal-only context key: it is merged from `body.context` only when the request authenticated as the process-internal user (the scheduler path), never from arbitrary HTTP/IM clients.

**Community tools** (`packages/harness/deerflow/community/`): optional integrations, each in its own subpackage and wired through `config.yaml`. Documented examples:
- `tavily/` - Web search (5 results default) and web fetch (4KB limit)
- `jina_ai/` - Web fetch via Jina reader API with readability extraction
- `firecrawl/` - Web scraping via Firecrawl API
- `image_search/` - Image search via DuckDuckGo
- `aio_sandbox/` - Docker-based isolation (`AioSandboxProvider`)

Additional providers also live here (`boxlite`, `brave`, `browserless`, `crawl4ai`, `ddg_search`, `e2b_sandbox`, `exa`, `fastcrw`, `groundroute`, `infoquest`, `searxng`, `serper`); see each subpackage for specifics.

**ACP agent tools**:
- `invoke_acp_agent` - Invokes external ACP-compatible agents from `config.yaml`
- ACP launchers must be real ACP adapters. The standard `codex` CLI is not ACP-compatible by itself; configure a wrapper such as `npx -y @zed-industries/codex-acp` or an installed `codex-acp` binary
- Missing ACP executables now return an actionable error message instead of a raw `[Errno 2]`
- Each ACP agent uses a per-thread workspace at `{base_dir}/users/{user_id}/threads/{thread_id}/acp-workspace/`. The workspace is accessible to the lead agent via the virtual path `/mnt/acp-workspace/` (read-only). In docker sandbox mode, the directory is volume-mounted into the container at `/mnt/acp-workspace` (read-only); in local sandbox mode, path translation is handled by `tools.py`

### MCP System (`packages/harness/deerflow/mcp/`)

- Uses `langchain-mcp-adapters` `MultiServerMCPClient` for multi-server management
- **Lazy initialization**: Tools loaded on first use via `get_cached_mcp_tools()`
- **Cache invalidation**: Detects config file changes via mtime comparison
- **Transports**: stdio (command-based), SSE, HTTP
- **OAuth (HTTP/SSE)**: Supports token endpoint flows (`client_credentials`, `refresh_token`) with automatic token refresh + Authorization header injection
- **Stdio file outputs**: Persistent stdio sessions are scoped by `user_id:thread_id`. For stdio transports only, DeerFlow pins the subprocess default `cwd` to the thread workspace and `TMPDIR`/`TMP`/`TEMP` to `workspace/.mcp/tmp/`, unless the operator explicitly configured `cwd` or temp env values. SSE/HTTP transports skip this filesystem prep entirely.
- **Stdio path translation**: MCP-returned local file references are not copied. If a `ResourceLink` or conservative free-text path resolves to an existing file inside the thread's mounted user-data tree, it is translated deterministically to `/mnt/user-data/...`; paths outside that tree remain unchanged.
- **Runtime updates**: Gateway API saves to extensions_config.json; the Gateway-embedded runtime detects changes via mtime

### Skills System (`packages/harness/deerflow/skills/`)

- **Location**: `deer-flow/skills/{public,custom}/`
- **Format**: Directory with `SKILL.md` (YAML frontmatter: name, description, license, allowed-tools, required-secrets)
- **Loading**: `load_skills()` recursively scans `skills/{public,custom}` for `SKILL.md`, parses metadata, and reads enabled state from extensions_config.json
- **Injection (legacy / default)**: Enabled skills are listed in the agent system prompt with full metadata and container paths (`<available_skills>` block). Controlled by `skills.deferred_discovery: false` (default).
- **Deferred discovery** (`skills.deferred_discovery: true`): Skills are listed by name only in a compact `<skill_index>` block, keeping the system prompt prefix-cache friendly. The agent calls the `describe_skill` tool at runtime to fetch full metadata for skills it wants to use, then loads the SKILL.md via `read_file`. Two new modules support this path:
  - `skills/catalog.py` — `SkillCatalog` (immutable, searchable; query forms: `select:a,b`, `+prefix`, free-text regex); `select:` returns all requested skills without a result cap; other modes cap at `MAX_RESULTS=5`.
  - `skills/describe.py` — `build_describe_skill_tool(catalog)` builds the `describe_skill` tool as a closure; `build_skill_search_setup(skills, enabled, ...)` produces a `SkillSearchSetup(describe_skill_tool, skill_names)` that is wired into both the LangGraph agent factory (`agent.py`) and the embedded client (`client.py`).
- **Slash activation**: `/skill-name task` loads that enabled skill's `SKILL.md` for the current model call only. The resolver rejects leading whitespace, missing separators, reserved channel commands (`/new`, `/help`, `/bootstrap`, `/status`, `/models`, `/memory`, `/goal`), disabled skills, and skills outside a custom agent's whitelist.
- **Installation**: `POST /api/skills/install` extracts .skill ZIP archive to custom/ directory
- **SkillScan**: `packages/harness/deerflow/skills/skillscan/` is the native deterministic scanner for `.skill` archives and agent-managed skill writes. It runs offline before the LLM scanner, emits structured findings (`rule_id`, `severity`, `file`, `line`, `message`, `remediation`, redacted `evidence` — category/analyzer are encoded in the `rule_id` prefix), blocks `CRITICAL`, and passes warning findings into `scan_skill_content()`. `scan_archive_preflight()` / `scan_skill_dir()` are pure sync functions (dispatch off the event loop); `enforce_static_scan()` applies the blocking policy and the `skill_scan.enabled` kill switch. Do not add Semgrep/OpenGrep or YAML rule-engine dependencies to the core path; Phase 1 rule specs live in Python constants next to their analyzers in `skillscan/orchestrator.py`.

#### Request-Scoped Secrets (`required-secrets`)

Lets a caller pass per-request, short-lived end-user credentials (e.g. an ERP token) to a skill's sandbox scripts without the value entering the prompt, tool arguments, the executed command string, or traces (issue #3861).

- **Declare**: a skill lists the secrets it needs in `SKILL.md` frontmatter — `required-secrets:` as a string list or `{name, optional}` mappings. `name` is both the lookup key and the env var name exposed to scripts. Parsed by `skills/parser.py::parse_required_secrets` into `Skill.required_secrets` (`SecretRequirement`); malformed entries are dropped with a warning.
- **Carry**: the caller sends values out-of-band in the run request's `context.secrets` mapping (never a message). `runtime/secret_context.py` owns the contract (`SECRETS_CONTEXT_KEY`, `extract_request_secrets`). The existing `context` passthrough carries it to `runtime.context` without mirroring into `configurable`. `build_run_config` still sets `configurable.thread_id` on the context path — the checkpointer requires it.
- **Bind (point A+)**: `SkillActivationMiddleware._resolve_secret_bindings` recomputes the injection set (`runtime.context[__active_skill_secrets]`) on every model call from two unioned sources, then REPLACES the key. (1) *Slash*: the run's most recent `/skill` activation, persisted as a source on the run context (only the activated skill's **canonical container path**, never its declared secrets) so the whole tool loop after the activation call keeps the binding; a new activation replaces it. Slash reads the genuine user text via `get_original_user_content_text`; `InputSanitizationMiddleware` preserves it (`ORIGINAL_USER_CONTENT_KEY`), so activation fires even after sanitization. (2) *In-context* (autonomous invocation): skills the model actually loaded in this thread — `ThreadState.skill_context` entries. **Both sources resolve the live registry skill by normalized container path on every call** (`_resolve_registry_skill`) and bind only that skill's own declared secrets — enabled + allowlist checked for both; the `secrets-autonomous: false` opt-out (malformed values fail closed to `false`) additionally gates the in-context path but exempts explicit slash. Resolving by registry — not by trusting the source's stored data — is what makes a caller-forged `__slash_skill_secret_source` harmless (`runtime.context` is caller-mergeable; the gateway also strips caller `__`-keys in `build_run_config`), #3938. Authorization is three-gated regardless of activation style: skill **enabled** by the operator × values **supplied per-request** by the caller (`context.secrets`) × names **declared** in frontmatter (∩ semantics). Because the set is recomputed per call, a skill evicted from `skill_context` (capacity) or a caller that stops supplying a value loses injection on the next call. The injected value always comes from the caller's request, never the host environment (scrubbed first — see below), so a declared name that also exists in the host env is safe: the caller's value wins and the host value is dropped (the #3861 per-user-key-overrides-shared-key case). Missing required secrets are logged once per binding change, not injected; binding changes are recorded as a `middleware:skill_secrets` journal event (skill and secret names only, never values).
- **Inject**: `bash_tool` reads the injection set and passes it as `execute_command(env=...)`. Scope is the activation turn/run only — a run without `/skill` activation injects nothing.
- **AIO image requirement**: on `AioSandbox` the env path uses the `bash.exec` API (`POST /v1/bash/exec`), which upstream all-in-one-sandbox only ships since `1.9.3` — older images (including a `latest` tag frozen on the `1.0.0.x` line) 404 the whole `/v1/bash/*` namespace. `AioSandbox` detects the 404, remembers the capability gap on the instance, and fails fast with an actionable upgrade error instead of letting the model retry raw 404s; there is deliberately **no** fallback through the legacy shell path because none keeps the secret values out of the command string (#3921). Regression tests: `tests/test_aio_sandbox.py::TestBashExecUnsupportedFailFast`.
- **Inherited-env scrub**: `execute_command` no longer leaks the Gateway's `os.environ` to skill subprocesses — `env_policy.build_sandbox_env` drops secret-looking names (`*KEY*`/`*SECRET*`/`*TOKEN*`/`*PASSWORD*`/`*CREDENTIAL*`/`*DSN*` + a connection-string denylist like `DATABASE_URL`/`REDIS_URL`/`GH_PAT`) so platform credentials never reach a skill; a skill that needs one must declare it.
- **Leak surfaces sealed** (verified by a real-gateway e2e run — secret reaches the sandbox but none of these): prompt (value never in a message), trace (`tracing/metadata.py` never copies `context`), checkpoint (secrets live on `runtime.context`, not graph state), audit (journal records names only), stdout (`tools.py::mask_secret_values` redacts injected values from bash output), and **run-record persistence + run API** (`services.py::start_run` stores `redact_config_secrets(body.config)` so `runs.kwargs_json` and `RunResponse.kwargs` never carry the secret).
- **Scope / non-goals**: no persistence/vaulting — values are request-scoped and never stored server-side, so long-lived use means the caller re-supplies `context.secrets` on each request while the skill stays in `skill_context`; subagents do not inherit the injection set; the MCP per-user-credential gap (#3322) is a sibling, not covered here. Tests: `tests/test_skill_request_scoped_secrets.py`.

### Model Factory (`packages/harness/deerflow/models/factory.py`)

- `create_chat_model(name, thinking_enabled)` instantiates LLM from config via reflection
- Supports `thinking_enabled` flag with per-model `when_thinking_enabled` overrides
- Supports vLLM-style thinking toggles via `when_thinking_enabled.extra_body.chat_template_kwargs.enable_thinking` for Qwen reasoning models, while normalizing legacy `thinking` configs for backward compatibility
- Supports `supports_vision` flag for image understanding models
- Config values starting with `$` resolved as environment variables
- Missing provider modules surface actionable install hints from reflection resolvers (for example `uv add langchain-google-genai`)

### vLLM Provider (`packages/harness/deerflow/models/vllm_provider.py`)

- `VllmChatModel` subclasses `langchain_openai:ChatOpenAI` for vLLM 0.19.0 OpenAI-compatible endpoints
- The native `sp-memory-recaller` middleware chain pairs `DynamicContextMiddleware` with the existing `SystemMessageCoalescingMiddleware`, as the lead agent does. Its role prompt and framework date reminder become one leading SystemMessage only in the model request; persistent messages and user-owned memory retain their original roles. This avoids Qwen's `System message must be at the beginning` error without changing SP scheduling or provider code. Sync/async graph regression coverage lives in `tests/test_tool_error_handling_middleware.py`.
- Preserves vLLM's non-standard assistant `reasoning` field on full responses, streaming deltas, and follow-up tool-call turns
- Recovers Qwen chat-template `<tool_call>{...}</tool_call>` blocks from content or reasoning into LangChain tool calls when the server lacks a compatible auto-tool parser. Tool-enabled streaming uses one bounded non-streaming provider request and returns a normal `AIMessageChunk`, while calls without tools keep native streaming.
- Designed for configs that enable thinking through `extra_body.chat_template_kwargs.enable_thinking` on vLLM 0.19.0 Qwen reasoning models, while accepting the older `thinking` alias
- `scripts/evaluate_sp_qwen_actions.py --async-mode` runs five live CentralAgent policy scenarios through the same async streaming shape used by Gateway; use `--thinking` and optional repeated `--scenario NAME` filters for focused compatibility checks.

### Trusted SP execution extensions and tender-audit experiment

`make_sp_agent(..., extensions=SPExecutionExtensions(...))` optionally supplies per-graph tools, a task preparation hook, a specialist-config copy hook, and additional callbacks. The hooks are trusted Python arguments, never deserialized from API context. Preparation runs before task rendering; specialist configuration runs before native tool/stage enforcement. `SubagentExecutor(callbacks=...)` appends callbacks to its native usage collector rather than replacing it. Omitting extensions retains the default tool loading, role prompts, skills and scheduler. Optional `subagent_middlewares(task)` supplies trusted per-task middleware instances, appended after the native chain through `SubagentExecutor(extra_middlewares=...)`; the default is absent and no API context can supply it. The audit experiment uses it only on Researcher to project duplicate full sources into a same-request evidence dictionary with exact per-occurrence metadata deltas. Persistent messages, native retries/actions and raw retrieval logs remain unchanged; callbacks observe the projected request, while `model_request_projections.jsonl` records both views. Its copied model config merges `Connection: close` into existing headers, and worker probes verify the actual SDK header without logging other headers. The optional trusted `SPExecutionExtensions.skill_names` narrows both the Central index and its capability metadata, defaulting to the existing profile when absent. Experiment-only delegation projections alias identical request/task fields without changing native task objects, pinned feedback or AIMessage reasoning. Evidence v2 factors common source metadata and sparse per-hit deltas; every source field/body is reconstructible. The CLI timeout multiplier scales config deadlines, native Central/subagent limits, HTTP/RAG/preflight/watchdog/case/worker-grace limits and the SDK stream timer. A default-one generic `DEER_FLOW_RUNTIME_TIMEOUT_MULTIPLIER` scales infrastructure startup/shutdown, Skill cache warm-up and dynamic-context injection waits only in the opt-in process; polling intervals and retry/token budgets stay unchanged. Parent and worker preserve a base stream timer to avoid applying the multiplier twice.

`app/experiments/tender_audit/` owns the opt-in business profile; `scripts/run_tender_audit_sp2.py` is its CLI. It injects complete rules and a shared output contract into Central's request and every delegation, binds `local_search_tool` to one dataset, and overrides the Reporter format/skills on copied configs. Reporter has case-local read tools and returns an object for native JSON serialization. Evidence packets deduplicate by chunk/document/content before budgeting; omitted sources remain available through case-local hashed read paths. The JSON document remains a native `report_revision` Artifact and must be the exact current-run Artifact accepted by FINISH. Policy `json_schema_only_v2` validates JSON/schema/case ID without repairing output or matching evidence to chunks; it explicitly records `evidence_evaluated=false` and `evidence_evaluation=offline_only`. Evidence correctness and GT recall are assessed offline, separately from delivery. SDK retries are disabled only in the experiment config copy (request timeout 120s); native middleware retries, thinking, scheduling and case budgets remain intact. Revalidate the experiment copy with `AppConfig.model_validate` after changing model/tool lists: `model_copy` and assignments do not refresh the private name indexes consumed by the native factory. Test the actual model/client settings, not only `config.models`. HTTP timeouts are not whole-invocation deadlines. The CLI scopes `DEER_FLOW_LLM_REQUEST_TIMEOUT_SECONDS` to its process with `--llm-request-timeout` (default150s,0 disables), validates it is below the progress watchdog, and forwards it explicitly to workers. Preflight and worker snapshots verify actual factory/client settings without extra provider calls. Audit callbacks retain bounded, redacted exception cause chains and distinguish provider callbacks from synthetic run cleanup, including empty-message cancellations. `canonical_content_source_metadata_v1` removes derived token indexes/empty metadata from tool messages and Reporter packets/caches; full bodies, source identifiers, positions and table lineage remain, with every raw response preserved. Native scheduling, retry budgets, prompts and vLLM fallback stay unchanged. A historical Qwen13 batch exposed this mismatch and records its actual SDK retry count separately. Explicit Central `metadata.skill_names` may still select a Skill despite copied default `skills=[]`; the audit format override remains authoritative, and such selections are retained in traces. Every attempt uses a separate process/runtime directory under the SP2 tree; retries preserve earlier costs and files. Model callbacks deduplicate by call ID and redact credentials/private reasoning. Preflight costs are separate from case metrics. See [the experiment guide](../docs/SP2_TENDER_AUDIT_LIGHTWEIGHT.md) and `tests/test_tender_audit_experiment.py`, `tests/test_tender_audit_delivery_v2.py`, `tests/test_sp_execution_extensions.py`.

Native report acceptance detects explicit missing-data assertions rather than phrases such as `无需询问数据来源`, invalid-bid clauses, or conditional `if data were not provided` instructions. Long-report diagnostics retain the actual triggering comparison phrase instead of the JSON header. The recovery hint selects JSON only for artifacts declared with a `.json` filename/path; default Markdown recovery, the three-attempt cap and FINISH checks remain unchanged. Regression coverage includes legitimate no-data rejection and bound-data JSON acceptance in `tests/test_sp_action_system.py`.

The audit preparation hook narrows both `task.allowed_tools` and `metadata.tool_names` before native runtime enforcement: DelegateHandler automatically adds Reporter read/write tools, while this experiment intentionally supplies a read-only Reporter copy. Keeping both A2A representations aligned prevents repeated forbidden-write delegations; the default native role and allowlist enforcement remain unchanged. `test_native_reporter_tool_defaults_are_narrowed_before_runtime_enforcement` covers the real executor-provider boundary with native default tool names.

### IM Channels System (`app/channels/`)

Bridges external messaging platforms (Feishu, Slack, Telegram, Discord, DingTalk, GitHub) to the DeerFlow agent via Gateway's LangGraph-compatible API.

**Architecture**: Channels communicate with Gateway through the `langgraph-sdk` HTTP client (same as the frontend), ensuring threads are created and managed server-side. The internal SDK client injects process-local internal auth plus a matching CSRF cookie/header pair so Gateway accepts state-changing thread/run requests from channel workers without relying on browser session cookies.

**Components**:
- `message_bus.py` - Async pub/sub hub (`InboundMessage` → queue → dispatcher; `OutboundMessage` → callbacks → channels)
- `store.py` - JSON-file persistence mapping `channel_name:chat_id[:topic_id]` → `thread_id` (keys are `channel:chat` for root conversations and `channel:chat:topic` for threaded conversations)
- `manager.py` - Core dispatcher: creates threads via `client.threads.create()`, routes commands including `/goal` (setting a goal persists it through Gateway and then routes the objective as a chat turn), keeps Slack/Discord on `client.runs.wait()`, uses `client.runs.stream(["messages-tuple", "values"])` for Feishu/Telegram incremental outbound updates, and switches to `client.runs.create()` (fire-and-forget, returns once the run is `pending`) for channels whose `ChannelRunPolicy.fire_and_forget=True` so long autonomous runs do not hit the SDK default 300s `httpx.ReadTimeout`
- `base.py` - Abstract `Channel` base class (start/stop/send lifecycle)
- `service.py` - Manages lifecycle of all configured channels from `config.yaml`
- `slack.py` / `feishu.py` / `telegram.py` / `discord.py` / `dingtalk.py` - Platform-specific implementations (`feishu.py` tracks the running card `message_id` in memory and patches the same card in place; `telegram.py` registers the "Working on it..." placeholder as the stream target and edits it in place via `editMessageText`; `dingtalk.py` optionally uses AI Card streaming for in-place updates when `card_template_id` is configured)
- `github.py` - Webhook-driven GitHub channel. Inbound messages come from `POST /api/webhooks/github`; outbound is log-only because GitHub agents post explicitly with `gh` from their sandbox when they choose to comment or create a PR
- `app/gateway/routers/channel_connections.py` - Browser-facing user connection and disconnect APIs
- `deerflow.persistence.channel_connections` - SQL-backed user-owned connection, optional credential, connect state, and conversation store

**Message Flow**:
1. External platform -> Channel impl -> `MessageBus.publish_inbound()`
   - For GitHub, the webhook router verifies the delivery then calls `fanout_event(bus, ...)`; matching agent bindings publish one `InboundMessage` each instead of a long-polling channel worker.
2. `ChannelManager._dispatch_loop()` consumes from queue
3. For user-owned channel connections, incoming messages carry `connection_id`, `owner_user_id`, and `workspace_id`; `owner_user_id` becomes the DeerFlow run `user_id`, while the raw platform user id remains `channel_user_id`. The Gateway forwards `channel_user_id` from `body.context` into the runtime context only (never `configurable`, which is checkpointed), and `bash_tool` exposes it to sandbox commands as the fixed env var `DEERFLOW_CHANNEL_USER_ID` — via a shell-quoted command-string prefix, NOT the `execute_command(env=...)` channel, which is reserved for request-scoped secrets and would switch `AioSandbox` onto the `bash.exec` path (image >= 1.9.3, fresh session per call). Per-call injection keeps group-chat identity correct (one thread/sandbox, many senders) **without depending on the AIO shell's session semantics**: every IM-channel command carries an explicit `export VAR=<id>; ` (valid id) or `unset VAR; ` (empty / non-str / over the 256-char cap, since `body.context` is client-writable). The AIO no-env path reuses a persistent shell session (the reason for the class lock, #1433), so a bare command could otherwise resolve a stale id an earlier sender exported; the `unset` closes the window the length/type guard would open (a dropped id would inherit the previous sender's value). Non-IM runs (no `channel_user_id` in context) are left untouched. Not injected on the Windows local sandbox (its PowerShell/cmd.exe fallback has no `export`/`unset`). Propagates across `task` delegation: `task_tool` captures the dispatching turn's id and the subagent executor forwards it into the subagent's runtime context, same as the guardrail attribution fields. The var is informational, never authorization-grade: any bash command can overwrite it (and web clients can set `body.context.channel_user_id`), so skills must not treat it as authenticated identity. Tests: `tests/test_channel_user_id_env.py`
4. For chat: look up/create thread through Gateway's LangGraph-compatible API
5. Feishu/Telegram chat: `runs.stream()` → accumulate AI text → publish multiple outbound updates (`is_final=False`) → publish final outbound (`is_final=True`)
6. Slack/Discord chat: `runs.wait()` → extract final response → publish outbound
6b. GitHub chat (`ChannelRunPolicy.fire_and_forget=True`): `runs.create()` returns once the run is `pending`; the manager does not wait for the final state and does not publish an outbound. The agent posts its own reply mid-run via `gh` from the sandbox. `ConflictError` on a busy thread still trips the standard `THREAD_BUSY_MESSAGE` path (log-only on GitHub).
7. Feishu channel sends one running reply card up front, then patches the same card for each outbound update (card JSON sets `config.update_multi=true` for Feishu's patch API requirement)
8. Telegram streaming: the "Working on it..." placeholder message is registered as the stream target; non-final updates `editMessageText` it in place (channel-side throttle: 1s in private chats, 3s in groups due to Telegram's 20 msg/min group cap; 4096-char truncation; rate-limited updates dropped); the final update performs the last edit and splits >4096 texts into follow-up messages
9. DingTalk AI Card mode (when `card_template_id` configured): `runs.stream()` → create card with initial text → stream updates via `PUT /v1.0/card/streaming` → finalize on `is_final=True`. Falls back to `sampleMarkdown` if card creation or streaming fails
10. For commands (`/new`, `/status`, `/models`, `/memory`, `/goal`, `/help`): handle locally or query Gateway API
11. Outbound → channel callbacks → platform reply
    - GitHub is the exception: the channel logs the final assistant message and does **not** auto-post it to GitHub. Agents use the sandbox `gh` CLI (`gh issue comment`, `gh pr comment`, `gh pr create`, etc.) for intentional writeback, so silence is cheap when several agents fan out on the same event.

**Owner-scoped file storage**: inbound files, uploads, and output artifacts are staged under the DeerFlow owner's bucket so they land where the agent run reads/writes (`users/{user_id}/threads/{thread_id}/user-data/{uploads,outputs}`). `ChannelManager._handle_chat` resolves the storage owner once via `_channel_storage_user_id(msg)` (sanitized owner id, falling back to `safe(msg.user_id)` for unbound auth-enabled channels — mirroring `_resolve_run_params`'s run identity; `None` only when no identity is available) and threads it as the `user_id=` kwarg through the file pipeline:
- `Channel.receive_file(msg, thread_id, user_id=...)` — owner-bound channels persist downloaded files under the owner's bucket instead of the default bucket
- `_ingest_inbound_files(...)` and the underlying `ensure_uploads_dir` / `get_uploads_dir` — owner-scoped via the same kwarg
- `_resolve_attachments` / `_prepare_artifact_delivery` — resolve output artifacts from the bound owner's bucket
The cached value is reused for both the blocking (`runs.wait`) and streaming (`_handle_streaming_chat`) paths, so uploads and artifact delivery always target the same bucket even if a channel returns a rewritten `InboundMessage` from `receive_file`. The bucket id matches the memory bucket resolved by `_resolve_memory_user_id` (both normalize through `make_safe_user_id`).

**Configuration** (`config.yaml` -> `channels`):
- `langgraph_url` - LangGraph-compatible Gateway API base URL (default: `http://localhost:8001/api`)
- `gateway_url` - Gateway API URL for auxiliary commands (default: `http://localhost:8001`)
- In Docker Compose, IM channels run inside the `gateway` container, so `localhost` points back to that container. Use `http://gateway:8001/api` for `langgraph_url` and `http://gateway:8001` for `gateway_url`, or set `DEER_FLOW_CHANNELS_LANGGRAPH_URL` / `DEER_FLOW_CHANNELS_GATEWAY_URL`.
- Per-channel configs: `feishu` (app_id, app_secret), `slack` (bot_token, app_token), `telegram` (bot_token), `dingtalk` (client_id, client_secret, optional `card_template_id` for AI Card streaming), `github` (operator kill-switch `enabled`, plus `default_mention_login` for mention-required GitHub triggers)

**User-owned channel connections** (`config.yaml` -> `channel_connections`):
- Disabled by default. It is a user-binding layer on top of the existing `channels.*` runtime config, not a replacement for provider bot credentials.
- No public IP, OAuth callback URL, or provider webhook route is required by the current implementation.
- Telegram uses a deep-link `/start <code>` flow over the existing long-polling worker. Slack, Discord, Feishu/Lark, DingTalk, WeChat, and WeCom use `/connect <code>` over their existing outbound channel workers.
- Frontend APIs: `GET /api/channels/providers`, `GET /api/channels/connections`, `POST /api/channels/{provider}/connect`, and `DELETE /api/channels/connections/{connection_id}`.
- Browser APIs remain protected by normal Gateway auth/CSRF. Provider messages arrive through the already-configured channel workers.
- Provider-level `connection_status` reflects the user's newest connection row. With no binding it is `not_connected`, except in auth-disabled local mode where a configured running channel reports `connected` because all channel messages already route to the default user.
- Slack replies use the configured operator bot token from `channels.slack` unless per-connection credentials are present; unreadable or corrupt stored credentials are treated as unavailable.
- Telegram, Slack, Discord, Feishu/Lark, DingTalk, WeChat, and WeCom workers resolve incoming platform identities to connection records before reaching `ChannelManager`.
- **Connect-code ordering vs `allowed_users`**: inbound workers consume a valid `/connect <code>` (or Telegram `/start <code>`) **before** applying the `allowed_users` filter, so a newly allowlisted-but-unbound user can bootstrap their first bind via the browser flow. Consequence: `allowed_users` is **not** a bind-time defense — any sender who possesses a valid code can consume it (not only allowlisted users). The bind security model rests on the code's confidentiality: `secrets.token_urlsafe(16)`, 600 s TTL, one-time `consume_oauth_state`, and codes surfaced only in the initiating browser (never echoed to chat). `allowed_users` still gates ordinary (non-bind) messages.
- **Single-active-owner transfer semantics**: an external identity is keyed by `(provider, external_account_id, workspace_id)`. The latest successful bind wins — `upsert_connection` revokes other owners' active rows for the same identity (ownership transfer). This invariant is enforced at the DB layer by the partial unique index `uq_channel_connection_active_identity` (`WHERE status != 'revoked'`), so concurrent connects from different owners cannot both end `connected`; the losing writer retries against the now-visible state. `find_connection_by_external_identity` therefore resolves deterministically.
- See `backend/docs/IM_CHANNEL_CONNECTIONS.md` for provider setup and operational notes.

**GitHub event-driven agents**:
- Configure agent-level bindings in a custom agent's `config.yaml` under `github:`. The global `config.yaml` `channels.github` block is only for the operator kill-switch (`enabled`) and the default mention login; per-agent `installation_id`, `bot_login`, repo bindings, and triggers live with the custom agent.
- Bindings are opt-in by event. `DEFAULT_TRIGGERS` only supplies per-event field defaults for events a binding declared. `GitHubAgentConfig` enforces a single binding per repo per agent; merge trigger maps instead of duplicating a repo.
- Threading is deterministic: fan-out sets `metadata["preferred_thread_id"]` from UUID5 over `(repo, PR/issue number, agent_name)`, and `ChannelManager._create_thread` passes it to `client.threads.create(thread_id=...)`. Different agents on the same PR intentionally get different LangGraph threads. ChannelStore uses `topic_id = f"{number}:{agent_name}"` so each agent's cached mapping is independent.
- Thread-create race recovery is narrow by design: only `langgraph_sdk.errors.ConflictError` (HTTP 409) is treated as a concurrent-create collision and followed by `threads.get(preferred_thread_id)` verification. Other create failures propagate so the delivery can fail/retry rather than caching an unverified mapping.
- Mention-handle precedence for `require_mention` triggers is `trigger.mention_login` → `github.bot_login` → `channels.github.default_mention_login` → `agent.name`. Whitespace-only defaults are treated as unset.
- Set `GITHUB_APP_ID` and `GITHUB_APP_PRIVATE_KEY_PATH` (or `GITHUB_APP_PRIVATE_KEY`) to enable installation-token minting. `ChannelManager` mints a short-lived installation token from the binding's `installation_id` on the bus-consumer side and passes the token string in `run_context["github_token"]`; the bash tool exposes it to sandbox commands as `GH_TOKEN` / `GITHUB_TOKEN` via per-call `extra_env`. No global `os.environ` mutation is used, so concurrent GitHub runs for different repos do not clobber each other.
- Tokens are not auto-refreshed past GitHub's 1h TTL. Long-running agents may need to finish GitHub writes before expiry until refresh is reintroduced. If minting fails, the agent still runs without push/write credentials.


### Memory System (`packages/harness/deerflow/agents/memory/`)

**Components**:
- `updater.py` - LLM-based memory updates with fact extraction, whitespace-normalized fact deduplication (trims leading/trailing whitespace before comparing), and atomic file I/O
- `queue.py` - Debounced update queue (per-thread deduplication, configurable wait time); captures `user_id` at enqueue time so it survives the `threading.Timer` boundary
- `prompt.py` - Prompt templates for memory updates
- `storage.py` - File-based storage with per-user isolation; cache keyed by `(user_id, agent_name)` tuple

**Per-User Isolation**:
- Memory is stored per-user at `{base_dir}/users/{user_id}/memory.json`
- Per-agent per-user memory at `{base_dir}/users/{user_id}/agents/{agent_name}/memory.json`
- Custom agent definitions (`SOUL.md` + `config.yaml`) are also per-user at `{base_dir}/users/{user_id}/agents/{agent_name}/`. The legacy shared layout `{base_dir}/agents/{agent_name}/` remains read-only fallback for unmigrated installations
- `user_id` is resolved via `get_effective_user_id()` from `deerflow.runtime.user_context`
- The `/api/memory*` endpoints resolve the owner through `_resolve_memory_user_id(request)`: trusted internal callers (IM channel workers carrying the `X-DeerFlow-Owner-User-Id` header, e.g. a bound `/memory` command) act for the connection owner; browser/API callers fall back to `get_effective_user_id()`. The header is only honored after `AuthMiddleware` validated the internal token, mirroring `get_trusted_internal_owner_user_id` used by the threads router
- In no-auth mode, `user_id` defaults to `"default"` (constant `DEFAULT_USER_ID`)
- Absolute `storage_path` in config opts out of per-user isolation
- **Migration**: Run `PYTHONPATH=. python scripts/migrate_user_isolation.py` to move legacy `memory.json`, `threads/`, and `agents/` into per-user layout. Supports `--dry-run` (preview changes) and `--user-id USER_ID` (assign unowned legacy data to a user, defaults to `default`).

**Data Structure** (stored in `{base_dir}/users/{user_id}/memory.json`):
- **User Context**: `workContext`, `personalContext`, `topOfMind` (1-3 sentence summaries)
- **History**: `recentMonths`, `earlierContext`, `longTermBackground`
- **Facts**: Discrete facts with `id`, `content`, `category` (preference/knowledge/context/behavior/goal), `confidence` (0-1), `createdAt`, `source`

**Workflow**:
1. `MemoryMiddleware` filters messages (user inputs + final AI responses), captures `user_id` via `get_effective_user_id()`, and queues conversation with the captured `user_id`
2. Queue debounces (30s default), batches updates, deduplicates per-thread
3. Background thread invokes LLM to extract context updates and facts, using the stored `user_id` (not the contextvar, which is unavailable on timer threads)
4. Applies updates atomically (temp file + rename) with cache invalidation, skipping duplicate fact content before append
5. **Staleness pass** (same LLM invocation as step 3, no extra API call): when `staleness_review_enabled` is `true` and at least `staleness_min_candidates` aged facts exist, `_select_stale_candidates` selects facts older than `staleness_age_days` that are not in `staleness_protected_categories` (default: `correction`), surfaces them in the prompt, and the LLM judges each as KEEP or REMOVE. `_apply_updates` enforces the guardrail unconditionally at apply time: it intersects the LLM-returned removal set with `_select_stale_candidates` output before applying the per-cycle cap (`staleness_max_removals_per_cycle`), so protected and non-aged facts can never be deleted regardless of model behavior or the feature flag setting.
6. Next interaction injects top 15 facts + context into `<memory>` tags in system prompt

**Token counting** (`packages/harness/deerflow/agents/memory/prompt.py`):
- `_count_tokens` budgets the injection. In default `tiktoken` mode, the encoding is loaded lazily and cached.
- Failed tiktoken loads are cached with a timestamp. During the fixed cooldown (`_TIKTOKEN_RETRY_COOLDOWN_S`, 600s), callers fall back to char estimation immediately instead of re-triggering the blocking BPE download; after the cooldown, transient outages can self-heal without a restart.
- In-flight loads are cached as a LOADING sentinel so concurrent callers fall back instead of spawning more blocking threads.
- Set `memory.token_counting: char` to skip tiktoken entirely and use the network-free CJK-aware char estimate.

Focused regression coverage for the updater lives in `backend/tests/test_memory_updater.py`.

**Configuration** (`config.yaml` → `memory`):
- `enabled` / `injection_enabled` - Master switches
- `storage_path` - Path to memory.json (absolute path opts out of per-user isolation)
- `debounce_seconds` - Wait time before processing (default: 30)
- `model_name` - LLM for updates (null = default model)
- `max_facts` / `fact_confidence_threshold` - Fact storage limits (100 / 0.7)
- `max_injection_tokens` - Token limit for prompt injection (2000)
- `token_counting` - Token counting strategy for the injection budget: `tiktoken` (default, accurate but may download BPE data from a public endpoint on first use — can block for a long time in network-restricted environments, see issues #3402/#3429) or `char` (network-free CJK-aware char estimate, never touches tiktoken)
- `staleness_review_enabled` - Enable proactive staleness pruning of aged facts (default: `true`; only triggers when aged candidates exist)
- `staleness_age_days` - Age in days before a fact becomes a staleness candidate (default: 180; range: 1–3650)
- `staleness_min_candidates` - Minimum aged candidates required to trigger a review cycle (default: 3; range: 1–50)
- `staleness_max_removals_per_cycle` - Maximum facts removed in a single cycle; lowest-confidence entries are kept when the LLM requests more (default: 5; range: 1–20)
- `staleness_protected_categories` - Fact categories that are never pruned by staleness review (default: `["correction"]`)

### Reflection System (`packages/harness/deerflow/reflection/`)

- `resolve_variable(path)` - Import module and return variable (e.g., `module.path:variable_name`)
- `resolve_class(path, base_class)` - Import and validate class against base class

### Schema Migrations (`packages/harness/deerflow/persistence/migrations/`)

DeerFlow's application tables (`runs`, `threads_meta`, `feedback`, `users`, `run_events`, plus the four `channel_*` tables) are owned by alembic via a **hybrid bootstrap** strategy. LangGraph's checkpointer tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) live in the same database but are owned by LangGraph and excluded from alembic's view via `migrations/_env_filters.py::include_object`.

**Convention**: every ORM model change (new column, new table, new index) MUST ship as an alembic revision under `migrations/versions/`. The Gateway runs `alembic upgrade head` automatically on startup; users do not run `alembic` manually in production.

**Hybrid bootstrap** (`persistence/bootstrap.py::bootstrap_schema`, invoked from `persistence/engine.py::init_engine`):

| DB state                                  | Action                                  |
|-------------------------------------------|-----------------------------------------|
| empty (no DeerFlow tables)                | `create_all` + `alembic stamp head`     |
| legacy (DeerFlow tables, no `alembic_version`) | `create_all` (baseline tables only, backfill) + `alembic stamp 0001_baseline` + `upgrade head` |
| versioned (`alembic_version` row exists)  | `alembic upgrade head`                  |

The legacy branch handles pre-alembic databases that already have at least one DeerFlow-owned table. `create_all` runs first because stamping at `0001_baseline` makes alembic skip the baseline's own `create_table` DDL on the subsequent upgrade — so any baseline table introduced into `Base.metadata` after the user's DB was first provisioned (e.g. the `channel_*` tables from PR #1930 for users upgrading across multiple releases) would otherwise never be created, and the first request hitting that table would 500 with `no such table`. The backfill is **restricted to `_BASELINE_TABLE_NAMES`** so it does not also create tables that future revisions introduce — those revisions' own `op.create_table` would otherwise fail with `relation already exists`. A guard test pins `_BASELINE_TABLE_NAMES` against `0001_baseline.upgrade()`'s actual output, so editing 0001 to add or remove a table forces a matching update to the constant. Column-level shape (pre-#3658 vs post-#3658 vs manual-ALTER for `token_usage_by_model`) is answered by each `versions/*.py` revision via the idempotent helpers in `migrations/_helpers.py` (`safe_add_column` / `safe_drop_column`) which no-op when the change is already present and `logger.warning` on shape drift. **Adding a new ORM column / table only requires a new revision file — no edit to `bootstrap.py` is needed** *unless* the new revision adds a new baseline table (rare; only happens when a new model is part of the baseline rather than introduced by its own revision).

The empty-DB path keeps using `create_all` because `Base.metadata` is the only authoritative schema source — `create_all` renders both SQLite (JSON, type affinity) and Postgres (JSONB, partial indexes) correctly without anyone having to keep a hand-written baseline in lockstep. `0001_baseline.upgrade()` is therefore almost never executed in practice; it exists as a stamp target + chain root.

**Concurrency safety**: Postgres uses `pg_advisory_lock` to serialise concurrent Gateway instances. SQLite uses a per-engine `asyncio.Lock` for same-process startup and is best-effort across processes via SQLite's file-level write lock + `PRAGMA busy_timeout`; multi-instance deployments should use Postgres. Column revisions in `versions/` additionally use idempotent helpers (`_helpers.py::safe_add_column`, `safe_drop_column`) so repeated post-baseline changes and retries are no-ops when the change is already present.

**Authoring a new revision**:
```bash
cd backend && make migrate-rev MSG="add foo column to runs"
```
This invokes `alembic revision --autogenerate` against the live ORM models. Review the generated file under `migrations/versions/` and switch raw `op.add_column` / `op.drop_column` calls to the idempotent helpers from `_helpers.py` before committing. There is no `make migrate` / `make migrate-stamp` target on purpose — the only execution path is Gateway startup, which keeps operational mistakes off the table.

**Where things live**:
- `migrations/env.py` — alembic env, delegates filter to `_env_filters.py`, sets `render_as_batch=True` for SQLite ALTER support
- `migrations/_env_filters.py::include_object` — drops LangGraph checkpointer tables from alembic's view
- `migrations/_helpers.py` — `safe_add_column` / `safe_drop_column`
- `migrations/versions/0001_baseline.py` — chain root, matches the schema `create_all` produces from `Base.metadata`
- `migrations/versions/0002_runs_token_usage.py` — fixes issue #3682
- `persistence/bootstrap.py` — `bootstrap_schema(engine, backend=...)`, the three-branch decision + locking
- Tests: `tests/test_persistence_bootstrap.py` (branches), `tests/test_persistence_bootstrap_concurrency.py` (concurrency), `tests/test_persistence_bootstrap_regression.py` (issue #3682), `tests/test_persistence_migrations_env.py` (filter), `tests/blocking_io/test_persistence_bootstrap.py` (asyncio.to_thread anchor)

### Terminal Workbench / TUI (`packages/harness/deerflow/tui/`)

A terminal-native UI over the embedded harness, exposed as the `deerflow` console script (`[project.scripts]` in `packages/harness/pyproject.toml`). It is a UI shell over `DeerFlowClient` and does **not** fork agent behavior. `textual` is an optional dependency (`deerflow-harness[tui]`; also in the backend dev group); the console script degrades to headless help when it is absent. Full guide: [docs/TUI.md](docs/TUI.md).

**Module layout** (all layers except `app.py` are pure / Textual-free and unit-tested directly):
- `cli.py` — `plan_launch()` (pure launch-mode decision) + headless `--print` / `--json` + `main()` entry point. TTY → TUI, else headless help. Uses an **absolute** `from deerflow.tui.app import run_tui` so the `app.py` module name doesn't trip `test_harness_boundary.py` (which records relative import module names verbatim).
- `view_state.py` — `ViewState` + `reduce(state, action)`, the testable heart. Rows: user / assistant / tool / system. Title captured from `values` events.
- `runtime.py` — `translate(StreamEvent) -> [Action]` (pure) + `stream_actions()` which brackets a run with `RunStarted`/`RunEnded` and turns model errors into an `AssistantError` row.
- `message_format.py` / `command_registry.py` / `input_history.py` / `render.py` / `theme.py` — pure helpers (tool summaries, slash registry + `resolve()`, ↑/↓ history, Rich renderers).
- `app.py` — Textual `App`. Runs `DeerFlowClient.stream()` (sync) on a worker thread and marshals actions to the UI thread via `call_from_thread`. Slash palette with `/goal` management + model/thread modal pickers; priority key bindings gated by `check_action` so they never steal keys from overlays or the composer.
- `session.py` / `persistence.py` — builds the client + checkpointer and the `ThreadMetaWriter`.

**Web UI visibility**: the Web UI lists threads from the `threads_meta` SQL table (user-scoped), not the checkpointer. `persistence.py` writes a `threads_meta` row under the default user (`"default"`) into the same DB the Gateway reads — via the harness-only `deerflow.persistence.engine.init_engine_from_config()` — so TUI sessions appear in the Web UI sidebar **without** running the Gateway. Best-effort: a no-op on the `memory` backend. All DB work runs on one long-lived background event loop (a SQLAlchemy async engine is bound to its creating loop).

**Tests**: `tests/test_tui_*.py` — pure layers via plain pytest, the app/palette/overlays via Textual's pilot harness with a fake in-process session, and `test_tui_persistence.py` for the `threads_meta` round-trip.

### Request Trace Context (`packages/harness/deerflow/trace_context.py`)

Request trace correlation is controlled by `logging.enhance.enabled` at **both** entry points, gated through the shared helper `deerflow.config.app_config.is_trace_correlation_enabled` so the Gateway and embedded paths cannot drift:

- **Gateway HTTP**: `app.gateway.trace_middleware.TraceMiddleware` binds one request-level trace id per HTTP request, inheriting inbound `X-Trace-Id` when present or generating a new id otherwise. The middleware writes the final value to every HTTP response at `http.response.start`, which covers SSE / streaming responses without consuming the body.
- **Embedded / TUI / CLI**: `DeerFlowClient.stream()` mints (or inherits) a request-level trace id per turn only when the flag is on. When it is off, no fresh id is minted — a caller that explicitly wraps `stream()` in `request_trace_context(...)` still opts in, because the downstream `get_current_trace_id()` read propagates that value into Langfuse metadata regardless of the flag. Because `stream()` is a sync generator (which shares the caller's context), the id binding is set/reset around each `next()` step rather than around `yield from`: this keeps LangGraph node execution and its log records inside the binding, while returning control to the caller with the ContextVar restored — avoids cross-request leak between yields and `ValueError: <Token> was created in a different Context` on GC-driven close of an abandoned generator (regression pinned by `tests/test_client_langfuse_metadata.py::test_stream_does_not_leak_trace_id_to_caller_context_between_yields` and `::test_stream_abandoned_generator_close_does_not_raise_cross_context`).

The same ContextVar value is injected into enhanced log records as `trace_id` and into Langfuse metadata as `deerflow_trace_id`.

`logging` is registered as a **restart-required** field
(`STARTUP_ONLY_FIELDS["logging"]`): `configure_logging()` installs the trace-context
filter and enhanced formatter on root handlers only during app.py lifespan startup,
and `TraceMiddleware` captures `logging.enhance.enabled` once when the FastAPI app
is constructed (via `resolve_trace_enabled(get_app_config())` in `create_app()`,
itself a thin alias for `is_trace_correlation_enabled`). This keeps the response
`X-Trace-Id` header, log `trace_id` fields, and Langfuse `deerflow_trace_id`
coherent — a runtime `config.yaml` edit to `logging.enhance.*` needs a Gateway
restart to take effect. The `deerflow_trace_id` chain inherits this guarantee
transitively because every injection point ultimately reads the same
`trace_context` ContextVar that the middleware alone populates. `DeerFlowClient`
reads its own `self._app_config` snapshot (captured at `__init__`) through the
same helper for the embedded gate.

`deerflow_trace_id` is a DeerFlow correlation metadata key, not Langfuse's native
trace id and not a DeerFlow `run_id`. Keep the existing subagent `trace_id` field
separate: that short id is still only for subagent execution logs/status.

### Tracing System (`packages/harness/deerflow/tracing/`)

LangSmith and Langfuse are both supported. The wiring lives in two layers:

- `factory.py::build_tracing_callbacks()` — returns the LangChain `CallbackHandler` list for the providers currently enabled via env vars (`LANGSMITH_TRACING`, `LANGFUSE_TRACING`, etc.). The handlers are attached at the **graph invocation root** for in-graph runs (`make_lead_agent` and `DeerFlowClient.stream` both append them to `config["callbacks"]` before invoking the graph) so a single run produces one trace with all node / LLM / tool calls as child spans. Standalone callers — anything that invokes a model outside such a graph (e.g. `MemoryUpdater`) — keep `create_chat_model`'s default `attach_tracing=True`, which falls back to model-level callback attachment.
- `metadata.py::build_langfuse_trace_metadata()` — builds the Langfuse-reserved trace attributes for `RunnableConfig.metadata`. The Langfuse v4 `langchain.CallbackHandler` lifts these onto the root trace (see its `_parse_langfuse_trace_attributes`), but only when it sees `on_chain_start(parent_run_id=None)` — which is why the callbacks have to live at the graph root, not the model.

**Trace-attribute injection points**: both `runtime/runs/worker.py::run_agent` (gateway path) and `client.py::DeerFlowClient.stream` (embedded path) merge the metadata into `config["metadata"]` right before constructing the graph. `subagents/executor.py::_aexecute` does the same for every subagent run so subagent traces group under the parent thread's session card (carrying the parent `thread_id` → `langfuse_session_id`, the user_id captured at `task_tool` → `langfuse_user_id`, and a `subagent:<normalized-name>` trace name). Caller-supplied keys win via `setdefault`, so an external `session_id` override is preserved. Field mapping:

| Langfuse field         | Source                                       |
|-----------------------|----------------------------------------------|
| `langfuse_session_id` | LangGraph `thread_id`                         |
| `langfuse_user_id`    | `get_effective_user_id()` (`default` in no-auth); for subagents, captured from `runtime.context` at `task_tool` time via `resolve_runtime_user_id()` |
| `langfuse_trace_name` | `RunRecord.assistant_id` / client `agent_name` (defaults to `lead-agent`); for subagents, `subagent:<name>` (lowercased, `_` → `-`) |
| `langfuse_tags`       | `env:<DEER_FLOW_ENV>` + `model:<model_name>`  |
| `deerflow_trace_id`   | Current request/entry trace id from `deerflow.trace_context`; matches `X-Trace-Id` for enhanced Gateway HTTP requests. Gated by `logging.enhance.enabled` in both gateway and embedded paths via `is_trace_correlation_enabled` — off by default; embedded callers can still opt in per-turn by wrapping `stream()` in `request_trace_context(...)` |

Returns `{}` when Langfuse is not in the enabled providers — LangSmith-only deployments are unaffected. Set `DEER_FLOW_ENV` (or `ENVIRONMENT`) to tag traces by deployment environment. Tests live in `tests/test_tracing_factory.py`, `tests/test_tracing_metadata.py`, `tests/test_worker_langfuse_metadata.py`, `tests/test_client_langfuse_metadata.py`, and `tests/test_subagent_executor.py::TestSubagentTracingWiring`.

### Config Schema

**`config.yaml`** key sections:
- `models[]` - LLM configs with `use` class path, `supports_thinking`, `supports_vision`, provider-specific fields
- `logging.enhance` - Optional request trace correlation (`enabled`, `format`) for Gateway `X-Trace-Id`, log `trace_id`, and Langfuse `deerflow_trace_id`
- vLLM reasoning models should use `deerflow.models.vllm_provider:VllmChatModel`; for Qwen-style parsers prefer `when_thinking_enabled.extra_body.chat_template_kwargs.enable_thinking`, and DeerFlow will also normalize the older `thinking` alias
- `tools[]` - Tool configs with `use` variable path and `group`
- `tool_groups[]` - Logical groupings for tools
- `sandbox.use` - Sandbox provider class path
- `skills.path` / `skills.container_path` - Host and container paths to skills directory
- `skills.deferred_discovery` - When `true`, replaces the full-metadata `<available_skills>` prompt block with a compact `<skill_index>` (names only) and registers the `describe_skill` tool so the agent fetches metadata on demand. Defaults to `false` (legacy full-metadata injection)
- `title` - Auto-title generation (enabled, max_words, max_chars, model_name; null model_name uses fast local fallback, explicit model_name uses the prompt_template LLM path)
- `summarization` - Context summarization (enabled, trigger conditions, keep policy)
- `subagents.enabled` - Master switch for subagent delegation
- `memory` - Memory system (enabled, storage_path, debounce_seconds, model_name, max_facts, fact_confidence_threshold, injection_enabled, max_injection_tokens, staleness_review_enabled, staleness_age_days, staleness_min_candidates, staleness_max_removals_per_cycle, staleness_protected_categories)

**`extensions_config.json`**:
- `mcpServers` - Map of server name → config (enabled, type, command, args, env, url, headers, oauth, description)
- `skills` - Map of skill name → state (enabled)

Both can be modified at runtime via Gateway API endpoints or `DeerFlowClient` methods.

### Embedded Client (`packages/harness/deerflow/client.py`)

`DeerFlowClient` provides direct in-process access to all DeerFlow capabilities without HTTP services. All return types align with the Gateway API response schemas, so consumer code works identically in HTTP and embedded modes.

**Architecture**: Imports the same `deerflow` modules that Gateway API uses. Shares the same config files and data directories. No FastAPI dependency.

**Agent Conversation**:
- `chat(message, thread_id)` — synchronous, accumulates streaming deltas per message-id and returns the final AI text
- `stream(message, thread_id)` — subscribes to LangGraph `stream_mode=["values", "messages", "custom"]` and yields `StreamEvent`:
  - `"values"` — full state snapshot (title, messages, artifacts); AI text already delivered via `messages` mode is **not** re-synthesized here to avoid duplicate deliveries
  - `"messages-tuple"` — per-chunk update: for AI text this is a **delta** (concat per `id` to rebuild the full message); tool calls and tool results are emitted once each
  - `"custom"` — forwarded from `StreamWriter`
  - `"end"` — stream finished (carries cumulative `usage` counted once per message id)
- Agent created lazily via `create_agent()` + `build_middlewares()`, same as `make_lead_agent`
- Supports `checkpointer` parameter for state persistence across turns
- `reset_agent()` forces agent recreation (e.g. after memory or skill changes)
- See [docs/STREAMING.md](docs/STREAMING.md) for the full design: why Gateway and DeerFlowClient are parallel paths, LangGraph's `stream_mode` semantics, the per-id dedup invariants, and regression testing strategy

**Gateway Equivalent Methods** (replaces Gateway API):

| Category | Methods | Return format |
|----------|---------|---------------|
| Models | `list_models()`, `get_model(name)` | `{"models": [...]}`, `{name, display_name, ...}` |
| MCP | `get_mcp_config()`, `update_mcp_config(servers)` | `{"mcp_servers": {...}}` |
| Skills | `list_skills()`, `get_skill(name)`, `update_skill(name, enabled)`, `install_skill(path)` | `{"skills": [...]}` |
| Goals | `get_goal(thread_id)`, `set_goal(thread_id, objective, max_continuations=8)`, `clear_goal(thread_id)` | `{"goal": {...}}` or `{"goal": None}` |
| Memory | `get_memory()`, `reload_memory()`, `get_memory_config()`, `get_memory_status()` | dict |
| Uploads | `upload_files(thread_id, files)`, `list_uploads(thread_id)`, `delete_upload(thread_id, filename)` | `{"success": true, "files": [...]}`, `{"files": [...], "count": N}` |
| Artifacts | `get_artifact(thread_id, path)` → `(bytes, mime_type)` | tuple |

**Key difference from Gateway**: Upload accepts local `Path` objects instead of HTTP `UploadFile`, rejects directory paths before copying, and reuses a single worker when document conversion must run inside an active event loop. Artifact returns `(bytes, mime_type)` instead of HTTP Response. The new Gateway-only thread cleanup route deletes `.deer-flow/threads/{thread_id}` after LangGraph thread deletion; there is no matching `DeerFlowClient` method yet. `update_mcp_config()` and `update_skill()` automatically invalidate the cached agent.

**Tests**: `tests/test_client.py` (77 unit tests including `TestGatewayConformance`), `tests/test_client_live.py` (live integration tests, requires config.yaml)

**Gateway Conformance Tests** (`TestGatewayConformance`): Validate that every dict-returning client method conforms to the corresponding Gateway Pydantic response model. Each test parses the client output through the Gateway model — if Gateway adds a required field that the client doesn't provide, Pydantic raises `ValidationError` and CI catches the drift. Covers: `ModelsListResponse`, `ModelResponse`, `SkillsListResponse`, `SkillResponse`, `SkillInstallResponse`, `McpConfigResponse`, `UploadResponse`, `MemoryConfigResponse`, `MemoryStatusResponse`.

## Development Workflow

### Test-Driven Development (TDD) — MANDATORY

**Every new feature or bug fix MUST be accompanied by unit tests. No exceptions.**

- Write tests in `backend/tests/` following the existing naming convention `test_<feature>.py`
- Run the full suite before and after your change: `make test`
- Tests must pass before a feature is considered complete
- For lightweight config/utility modules, prefer pure unit tests with no external dependencies
- If a module causes circular import issues in tests, add a `sys.modules` mock in `tests/conftest.py` (see existing example for `deerflow.subagents.executor`)

```bash
# Run all tests
make test

# Run a specific test file
PYTHONPATH=. uv run pytest tests/test_<feature>.py -v
```

### Running the Full Application

From the **project root** directory:
```bash
make dev
```

This starts all services and makes the application available at `http://localhost:2026`.

**All startup modes:**

| | **Local Foreground** | **Local Daemon** | **Docker Dev** | **Docker Prod** |
|---|---|---|---|---|
| **Dev** | `./scripts/serve.sh --dev`<br/>`make dev` | `./scripts/serve.sh --dev --daemon`<br/>`make dev-daemon` | `./scripts/docker.sh start`<br/>`make docker-start` | — |
| **Prod** | `./scripts/serve.sh --prod`<br/>`make start` | `./scripts/serve.sh --prod --daemon`<br/>`make start-daemon` | — | `./scripts/deploy.sh`<br/>`make up` |

| Action | Local | Docker Dev | Docker Prod |
|---|---|---|---|
| **Stop** | `./scripts/serve.sh --stop`<br/>`make stop` | `./scripts/docker.sh stop`<br/>`make docker-stop` | `./scripts/deploy.sh down`<br/>`make down` |
| **Restart** | `./scripts/serve.sh --restart [flags]` | `./scripts/docker.sh restart` | — |

**Nginx routing**:
- `/api/langgraph/*` → Gateway embedded runtime (8001), rewritten to `/api/*`
- `/api/*` (other) → Gateway API (8001)
- `/` (non-API) → Frontend (3000)

### Running Backend Services Separately

From the **backend** directory:

```bash
# Gateway API
make gateway
```

Direct access (without nginx):
- Gateway: `http://localhost:8001`

### Frontend Configuration

The frontend uses environment variables to connect to backend services:
- `NEXT_PUBLIC_LANGGRAPH_BASE_URL` - Defaults to `/api/langgraph` (through nginx)
- `NEXT_PUBLIC_BACKEND_BASE_URL` - Defaults to empty string (through nginx)

When using `make dev` from root, the frontend automatically connects through nginx.

## Key Features

### File Upload

Multi-file upload with automatic document conversion:
- Endpoint: `POST /api/threads/{thread_id}/uploads`
- Supports: PDF, PPT, Excel, Word documents (converted via `markitdown`)
- Rejects directory inputs before copying so uploads stay all-or-nothing
- Reuses one conversion worker per request when called from an active event loop
- Files stored in thread-isolated directories under the resolving user's bucket (`users/{user_id}/threads/{thread_id}/user-data/uploads`). For IM channels the owner is threaded explicitly via the `user_id=` kwarg (see IM Channels → Owner-scoped file storage); HTTP/embedded callers resolve it from `get_effective_user_id()`
- Duplicate filenames in a single upload request are auto-renamed with `_N` suffixes so later files do not truncate earlier files
- Gateway HTTP uploads stage bytes as `.upload-*.part` files and atomically replace the destination only after size validation. These staging files are hidden from upload listings, agent upload context, and sandbox listing/search tools, and swept on Gateway startup if a hard crash leaves one behind.
- Gateway HTTP upload/list/delete handlers offload filesystem work through `deerflow.utils.file_io.run_file_io`, a dedicated ContextVar-preserving file IO executor. Non-mounted sandbox uploads acquire sandboxes with `SandboxProvider.acquire_async()` and offload `read_bytes()` plus `sandbox.update_file()` together.
- Agent receives uploaded file list via `UploadsMiddleware`

See [docs/FILE_UPLOAD.md](docs/FILE_UPLOAD.md) for details.

### Plan Mode

TodoList middleware for complex multi-step tasks:
- Controlled via runtime config: `config.configurable.is_plan_mode = True`
- Provides `write_todos` tool for task tracking
- One task in_progress at a time, real-time updates

See [docs/plan_mode_usage.md](docs/plan_mode_usage.md) for details.

### Context Summarization

Automatic conversation summarization when approaching token limits:
- Configured in `config.yaml` under `summarization` key
- Trigger types: tokens, messages, or fraction of max input
- Keeps recent messages while summarizing older ones
- Manual compaction uses `POST /api/threads/{id}/compact`, reuses the same
  `DeerFlowSummarizationMiddleware`, writes a new checkpoint with updated
  `messages` and `summary_text`, and bumps only those channel versions.
  The route shares the per-thread serialization gate used by `/goal` writes
  and run admission so compaction cannot race with goal updates or runs that
  read/write checkpoints.

See [docs/summarization.md](docs/summarization.md) for details.

### Vision Support

For models with `supports_vision: true`:
- `ViewImageMiddleware` processes images in conversation
- `view_image_tool` added to agent's toolset
- Images automatically converted to base64 and injected into state

## Code Style

- Uses `ruff` for linting and formatting
- Line length: 240 characters
- Python 3.12+ with type hints
- Double quotes, space indentation

## Documentation

See `docs/` directory for detailed documentation:
- [CONFIGURATION.md](docs/CONFIGURATION.md) - Configuration options
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) - Architecture details
- [API.md](docs/API.md) - API reference
- [SETUP.md](docs/SETUP.md) - Setup guide
- [FILE_UPLOAD.md](docs/FILE_UPLOAD.md) - File upload feature
- [PATH_EXAMPLES.md](docs/PATH_EXAMPLES.md) - Path types and usage
- [summarization.md](docs/summarization.md) - Context summarization
- [plan_mode_usage.md](docs/plan_mode_usage.md) - Plan mode with TodoList


## Collusion audit isolated application

`app/experiments/collusion_audit/` is an optional, run-scoped adapter. Use the existing SPExecutionExtensions and native graph. Do not force a next subagent, replace the Central policy, or feed any historical project analyses, workbook project experience, validation results or GT into model prompts/tools. Only whitelisted generic M01–M15 fields and original staged project sources are agent-readable. Tools wrap unchanged algorithms in `vendor/bid_audit_tools`; paths are restricted to clean project files and the current rule output. Tests: `tests/test_collusion_audit.py` plus the copied vendor synthetic tests. See `../docs/COLLUSION_AUDIT.md`.

Trusted existing-tool execution: `SPSubagentTask.tool_execution_contract` is a typed, default-None server-side extension. It can be set by prepare_task, never via action metadata. Under this opt-in contract, Coder requires successful registered tool receipts rather than source changes/software tests; error JSON fails. Read-only tool capabilities remain scoped. Actor, stage, Central policy and normal coding-task verification are unchanged. Regression anchor: `tests/test_sp_tool_execution_contract.py`.

For tool-only tasks, `artifact_write_tools` explicitly declares scoped output writers without granting input/source mutations. The collusion writer uses native `path` so existing created-path recovery observes its actual tool receipt. Long Markdown is written once to a real .md; only a short metadata envelope is returned. The driver distinguishes observed FINISH from a selected Markdown artifact.

Collusion existing-tool contracts also restore the server-captured Central tool request before software defaults; action metadata cannot enable the typed contract. Default software tasks retain their original tool injection and verification. See docs/COLLUSION_AUDIT.md.

Trusted existing-tool metadata is server-stamped into native observations and narrows recovery capabilities; it does not alter recovery role/stage/budget. Model-declared contracts are stripped. Execution receipts accept any successful delegated registered tool, while artifact writes alone do not count for the collusion profile.

Run-scoped artifact_type_aliases on typed SPSubagentTask default to empty. The collusion profile canonicalizes its legacy tool_output_report to native research_observation, preserving declared_artifact_type and raw model output; intermediate evidence must not trigger deliverable finalization.

`SPToolExecutionContract.require_result_protocol` defaults to false. The collusion profile enables it: successful read receipts cannot promote an empty, promise-only or non-protocol terminal reply to complete. Normalization only validates summary/type/completion metadata and marks unmet output partial; it does not repair JSON or select a recovery action. Keep default callers unchanged and test with `test_sp_tool_execution_contract.py`.

Collusion delegation carries server-observed audit_calculation_runs. Do not confuse SP execution run_id with vendor audit_run_id. Wrong-ID errors expose only current-scope audit IDs; report requirements remove the current SP ID and include the real calculation ID. No aliasing of tool arguments or historical-run access.

Native local-document routing may convert a Coder action to Perception and strip custom tool names. Do not bypass that routing. The collusion adapter materializes lossless10-row read_file pages and uses native stage Artifact bodies for intermediate classification. Non-memory roles opt into terminal protocol validation; capability metadata must be server-stamped for every contracted role.

SPExecutionExtensions.max_actions is a trusted, default-None resource allowance in2..256. Runtime/API values remain clamped to20 absent that extension. Reuse SPActionBudgetMiddleware without changing action/role selection. Collusion M04 uses64 actions/1024 graph steps because20-row output batches exceed the old20-action envelope; other rules retain20/320. Tests cover client non-escalation, trusted bounds and the unchanged native terminal guard.

`SPExecutionExtensions.precise_report_intent` defaults false and is enabled only by the collusion application. Native SPControlActionMiddleware receives this trusted boolean; API/action metadata cannot enable it. When enabled, only report-intent matching becomes clause-local and polarity-aware; code priority, local-document inspection, missing-role inference, stage normalization, tool scopes and native acceptance/recovery stay unchanged. Tests: `test_sp_precise_report_intent.py`, including actual native executor dispatch. Preserve the other14 audit results; onlyM04 is revalidated.


The collusion v15 application optionally supplies `SPExecutionExtensions.finish_validator` and `observe_result` (both default None). The native router owns FINISH rejection/recovery and action budgets; the application only reports missing business coverage. The result observer persists strictly validated source/assessment records from native Artifact metadata. No model/API metadata enables either hook. Application-only subagent middlewares scope duplicate reads and reserve a normal tool-free closing response near the native tool cap; they never repair JSON or choose another role. MinerU knowledge is persistent and one PDF per KB; do not merge document scopes. Core tests: test_sp_business_finish.py. Domain tests: test_collusion_{knowledge,coverage,closing,repeat,ingest,knowledge_integration,table_grid,metrics}.py. Keep original algorithms and all historical model results immutable; only M04 is revalidated initially.


Collusion rule-level opt-in: `--rule-level-review` sends the full current code packet, with lossless shared metadata grouping, rather than requiring20-row retrieval and judgement submissions. `full_results.py` validates collection counts/path scopes and roundtrips every original field; it does not summarize facts. No row ledger/FINISH row-coverage hook is installed in this mode. Generic rules, original manifest, persistent per-PDF MinerU tools and native report acceptance remain. Legacy row mode is retained explicitly for regressions; old model results are immutable.

`SPExecutionExtensions.scoped_delegate_recovery: bool = False` is a trusted Python-only opt-in. `SPSubagentTask.recovery_scope_key` is host-only, omitted from model request protocols; DelegateHandler strips forged action/result scope metadata and stamps the typed key into delegate/observation/Artifact metadata. SPFinishAvailabilityMiddleware uses it in both before/after-model recovery paths, with legacy fallback when absent. Stage/run/role filtering, original retry thresholds, reporter recovery and action budgets remain. Collusion derives scopes from actual original/KB/code tool receipts; model wording does not mint arbitrary scope IDs. Tests: `test_sp_scoped_recovery.py`, `test_collusion_full_results.py`, `test_collusion_rule_mode.py`, `test_collusion_large_submission.py`.

Collusion current-project round facts must come only from the staged manifest. Never assume two rounds: single-round M14 still invokes its code tool and reports applicability/gaps without inventing another round. Regression: `test_collusion_project_rounds.py`. The user-authorized Jiuzhoutong project replacement clears old test outputs and the explicitly approved old staged inputs/cache; original datasets, original SP2, source snapshots and remote knowledge remain protected. Current input/result index: `../collusion_audit/README.md`.


Trusted `SPSubagentTask.source_reference_tokens` defaults to an empty tuple and is excluded from `protocol_payload`. Only Python host hooks may set it; action/result metadata is ignored. Native report provenance permits these observed IDs separately from required literals; unknown IDs and all other report checks remain. The isolated collusion profile captures fact fields before projection, excludes query/error echoes and model-written drafts, and binds its existing false-required-token projection to the current single-message run using native whitespace/clipping rules. Optional source IDs never become inherited must-include requirements. Tests: `test_sp_source_reference_tokens.py`, `test_collusion_source_reference_tokens.py`.

The copied M11 PDF parser tolerates null/unresolved XObjects, preserves valid font extraction/grouping rules, and records page-specific warnings in font-cache-v2 so cache reuse retains coverage limits. Only `vendor/bid_audit_tools/bid_audit/pdf_rules.py` in this isolated copy changes; the provided source tool directory remains protected. Failed calculations return the original error before full-result packaging and are traced as failures, never as successful receipts. Tests: `test_collusion_font_null_resources.py`. Native actor/stage selection, budgets, timeout multiplier and JSON parsing are unchanged.

`full_results.COMMON_FIELDS` groups stable document provenance only. Keep page and table_bbox verbatim in rows so cross-page records share document metadata. The schema remains grouped_records_v1; record order and JSON types roundtrip exactly. Regression: test_document_metadata_is_shared_across_distinct_page_and_table_positions. Never truncate the full packet to fit a model.


### Quality controls for a fresh Qwen run (v18)

The collusion runner accepts `--quality-controls` (default **off**). With
`--rule-level-review`, it preserves the complete lossless code packet and adds a
current-run deterministic facts index: actual comparison scope, zero matches,
font-byte comparison counts, image distance statistics and original quote fields.
Central and subagents receive the same current facts; historical reports and rule
experience are never loaded. These facts do not assign risk verdicts.

The trusted host supplies explicit audit-ID catalogues and required rule/run IDs;
ordinary SHA/font/model terms are no longer inferred as mandatory audit IDs.
The rule's original evidence grade is checked. `view_image_pair` resolves both
M06 endpoints from the current calculation, and submission receipts track only
images actually sent in model requests. Reports declare their truly judged subset,
which may be incomplete; there is no all-candidate ledger or fixed paging gate.
Generic rule guidance separates verified exclusions from unverified explanations.

Registered tool identity fields distinguish legal `read_file` character windows.
The normal repetition threshold and Central's same-role checkpoint remain intact.
A compact phase-closing hint helps long tool delegations return their own native
protocol; malformed JSON is still rejected without repair. All hooks are host-only,
optional and inactive in the original SP2 application. Roles, stages, THINK,
recovery and FINISH remain selected and accepted by native SP2.

Fresh batches use independent directories; prior reports, raw trajectories, cost
files and persistent MinerU knowledge are preserved byte for byte. The model,
thinking, token cap, timeouts and action budgets remain unchanged.

M11报告校验还检查未验证前缀生成机制的确定性断言，以及把当前零字节匹配写成绝对排除文件复用的表述；失败保留原文并由Central原生恢复。完全相同的图像显示字节可绑定多个真实来源，避免图像去重后丢失候选端点。


质量模式补充（v18b）：原生图像注入器默认仅识别view_image，可信扩展image_tool_names可显式注册其他返回viewed_images的工具；仅M06启用view_image_pair，默认行为不变。验收必须查看实际模型请求中的图片，不能用PNG生成或工具成功代替。新增原生执行器真实图像注入集成回归。

/knowledge/dXXX/pages/起始页-结束页.md为仅质量模式开放的物理页虚拟文件，仍支持原字符游标和窗口上限。它按冻结chunk原始位置选择完整原文，跨页chunk/无文本页如实标注，不修改MinerU库。实际返回的完整chunk及图像输入回执只表示信息可用，不自动计为模型已判读。M06报告另声明ocr_reviewed_candidates（可空）与candidate_page_count，并与实际输入及候选端点去重页数一致；不要求全候选台账。页面区间推断须在正文披露，不能只藏于metadata。

可信image_tool_names同时用于原生Perception只读工具过滤：仅保留Central已经显式请求的登记图像工具，不添加未请求工具，不保留写文件/执行命令等能力。角色重写和阶段选择原样保留；默认空注册的原生行为完全不变。M06仅在质量模式启用这项兼容，模型或API元数据不能开启。

质量模式的委派收尾计数采用当前完整状态与历次模型请求中工具调用ID的去重并集，避免模型历史窗口裁剪后漏算已执行调用。每次委派独立计数，重复播放同一消息不累加；原16条回执收尾阈值、模型/时间/动作预算及默认关闭行为不变。计数依据与裁剪窗口内条数一并写入事件，原始JSON不修复。

### M01 generic recall v19

The isolated collusion copy routes both registry and runner M01 through
`vendor/bid_audit_tools/bid_audit/m01_recall.py`. `run_rule(parsed_texts=...)`
accepts trusted in-memory original-only derived chunks for M01; source hashes
must match the clean manifest. `m01_corpus.py` excludes generated KB metadata.
Calculation keys include derived text and position fingerprints as well as
original bytes/tool code/dependencies. Original PDFs remain authoritative for
binary/image rules; M02–M15 algorithms are unchanged.

The lexical matcher uses normalized maximal exact anchors, sentence/block
windows and approximate character seeds. Template exclusions, thresholds,
source coverage, candidate grouping and both source locators are persisted.
Overlapping windows are not independent evidence. Multi-page OCR locators never
claim an exact first page. These are candidate facts, never risk verdicts.

`m01_delivery.py` declares `indexed_large_M01` for complete packets over80k
characters: every candidate remains in the immutable full result and an
addressable per-candidate JSON file. Bounded previews and category hints are
navigation, not full review or proven public-source exclusions. Native Central
gets paired excerpts/differences/IDs through current-code-fact context, and can
choose subsequent roles/steps. No row ledger or JSON repair is introduced.
`project_search` explicitly supports normalized literal or AND-term search;
M01 also searches verified current MinerU chunks. Logs distinguish modes.

Regression anchors: `test_collusion_m01_recall_v19.py` and
`test_collusion_m01_adapter_v19.py`. Historical project answers/terms/pages are
restricted to operator-only posthoc evaluation; do not inject them into runtime
code, generic tests, skill/prompt, or candidate selection.

M01 v19 follow-up: `compact_facts` and large-result navigation retain complete
paired candidate passage bodies and primary source quotes for the six highest
priority candidates, while explicitly declaring the candidate subset. Never
clip a body to180 characters and then describe it as full evidence. All other
candidate records remain addressable. `input_channel_coverage` separates raw
PDF text sparsity from actually compared persisted MinerU chunks/page positions;
positions do not prove recognition or model review. Deterministic document-pair
and match-mode totals avoid estimating full distributions from partial indexes.
Regressions cover body-tail retention, original quote retention and dual-channel
warnings. Native scheduling, budgets, timeouts and JSON parsing stay unchanged.

### Collusion generic evidence v20

With trusted parsed_texts, vendor runner installs source-hash-checked derived
original views and routes M02–M13 through enhanced_rules. Standalone callers
without parsed_texts retain legacy entry behavior. Original PDF bytes and raw
Context.pages are never replaced. structured_text handles HTML spans and keeps
layout conflicts explicit. enhanced_tables recovers same-page split columns
only with unique parameter context, and records same-document ordinal or ordered
multi-item section inference. Ambiguous/orphan rows remain persisted, never
become silently completed audit items. Numeric interval facts and identical
negative labels are distinct from business deviation verdicts.

The shared-passages cache contains only current M01 lexical facts and source
fingerprints, never model reports. enhanced_text uses arbitrary observed edits,
not project-specific error terms. Single-side edit artifacts are not joint errors.
M06 original-image decisions remain native; complete OCR chunks count as input
only when seen in actual projected model text. M10 marker counts never equal a
verified number of saves; M11 bad resources retain warnings; M12 raw printable
non-stream syntax windows and decoded CDC stay separate observation domains.

Large packets persist losslessly with result_navigation/candidates and explicit
supplementary collection subsets. compact_facts retains selected complete paired
passages and source quotes. No judgement ledger, role forcing or JSON repair.
Historical evaluation anchors belong only under operator operations/results.
Tests: test_collusion_general_v20.py plus existing source/quality/tool suites.

v20 scope follow-up: response_views uses actual response headers and nearby
status-bearing continuation pages (allowing up to two missing intervening pages),
not unrelated appendix tables. Technical/business ordinal catalogs are separate.
Disjoint measurement dimensions stay in alignment_conflicts and are not mined as
word-level edits. Bid-stated requirement text is not a verified public template
without an actual tender/public-source match. Scope regressions live in
`test_collusion_v20_scope_refinements.py`; do not pass operator reference files
to runtime. First M02 run stays immutable; rerun under a new attempt batch.

Original-geometry follow-up: geometric_tables caches only clean source facts keyed by source SHA, parser version, selected pages and module fingerprint. Relative header x bands survive varying continuation subcolumns; row y is cell intersection, not merged-cell union. enhanced_tables merges same physical readings while preserving raw extraction_variants and numeric conflicts. M03 keeps same-own-requirement pairs as source observations. General navigation supplies exact edit fields and whole-collection counts; source observation indexes never imply semantic review. Null resource maps retain warnings; font cache v4. Regression: test_collusion_geometry_v20.py.

source_access in compact facts separates original read capability, observed original/OCR coverage and pair-comparison membership. DOCX without MinerU stays reachable via document_id; missing OCR positions are not evidence that original text was omitted. Scope regression: test_collusion_geometry_v20.py.

Quality-mode delegation transport deduplicates calculation identity and carries metadata/pointers rather than repeated fact bodies when a canonical live fact index exists. CurrentCodeFactsMiddleware still injects the unmodified complete current fact view; missing live indexes retain original transport bodies. Default/non-quality behavior, full rules, historical reasoning and native role choice are preserved. Regression: test_collusion_context_facts_v20.py.

Source-navigation follow-up: M03 copy observations carry actual normalized tender/public-source support and separate verified/unverified counters. Never infer template provenance from a bidder requirement column. M12 exposes participant-set/resource-type representative raw windows without deleting full findings. Read-only calculation references require producer-known filenames and matching SHA; model drafts, forged extra candidates and changed files are untrusted. Quality mode enables explicitly requested original single-image viewing for all rules, paired viewer remains M06-only; native defaults/roles are unchanged. Regression: test_collusion_source_navigation_v20.py.

Bounded response-pair source views preserve complete q/a/b text and source positions for aligned findings, capped by200records/120000serialized characters for direct delivery; overflow stays explicit and lossless on disk. CurrentCodeFacts suppresses a duplicate navigation body only when an actual current audit ToolMessage contains the identical complete navigation; otherwise it restores the full live fact view. No message mutation, hidden row truncation, JSON repair or role forcing. Regressions: source_navigation_v20 and context_facts_v20.

compact_paired_source_view defaults false and is enabled only on the quality Central fact middleware. It reduces the newly added dense pair-view transport while preserving total counts, selected complete source previews and the full view path. Scoped subagent fact views and the backing index stay complete and immutable. Empty central source-index rows are explicitly transport omission, not data absence. Native scheduler/timeouts/JSON handling are untouched.

OCR boundary follow-up: M06 current-code facts observe the exact fact body forwarded to the model before receipt metadata is assembled, preserving the existing image observer order. Strict JSON decoding accepts only complete host-hash-verified calculation text; invalid text is ignored, never repaired. Real create_agent middleware-chain regression proves the text reaches the model and the receipt. General previews round-robin actual document IDs for bounded context/evidence arrays; full candidate sources stay immutable.

通用表单/模板观察索引保留真实表名、默认项、空白计数、正文未验证状态与源位置；模板保留上下文及匹配位置。仅表单/模板补充集合增加完整位置，其他大集合不重复扩张。结果足够时可按原生阶段协议直接收尾，不要求占位工具调用；是否继续取证仍由原生Central自主决定。

M06质量提示说明12图为保留上限而非推荐批量；在固定8192输出预算下可由Central自主将视觉委派限定一到两对并简短交付，后续是否补查仍自主决定。dHash=0不能冒充像素哈希一致。图片窗口、模型配置、调度和原始集合均未改动。

v20预览按实际finding kind轮转，在各类内保留优先顺序，避免长公共记录占满有限预览。行政许可结构标签只影响导航优先级，不构成模板来源验证或风险排除；完整候选和源正文不删。BudgetClosingMiddleware的report_path默认None；仅围串标质量模式Reporter绑定当前真实报告契约路径。收到write_file成功created回执后请求原生阶段收尾，不再重复写/读同一文件；错误、其他路径、用户文本不触发，不自动验证或标记通过，不修复JSON。Central仍决定是否另行修订及后续角色。

候选索引保留真实punctuation/pattern，summary_statistics从完整findings计算其分布，不要求模型逐条计数。质量模式Perception若input_refs为空，只将委派task中明确出现、当前生产器已登记且SHA一致的计算文件登记为真实input_refs；不新增读取任务或工具，不声明已读，现有Central输入引用和真正澄清均不改。默认关闭，原生Perception验收代码不变。

M08候选索引保留原始短context和实际来源页，literal_context_navigation仅计算原上下文的字面表头/配置/占位标记、文档集合及交叉计数。未知不表示自由正文，字面标记不表示已验证表格/模板，更不表示风险；unpaired_anomalies独立计数。无全候选二次分类交付门槛，Central仍按需决定补查。完整源集合与既有结果保留。

M08检测改用原始可见字符单元：HTML按实际单元格/段落分隔，DOCX按原始段落/单元格分隔，不使用M01纯文本转换插入的分号，不通过删除空白创造连续标点。宽度与空白规范化仅在实际检测后用于分组/模板匹配；原标点与原文quote、真实来源定位保留。真实单元内连续标点及招标本身的同型标点继续保留/按来源匹配。此变更仅M08，原件和其他规则算法不改。

质量模式Perception的真实来源声明也解析委派明确指出的当前audit_run_id和集合名，绑定生产器实际文件并核验SHA；不加入未请求集合、不读历史目录。局部Perception取证补充范围说明，不重做全项目任务归一化；真正缺口仍保留。默认行为和原生验收/调度代码不改，模型与超时参数不改。

CompactControlScopeMiddleware默认关闭，仅围串标质量模式Central启用。仅提示控制动作的task用简短受托范围、真实已有run_id/集合名及输出种类引用已自动附带的完整事实，避免再次抄写长规则/统计表/引文。完整规则、证据正文、历史reasoning和原生工具不删、不重写；子代理不启用该提示，角色和步骤不指定，不改模型参数、超时或JSON处理。

围串标 quality_controls 的 Central 事实投影启用默认关闭的 compact_control_navigation：保留完整规则、真实统计、最多3个完整候选预览及各集合最多2个完整示例、完整来源路径；大索引仅在控制传输中省略。未具备完整持久化路径的正文不裁减；受托审核/报告代理仍接收完整有界证据视图，原件及完整计算包不变。不改变原生角色、步骤、模型预算、时限和JSON处理。

围串标质量模式增加默认关闭的 close_repeated_reads：仅在当前委派至少6个真实工具回执、最近4个回执至少3次以不同call_id返回相同只读already_returned/result_ref且末项仍重复时，进入原生阶段协议收尾。保留全部已有消息和来源，不认定完成、不修复JSON；未完成范围须标partial，后续角色与补查仍由原生Central决定。错误、不同结果、写入及用户文本不触发。

质量模式报告引用目录对超过20000个真实E六位证据ID采用互斥前缀范围分区；每个目录保持原生20000项上限，完整允许集合及对不存在/非规范数字ID的拒绝能力不变。小目录与原先契约完全一致；不扩大原生32目录上限，无法无损表达时显式失败而非截断。只改围串标适配，不修改原生角色/步骤或JSON处理。

围串标质量模式阶段交接保持原生协议及完整规则/源正文，默认提示只交接新增判断、来源路径与局限（artifact_content约800汉字），不要求重抄完整明细表作为规则报告前置。M12代码新增代表分区精确求和与完整性标记，并对全部共享原始80字节非流窗口扫描字面PDF键/XMP标记、保留全部匹配源观察；计数不是独立证据、字面键不是已验证元数据归属或风险，零值不代表整份PDF没有元数据。原生角色/步骤/预算/时限及JSON处理均不变。

默认关闭的子代理容量接口：SubagentConfig的model_max_tokens/model_timeout_seconds/model_request_deadline_seconds均默认为None，仅信任服务端配置钩子，不从模型action metadata读取。围串标质量模式可显式传--reporter-max-tokens及--reporter-request-timeout，只给Reporter覆盖输出上限、HTTP超时及本角色模型请求截止；当前验证拟用16384/480秒，thinking保持开启，Central及取证复核模型8192/240秒和原生300秒截止不变，调度策略与JSON处理不变。未启用参数的所有原生调用保持默认行为。

Existing-tool recovery: the host-stamped `tool_execution_contract` (`kind=existing_tools`, `source_changes_required=false`) excludes an observation from source-patch recovery normalization and its blocking policy. This capability is absent by default; action/result metadata cannot create it. Central still selects roles/stages, and ordinary code-patch recovery is unchanged. Regression: `test_sp_existing_tool_recovery_v24.py`.

Collusion price-source extraction is isolated in `vendor/bid_audit_tools/bid_audit/price_sources.py`. It preserves literal cap scope, actual multirow headers, source roles, and unverified contract/performance context. No risk verdict or scope label proves current offered configuration. `configuration_contexts` uses the existing complete-result navigation. Missing OCR positions derive from source coverage; agent unread pages remain unread. M12 stage summaries reference complete source files without requiring a retyped full classification ledger.

`--evidence-role-capacity` is a default-off collusion CLI flag that shares explicitly provided Reporter capacity with Coder and Researcher only. Host options, not task metadata, enable it. Effective configs separately record this expansion; Central, Perception and memory defaults remain unchanged. Tests: `test_collusion_evidence_budget_v26.py`.

Price navigation preserves document/field/scope diversity; literal numeric per-unit cap observations and tender baseline tables receive preview prominence, not risk significance. Complete persisted collections stay unchanged. Regression: `test_collusion_price_navigation_v27.py`.

Collusion resource options `--central-retry-timeout` and `--central-retry-max-tokens` forward existing native runtime knobs; defaultNone preserves native defaults and no scheduler code changes. Quality+rule mode uses `bounded_records.read_bounded` over unchanged20-row backend pages; it returns complete records within60000chars, with truthful cursors and max200requested records. Legacy transport is unchanged. `supplemental_prices.py` preserves source price observations, exact currency/unit arithmetic, duplicate origins, conflicting copies and ambiguous parallel requirement/response tables. M15 provisional amount vectors are not automatically verified unit prices or a risk verdict; fact scope includes actual supplementary bid sources.

### Collusion opt-in reader HTML

`CollusionProfile.report_format` defaults to `markdown`; trusted CLI `--report-format html` requires quality controls and rule-level review. Scoped task/delegation prompts describe actual role capabilities, distinguish native once-only perception from consecutive-delegation checkpoints, and request concise natural-language handoffs without dropping independent findings. They do not prescribe a next role or step.

`reader_source_facts` adds current successful receipt references by natural document identity and physical pages, plus an explicitly non-semantic deduplicated lower bound for complete M01 candidate delivery. It reads only current receipt files, verified OCR receipt state and actually submitted images; it never parses model-authored JSON text as source evidence. This is an input aid, not a review ledger or new completion gate. Without a reliable semantic ledger, prompts use qualitative checked scope rather than invented exact review/remainder counts, and distinguish inspected subjects from risk subjects.

The application supplies a generic blank HTML template and natural document-name map, allows real HTML output writes, and checks seven reader sections, portability, internal-identifier privacy, evidence grade and M06 declared coverage. M06 distinguishes reviewed image/OCR pairs from all candidate endpoint pages. Provenance is host-stamped in artifact metadata; the HTML body carries natural sources and physical pages. Native FINISH still selects the actual current-run report artifact.

Two trusted generic compatibility interfaces default off. `SPReportReferenceContract.content_type` defaults to None; only `text/html` projects report text without head/style/script for the unchanged language ratio check. Raw files and all other checks remain intact. `SPExecutionExtensions.preserve_registered_tool_intent` defaults False; when enabled, the runtime derives identities from the actual extension tool registry. A requested registered capability that cannot survive Perception filtering prevents a false pure-file-reader rewrite of Central's explicit Coder choice. Pure file inspection, explicit Perception, positive report routing, stage guards and budgets stay native. Model metadata/API context cannot activate either interface.

HTML M01 also registers `audit_m01_index`: exact current-run index rows filtered by a plain `document_pair="d001|d007"` string/category/match mode (no JSON array coercion or repair) with original rank, totals and explicit cursors. Whole index rows fit a 10000-character response budget, and the cursor advances by actual delivered count to avoid generic12000-character preview externalization. Navigation is never counted as full passage delivery, and has a distinct recovery identity. Scoped HTML guidance exposes the existing delegation close thresholds (16 tool receipts or >=4 receipts and 200000 result characters), without selecting roles or making full enumeration a gate.

HTML M01 candidate reads return whole records within 60000 characters, explicit remaining IDs and exact oversized-file references; the bounded tool is exempt from generic preview externalization. Continuation is optional according to evidence needs; undelivered text cannot be claimed reviewed. Markdown transport and all comparison algorithms remain unchanged.

Tests: `test_collusion_reader_source_facts_v21.py`, `test_collusion_html_reports_v21.py`, `test_collusion_html_candidate_delivery_v21.py`, `test_collusion_html_candidate_navigation_v21.py`, `test_sp_html_report_language_v21.py`, `test_sp_registered_delegate_intent_v21.py`. Retain independent source freezes, full traces and costs, including unsuccessful trials. Never convert old Markdown reports or inject historical verdicts; no JSON repair.

HTML M01 索引补充：`audit_m01_index` 可选 `sort_by=shared_chars` 按真实共享字符数排序（同分保持原排名，默认仍为原排名），并按实际候选证据返回双端文档与物理页码。缺失定位明确披露，整行与位置映射共同受10000字符预算限制；游标按实际返回数推进。索引不是正文交付或语义复核，原始候选与规则、原生调度不变。回归见 `test_collusion_html_index_locations_v21.py`。

HTML输入精简：默认关闭的 `preloaded_task` 仅在HTML质量模式的非图像子代理启用。只有实际当前Human任务框架、action_id和服务端预载计算列表均匹配时，才将逐字段完全相同的事实改为同一请求中原文位置引用；额外差异、新证据、源回执及历史reasoning保留，原文件不改。框架被摘要移除或内容不等则恢复完整事实。M06保持原图/OCR观察路径。Reporter的读者指南只在system中保留一次；无去重语义台账时各阶段与报告采用定性覆盖描述。短阶段协议与完整HTML工具参数的长度要求区分。回归：`test_collusion_html_prompt_transport_v21.py`。

运行器等待保护：仅当显式角色或Central请求时限超过既有范围时，外层无可观察进展保护使用 `max(600, 显式请求时限+120)` 秒，避免较长的非流式请求被固定600秒提前终止。未配置或使用480/240秒时仍为600秒。请求重试、角色选择、token预算和JSON处理不变；实际值记录effective_config。回归：`test_collusion_progress_watchdog_v21.py`。

### Collusion incremental handoff and HTML acceptance v22

Trusted `SubagentConfig.model_thinking_enabled` defaults None and is applied after the run-scoped configuration extension; model action metadata cannot override it. Collusion `--researcher-no-thinking` defaults off and disables only Researcher thinking. Independent `--evidence-max-tokens` / `--evidence-request-timeout` retain explicit evidence capacities when Reporter grows; omitted values preserve the older shared-capacity behavior. No routing, recovery, action ceiling, historical reasoning, or JSON handling changes. Handoffs emphasize new evidence, corrections and unfinished delegated work; whole source data stays accessible.

HTML5 DOM parsing is in memory only, using html5lib. Legal omitted end tags do not fail delivery; file ownership/existence, actual nonempty body, explicit complete-page boundary, placeholders, internal identity leaks and active/external resources remain delivery errors. Section IDs, table counts, reader layout preferences and exact visible coverage-table phrasing are warnings only. Existing factual metadata and source-receipt scope validation remain independent. A bounded cache reuses format reviews for identical file content; raw reports are never rewritten. Tests: `test_collusion_incremental_html_v22.py` and existing HTML/resource/native-extension suites.

### Collusion resilient delivery and Alibaba API v23

Trusted `SPExecutionExtensions.allow_limited_report` defaults False. When enabled, exhausted non-Reporter recovery retains Central control choices and a Reporter delegation; it does not schedule Reporter automatically or promote partial results. Further evidence delegation after exhaustion is rejected by the control-tool wrapper. Missing source scope counts only the unresolved chain since the latest successful same-role stage. Existing default behavior, report validity and report recovery remain unchanged.

`SubagentConfig.tool_call_limit` defaults None; explicit64 on evidence specialists keeps native loop detection with sufficient graph shutdown steps (400), while repeated-identical-call detection and timeouts remain enabled. Both the agent prompt and real loop guard use the same limit. `--resilient-delivery` is a trusted quality/rule-mode collusion opt-in. It disables only the extra count/volume/repeated-receipt tool withdrawal, not source isolation, JSON validation or report-created feedback. Candidate requests may exceed8IDs; each complete record is delivered within a conservative512000-byte transfer allowance with explicit remaining/oversized references. File reads default to the complete source within that allowance; explicit offsets and truthful next offsets remain. This is a transport bound, not an audit-completion gate.

Producer-known m01_candidates files are registered with SHA and candidate identity; modified/model-authored files cannot supply trusted recovery identities. Primary current artifact references take precedence over unrelated historical stage references. Original source bodies stay unchanged. Alibaba configuration is process-local inside the experiment copy, uses ChatOpenAI and official enable_thinking, streaming usage and zero hidden SDK retries. It does not use vLLM tool-text recovery. Preflight uses synthetic data, stops on its first failure and is separately accounted; M01 starts only after passing. Secrets are redacted and must not enter source snapshots. Tests: `test_collusion_resilient_delivery_v23.py`, `test_sp_limited_report_v23.py`, `test_collusion_aliyun_v23.py` plus existing contracts.

### Collusion Central request ownership v24

Trusted `SPExecutionExtensions.central_recovery_owned` defaults false and
`central_model_overrides` defaults None. Only the copy's explicit Alibaba
`--central-efficiency` mode enables them. The lead factory passes a trusted
`propagate_model_errors` switch to its generic error middleware: no generic
request cutoff, retry, circuit fallback or assistant-text error conversion wraps
SP's Central recovery. Specialist chains keep native defaults. Central's existing
initial/post-delegation retry and once-per-phase THINK checkpoint remain intact;
transport recovery additionally handles temporary connection/429/5xx failures,
while fatal errors and cancellation propagate. SDK retries remain zero.
Central uses 180s initial,480s post-delegation/retry,8192 output tokens,
reasoning_effort=low, HTTP read/stream gap510s,connect10s/write30s/pool30s.
Only exact adapter-generated task text is projected for Central; business rules,
manifest, evidence bodies, task memory, history and native prompts stay intact.
Detailed HTML layout/writer instructions remain in specialist/Reporter inputs.
No tool changes, stage forcing, historical reports, output repair or JSON repair.
Tests: test_collusion_central_v24.py plus native recovery/model/middleware suites.


### 围串标 Reporter 流式交付 v25（默认关闭）

仅围串标副本的阿里云 HTML 质量规则审核可显式启用 `--reporter-delivery`。Reporter 使用独立同模型配置、关闭 thinking，完整输出上限32768；建议单次模型请求900秒，HTTP connect10/read330/write60/pool30秒，SDK分片间隔与有效正文/工具参数进展间隔均300秒。整个原生委派3600秒及case14400秒不增加。完整请求超时不原样自动重发；仅无普通输出、30秒内的临时连接/429/5xx错误至多重试一次。Central仍自主决定所有角色与步骤。

`reporter_stream.py` 保留正文与工具参数的实时原始分片、精确时间、结束原因、供应商ID/usage；只记录思考字符数，不写思考正文。运行目录 `reporter-streams/<action_id>/` 中的 `.delta.jsonl`、`.content.partial.txt`、`.tool-*.arguments.partial.txt`、`.tool-*.html.partial` 和 `.status.json` 是观察记录，绝非已通过的报告。不补全JSON、HTML或工具调用；只有模型真实调用write_file后形成的文件才进入原生验收/FINISH。无有效输出或不完整流明确失败，不被当作空成功。

`reporter_delivery.py` 将同一实际任务JSON内完全相同的较长值改为本请求JSON Pointer，递归展开严格等于原始任务；完整规则、双端证据、不同观察、局限和历史推理不裁减。独立 `collusion-reporting-html` skill 替代此模式Reporter的Markdown skill；其他角色和默认技能不变。所有原件与旧结果保留，不向模型输入历史报告。

通用兼容接口 `SubagentConfig.model_errors_propagate=False`、`SPExecutionExtensions.count_missing_reports=False` 默认保持旧路径。仅此模式启用前者以使原生子代理返回真实失败，启用后者让未创建任何报告的失败也适用原生既有三次Reporter恢复上限；不伪造产物。没有报告时保留传输/协议主因，不追加缺少HTML/证据等级等次生诊断。回归：`test_collusion_reporter_v25.py`，并运行现有collusion/SP/模型/执行器测试。


### HTML 交付验收的定位反馈（2026-11-12）

围串标 HTML 模式沿用原有内部标识和运行字段检查，失败反馈增加匹配总数以及最多三处违规词和附近原文，供 Central 自主安排的修订使用。长报告不整页回传；定位示例有界，不引入新验收门槛。Reporter 指南要求按定位实际修订文件并检查同类残留，保持审核事实、引文和局限不变。宿主不替换报告文字、不修复 JSON，不改变角色选择、工具范围、预算、超时或原生三次报告恢复上限。Markdown 和未启用此 HTML 适配的调用不受影响。回归：`test_collusion_html_validation_feedback_v26.py`。

### Portable CollusionAudit inputs and shared knowledge

collusion_audit.prepare stages explicitly selected originals into a new project
root and optionally preserves published IDs after exact path and SHA256 checks.
The runner accepts trusted CLI project, rules, configuration, output and
credential paths. Only originals declared in the selected manifest enter tools.
Keep private inputs and runtime outputs out of Git.
shared_knowledge.restore reads existing RAGFlow resources, verifies source bytes,
remote scope, full chunk hashes and page bounds, then atomically publishes a
local KnowledgeStore. The shared mapping contains metadata only. Reject changed
remote content instead of silently rebuilding a published mapping.
Operator commands are in docs/COLLUSION_AUDIT.md.
