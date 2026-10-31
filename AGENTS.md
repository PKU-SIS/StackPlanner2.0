# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, Codex, and others) when working with code in this repository. It is the source of truth; the sibling `CLAUDE.md` imports it via `@AGENTS.md`.

It is the **monorepo orientation layer**: it maps the whole repo and points to the
module guides that own the depth. For anything inside a module, read that module's
guide rather than expecting full detail here:

- **[backend/AGENTS.md](backend/AGENTS.md)** — backend depth: harness/app split, agent &
  middleware chain, sandbox, MCP, skills, memory, IM channels, persistence/migrations,
  config system, test layout.
- **[frontend/AGENTS.md](frontend/AGENTS.md)** — frontend depth: Next.js App Router layout,
  thread/streaming data flow, code style, commands.

## What is DeerFlow

DeerFlow is a LangGraph-based AI super-agent system with a full-stack architecture. The
backend runs a "super agent" with sandboxed execution, persistent memory, subagent
delegation, and extensible tools (built-in, MCP, community), all per-thread isolated. The
frontend is a Next.js chat UI. External IM platforms (Feishu, Slack, Telegram, Discord,
DingTalk) bridge into the same agent through the Gateway.

## Service Topology

A single `make dev` / Docker stack runs four cooperating services:

| Service         | Port   | Role                                                                 |
| --------------- | ------ | ------------------------------------------------------------------- |
| **Nginx**       | `2026` | Unified reverse-proxy entry point — open this in the browser        |
| **Gateway API** | `8001` | FastAPI REST API + embedded LangGraph-compatible agent runtime      |
| **Frontend**    | `3000` | Next.js web interface                                               |
| **Provisioner** | `8002` | Optional — only when sandbox is configured for provisioner/K8s mode |

Nginx is the single public entry: it serves the frontend and proxies `/api/langgraph/*`
to the Gateway's LangGraph runtime, rewriting it to Gateway's native `/api/*` routes; all
other `/api/*` go straight to the Gateway REST routers. See
[backend/AGENTS.md](backend/AGENTS.md) for the runtime and router detail.

## Repository Map

```
deer-flow/
├── Makefile                        # Root orchestration: drives the full stack (dev/start/stop, docker, setup)
├── config.example.yaml             # Template → copy to config.yaml (gitignored) at repo root
├── extensions_config.example.json  # Template → copy to extensions_config.json (gitignored): MCP servers + skills
├── backend/                        # Python backend — see backend/AGENTS.md
│   ├── Makefile                    # Per-module backend commands (dev, gateway, test, lint, migrate-rev)
│   ├── packages/harness/           # deerflow-harness package (import: deerflow.*) — agent framework
│   └── app/                        # FastAPI Gateway + IM channels (import: app.*)
├── frontend/                       # Next.js frontend (pnpm) — see frontend/AGENTS.md
├── docker/                         # docker-compose files, nginx config, provisioner
├── skills/                         # Agent skills: public/ (committed), custom/ (gitignored)
├── contracts/                      # Cross-component JSON contracts (e.g. subagent status)
├── scripts/                        # Root orchestration scripts invoked by the Makefile (check, configure, doctor, support_bundle, serve, nginx, docker, deploy, setup_wizard)
├── tests/                          # Root-level tests (currently tests/skills/ — public skill tests)
└── docs/                           # Cross-cutting docs, plans, and design notes
```

Runtime config lives at the **repo root**: copy `config.example.yaml` → `config.yaml`
(main app config) and `extensions_config.example.json` → `extensions_config.json` (MCP
servers + skills). Both real files are gitignored and may be edited at runtime via the
Gateway API. Config schema and resolution order are documented in
[backend/AGENTS.md](backend/AGENTS.md).

`config.example.yaml` is the canonical copy/upgrade source and must not contain duplicate
mapping keys. `backend/tests/test_config_example_yaml.py` loads it with a strict
unique-key YAML loader so a later key cannot silently shadow an earlier setting.

Scheduled-task note:
- The scheduled-task MVP adds a workspace page at `/workspace/scheduled-tasks` plus a background scheduler service gated by `config.yaml -> scheduler.enabled`.
- Scheduled background runs are intentionally non-interactive: they execute through the normal run lifecycle, but the lead-agent toolset excludes `ask_clarification` when `context.non_interactive=true`. The key is honored only for internally-authenticated callers (the scheduler launch path); client-supplied `context.non_interactive` is dropped.

StackPlanner 2.0 is an optional orchestration policy inside the same Gateway
runtime. Its CentralAgent exposes only `sp_*` control actions; ordinary Tools
and Skills execute inside scoped subagents. The policy chooses direct, bounded,
or deliberate workflows, carries pinned feedback and bounded stage-artifact
bodies across delegations, and requires an explicit artifact-family contract
before finishing file-producing work. See `backend/AGENTS.md` for the handler,
memory, Qwen compatibility, and live policy-evaluation details.
The opt-in `/workspace/debug` execution-audit surface is backed by persisted
run events and `GET /api/threads/{thread_id}/runs/{run_id}/debug-trace`; it
shows explicit decisions, injected task-memory context, tool/subagent activity,
timing, and token attribution while redacting secrets and excluding
provider-private hidden chain-of-thought.

