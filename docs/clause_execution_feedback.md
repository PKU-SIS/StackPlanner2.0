# CLAUSE file-mode execution

Current file mode uses the native Central control channel, numbered source reads and full-text searches. The quote checker is optional. The workflow and acceptance rules are documented in [CLAUSE file review](clause_autonomous.md).

## Historical staged experiment

The following describes the previous request adapter and its budgets.

The autonomous adapter states the task, shared input path, evidence scope and
final JSON-file contract. Central chooses roles and decomposition within separate evidence, verification and JSON delivery delegations. See [staged review](clause_autonomous.md) for final artifact ownership and strict export. L1/L2 prompt
files and their zero/one-shot examples, source texts, labels and metrics remain
unchanged. Final-file requirements do not replace native intermediate envelopes.

## Dataset-scoped tools and feedback

The CLAUSE plugin explicitly overrides two ordinary tools for its own runs:

- `read_file` returns the source document as numbered lines with exact source
  text, total lines, EOF, complete-file coverage, truncation and a continuation
  line. Other paths retain native read behavior, including final-file readback.
- `grep` searches the complete source, including long paragraphs. It accepts
  the source file or uploads directory, returns actual matching excerpts with
  line/column and character offsets, and explicitly marks result limits and regex timeouts.
  All occurrences are included, including multiple hits on one line; `next_offset`
  resumes a limited search. Excerpt truncation is distinct from search completeness.
  Other paths retain native search behavior. A zero-match complete scan differs
  from an incomplete scan. Install `configs/clause/requirements-inference.txt`.

Native grep silently skips lines over 2,000 characters and previews only the
start of matching lines. Those defaults are unsuitable for long contract
paragraphs; this adapter does not change grep behavior for CUAD or RAG.

A trusted `model_request_adapter` receives only the already model-visible
request, never Task labels or evaluation resources. CLAUSE uses it to append a
transient evidence-availability reminder after a complete numbered source read
or an unchanged/already-covered read-only result. The source, previous messages,
model parameters and full tool choices remain available during normal execution. Errors, new evidence,
mutations and a new user request invalidate duplicate evidence as appropriate.
When the native loop guard emits its named **identical-call** warning for a
specialist, the CLAUSE adapter makes that recovery response result-only. It
omits tool bindings for that response and asks for the native complete/partial/
blocked envelope using existing evidence. Central control tools are never
restricted. A native total-call stop warning also permits a result-only response in a read-only evidence or verification delegation once the required reads are present. The same result-only turn applies after 14 observed reads in a scoped pass with its prerequisite reads. A successful check of every quotation in a just-written owned answer also hands control back to Central. Central can reopen the review; partial and blocked results retain their native status. Frequency warnings alone do not activate this mechanism.
This follows an already-issued native stop request, avoids another identical
call consuming the hard-stop allowance, and does not declare the task successful.
It neither forces completion nor manufactures an empty answer. The original
SP state and trace remain intact; the adapter identity and source hashes are
recorded in the inference manifest. RAG and CUAD do not enable this adapter.

Central's API requests use `tool_choice=required` only when the available tools
are exclusively native `sp_*` controls and no explicit native choice overrides
that default. The model still chooses which control action and role to use; this
prevents ordinary completion prose from ending the graph without an accepted
`sp_finish`. Specialist requests keep normal automatic tool choice so they can
return their result envelopes without another tool call. Existing explicit native
choices are preserved. This is a protocol boundary, not a prescribed plan.

For Central's `sp_delegate` tool, the CLAUSE adapter also documents the existing
native data-processing metadata (`coder_task_kind=data_processing` and
`requires_implementation=false`) if Central chooses Coder for computation,
JSON creation or validation. It changes help text only, not arguments or role
selection. This prevents an underspecified data task from accidentally inheriting
software-source-change and software-test acceptance requirements; native data
execution and artifact checks remain mandatory.

A second factual reminder explains a native Coder-to-Perception routing rejection
when a completed Perception stage cannot be reopened. It is attached only to the
matching `sp_delegate` error and leaves all Central controls intact. It names no
replacement role and never changes the requested action or native routing policy.

## Native runtime corrections and budgets

Three narrowly scoped corrections remain in native loop detection: different
read_file line ranges no longer share a coarse 200-line identity, and a total
budget of N admits N calls before rejecting an excess call. Search identity also
includes literal/case modes, limits and pagination instead of conflating corrected
queries. Genuine repeated
calls, overall limits and acceptance checks remain active. No Central planning,
role prompt, delegation selection or completion gate is changed.

Use a fresh run and an explicit configuration for autonomous experiments.
For example, start from `configs/stackplanner_benchmarks.yaml`, set
`sandbox.read_file_output_max_chars: 262144`, and set
`subagents.agents.sp-perception.max_turns: 128` (with the prior reporter budget
also recorded). Pass `--max-concurrent-subagents 3`. These are available budgets,
not required numbers of roles or stages. `--central-timeout` and
`--central-retry-timeout` expose existing native model-call deadlines (native
defaults remain 90/60 seconds when omitted); `--timeout` bounds the entire sample.
Thinking or high concurrency can need longer model-call deadlines. These
options are recorded in manifests and do not alter planning decisions. Budget changes must be reported when
comparing against prior experiments. Never resume an old manifest with them.

Keep model reasoning mode explicit in the frozen runtime configuration. For Qwen
services this is `models[].extra_body.chat_template_kwargs.enable_thinking`.
Do not silently compare an enabled-thinking SP run with a disabled-thinking
direct baseline as an isolated estimate of orchestration gain. In local paired
request probes, enabling thinking with the same evidence-availability reminder
resolved repeated reads that persisted with thinking disabled; this is empirical
evidence for those requests, not a guarantee for every model or document.

## Validation and result handling

Use independent validation samples spanning short, paragraph-heavy and large
contracts. Check actual tool responses and accepted JSON files, then run the
unchanged offline evaluator. Unit checks alone do not establish model reliability.
Preserve failed and interrupted attempts separately; do not repair model JSON,
accept intermediate artifacts, or count interrupted reviews as `[]`. Full runs
report coverage and failure reasons alongside valid-subset and common-subset
metrics. More roles or calls alone do not demonstrate a reasoning improvement.