## Commands: Root vs. Module

**Root `make` targets drive the whole stack** (run from the repo root):

```bash
make setup       # Interactive setup wizard (recommended for new users)
make doctor      # Check configuration and system requirements
make support-bundle  # Generate redacted troubleshooting summary, AI issue draft, and optional zip
make config      # Generate local config files from the examples
make check       # Check that required tools are installed
make install     # Install all dependencies (frontend + backend + pre-commit hooks)
make dev         # Start all services with hot-reload (Gateway + Frontend + Nginx)
make start       # Start all services in production mode (local, optimized)
make stop        # Stop all running services
make up / down   # Build/stop the production Docker stack (browser at localhost:2026)
make docker-start / docker-stop / docker-logs   # Docker development environment
```

Run `make help` for the full list.

**Per-module commands drive a single module** (run inside that module):

```bash
# Backend (see backend/AGENTS.md for the full set)
cd backend && make dev        # Gateway API with reload (port 8001)
cd backend && make test       # Backend test suite
cd backend && make lint       # ruff check
cd backend && make format     # ruff format

# Frontend (see frontend/AGENTS.md for the full set)
cd frontend && pnpm dev       # Dev server with Turbopack (port 3000)
cd frontend && pnpm check     # Lint + type check (run before committing)
cd frontend && pnpm test      # Unit tests
```

Rule of thumb: **root `make` = the full application**; **`backend/Makefile` and `frontend/`
(`pnpm`) = per-module work.**

`scripts/serve.sh` accepts `GATEWAY_PORT`, `FRONTEND_PORT`, and `NGINX_PORT`
environment overrides for parallel worktrees. It validates and exports all three so
child processes, runtime diagnostics, SSR routing, and the generated Nginx config
observe the same selected ports.
The launcher also appends `127.0.0.1,localhost` to both `NO_PROXY` spellings so
internal Gateway, SSR, and local vLLM traffic cannot be diverted through a
machine-wide HTTP proxy.

## Where to Go Next

- Backend work → **[backend/AGENTS.md](backend/AGENTS.md)**
- Frontend work → **[frontend/AGENTS.md](frontend/AGENTS.md)**
- Setup & install → **[Install.md](Install.md)**, **[CONTRIBUTING.md](CONTRIBUTING.md)**
- Project overview & usage → **[README.md](README.md)** (translations: `README_zh.md`,
  `README_ja.md`, `README_fr.md`, `README_ru.md`)
- Security policy → **[SECURITY.md](SECURITY.md)**
- Changes → **[CHANGELOG.md](CHANGELOG.md)**

## DCC input preprocessing

`dcc_harness/preprocessing/` is the independent data ingestion layer, with
`Sample`/`Asset` contracts, trusted `module:function` loader/processor extensions,
content-addressed public assets, separate private labels, a resumable RAGFlow
index ledger, and file-based prompt composition. The CLI is
`scripts/dcc_preprocess.py`; configuration/prompt/Skill examples are under
`configs/dcc/`. See `docs/DCC_PREPROCESSING.md` for the artifact contract.

The optional `dcc_harness.preprocessing.native:make_plugin` uses the existing
`BenchmarkPlugin` interface. It stages public rules/context per user/thread and
registers sample-scoped `ragflow_retrieve`; each independent sample owns a
dedicated RAGFlow knowledge base, including samples from the same source dataset
or with identical document bytes. Related documents within one sample may share
its base. Index schema v2 rejects the old shared-dataset ledger. The adapter does
not modify the native agent loop and intentionally has no scorer yet. Keep private gold/source metadata and
credentials out of uploads, prompts, and RAGFlow documents. Never broaden a
sample's document IDs on an empty retrieval result.
Builders consume only PublicTask and use native stage_files/build_inputs.
Rendered files carry a public retrieval context and rules with neutral names;
record IDs, split and source paths stay in host-side manifests. After upgrading
the input contract, rerun render; indexing and the existing per-sample bases stay valid.
FinAuditing subtask instructions/output contracts live in
`configs/dcc/prompts/finauditing/` and are selected by `prompts.datasets` in its
example configuration. Keep task/output instructions inline, domain rules and
taxonomy in public reference files, and all answer labels in private artifacts.
FinAuditing baseline prompt files reproduce the original dataset instructions
verbatim. `releases.py` packages original Parquet/cards, a ready normalized bundle,
relative configuration and frozen prompts as a same-repository GitHub Release
asset. Pack verifies every normalized input/label against source rows and requires
empty processors; keep method optimizations in separately configured variants.
Release descriptors in `configs/dcc/releases/` pin the tag, asset and SHA-256.
`download` verifies the archive, complete file inventory and sample scopes before
atomically installing it; it requires no dataset access token or RAGFlow writes.
Private GitHub downloads use `gh auth login`; service keys remain local settings.
`ragflow.index_workers` (1–16, default 1) bounds concurrent independent samples,
uses one HTTP session per worker, and serializes resumable ledger checkpoints.

Short scenarios use `document_mode: inline`: prepare -> render, without index or
RAGFlow credentials. `native:make_inline_plugin` registers `dcc_inline`, stages
rules/context only and exposes no retrieval tool. Complete scenario text is both
in the prompt and PublicTask.document; the builder verifies it against public
assets, then omits the duplicate document upload. Preserve the RAGFlow default
and legacy schema-1 manifests without a document_mode field for FinAuditing.
`rulearena:load` is a module:function extension pinned by
`configs/dcc/sources/rulearena-nba.json`. Preserve all original NBA rows, complete
CBA rules, historical salary assumptions and zero-shot output requirements. True
means a violation; relevant_rules, offense labels, counts and complexity are host
metadata/labels, never policy inputs. Do not fix rule IDs, append missing salary
tables, or split complexity levels into train/validation/test. The raw evaluator
is provenance only: read prompt constants with AST and never execute it.
Release construction is injectable via `release_builder`; the NBA baseline checks
source/input/label parity and uses the shared archive/download implementation.
See `docs/DCC_RULEARENA_NBA.md` and `tests/test_dcc_rulearena.py`.

Run `python -m pytest tests/test_dcc_preprocessing.py tests/test_dcc_releases.py tests/test_dcc_rulearena.py -q`; with native SP
dependencies also run `tests/test_dcc_native.py --no-sp-runtime`. Use `ruff check` and `ruff format`
on changed Python files. Live indexing requires an explicit RAGFlow key supplied
by environment or a private file; keep generated bundles outside the source tree.

## Cross-Cutting Conventions

These apply repo-wide; module guides own the module-specific detail.

- **Documentation update policy** — keep docs in sync with code: update `README.md` for
  user-facing changes and the relevant `AGENTS.md` for development/architecture changes in
  the same change set.
- **Test-driven development** — features and bug fixes ship with tests. Backend tests live
  in `backend/tests/` (TDD is mandatory there; see [backend/AGENTS.md](backend/AGENTS.md));
  frontend tests live in `frontend/tests/`.
- **Format before pushing** — run `make format` (backend) / `pnpm check` (frontend). Backend
  CI enforces `ruff format --check`, so formatting must be clean before a push.

## Benchmark plugin lifecycle (DCC v3)

`scripts/infer_sp_benchmarks.py` collects native SP FINISH deliveries only;
`scripts/evaluate_sp_benchmarks.py` scores saved deliveries without executing SP.
`dcc_harness/data.py` separates private `Task` from `PublicTask`; only the latter
may reach prompt/input builders or answer parsers. UTF-8 inputs are staged in the
native per-user/thread uploads directory. Evaluation-only labels, metadata,
record IDs and source paths must not be injected into the agent contract.
Plugins own parsing, scoring, setup/resource provenance, and aggregation; do not
add benchmark-specific averaging branches to the shared runner. Preserve failed
predictions as failures and require complete manifests unless diagnostic partial
evaluation is explicitly requested. Keep native planning/delegation/FINISH intact.
See `docs/dcc_harness.md` for the v3 API and migration from the old combined entry.
Run root benchmark tests with `--no-sp-runtime` when real SP execution is excluded;
this guards graph construction and skips tests marked `sp_runtime`.

CLAUSE is implemented in `dcc_harness/datasets/clause.py` (plugin),
`clause_data.py` (pinned bundle conversion), and `dcc_harness/clause_metrics.py`
(reference alignment/metrics). Keep scoring imports lazy: ordinary inference and
CUAD must not require NLTK, Torch or BERTScore. Protocol/prompt resources live in
`configs/clause/`; documents, private annotations and models belong only in the
ignored `.benchmark-data/`. Do not expose source/category/record IDs through
filenames, skill selection or PublicTask. Preserve the pinned GPT DFS graph order,
original-defect counts, nullable similarity denominators and layered rounding.
CLAUSE validates whole pinned splits independently of run-manifest completeness.
See `docs/clause_benchmark.md` and `tests/test_clause_plugin.py` for reference-code
parity, mixed CUAD/CLAUSE delivery tests and optional real local encoder checks.

CLAUSE source reconstruction lives in `dcc_harness/datasets/clause_source.py`.
`dcc_prepare_clause.py` accepts exactly one of `--download`, `--source-archive`,
`--archive`, or `--ready-dir`, then uses the same public/private converter.
Authenticate the pinned source Git tree and all release file hashes; counts alone
are insufficient. Publish new directories only after verification, preserving
existing outputs. `configs/clause/source_rebuild/templates/` contains frozen
byte-authenticated data-protocol resources: never format/rewrite these files.
Historical upstream source containing credentials must remain excluded from Git;
reconstruction restores it only into ignored local provenance, never executes it.
See `tests/test_clause_source_rebuild.py` for tamper checks and optional fresh-source
parity tests. No reconstruction or adapter test may invoke an SP/model/Judge run.
