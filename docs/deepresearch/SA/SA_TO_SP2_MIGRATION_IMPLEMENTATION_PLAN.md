# ScaffoldAgent to StackPlanner2 Migration Implementation Plan

> Date: 2026-08-06
> Scope: migrate the useful ScaffoldAgent Lite workflow into StackPlanner2 using skills, specialist subagents, CentralAgent SOP constraints, and artifacts.
> Related note: `StackPlanner2/docs/deepresearch/SA/SA_METHOD_ABSTRACTION_AND_PROMPT_ESSENCE.md`

## 1. Goal

The migration should not copy `scaffold_agent_lite` as a Python pipeline into SP2 first.

The target design is:

```text
ScaffoldAgent method = SOP + specialist agents + skills + persisted artifacts
```

In SP2 terms:

- Skills encode the reusable methodology.
- Subagents execute each stage with role-specific prompts and skill allowlists.
- CentralAgent enforces the order of calls.
- Research Summary, ScopeTree, evidence bundles, and reports are persisted as artifacts or workspace files.

The first usable version should be intentionally simple:

```text
User request
  -> pre-research specialist
  -> outline/scaffold specialist
  -> reporter specialist
  -> optional quality check
  -> finish
```

The first version can generate the full report in one reporter call. Section-by-section report generation should be a later upgrade.

## 2. Current SP2 Hook Points

### 2.1 Skill loading

Lead-agent skills are progressively loaded. The lead prompt only advertises skill names/descriptions/paths or a compact skill index. The model loads a skill through `describe_skill` and `read_file`.

Relevant files:

- `backend/packages/harness/deerflow/agents/lead_agent/prompt.py`
- `backend/packages/harness/deerflow/skills/describe.py`
- `backend/packages/harness/deerflow/agents/middlewares/skill_activation_middleware.py`

SP subagents are different. A subagent loads the full `SKILL.md` content at startup according to `SubagentConfig.skills`.

Relevant files:

- `backend/packages/harness/deerflow/subagents/executor.py`
- `backend/packages/harness/deerflow/subagents/config.py`
- `backend/packages/harness/deerflow/subagents/registry.py`

### 2.2 StackPlanner specialist selection

StackPlanner CentralAgent does not call normal execution tools directly. It calls SP control actions, especially `sp_delegate`.

Relevant files:

- `backend/packages/harness/deerflow/sp/central/prompt.py`
- `backend/packages/harness/deerflow/sp/agent_tools.py`
- `backend/packages/harness/deerflow/sp/subagents/dr2_adapter.py`
- `backend/packages/harness/deerflow/subagents/builtins/sp_specialists.py`

Current SP specialists:

```text
researcher  -> external evidence retrieval
outline     -> outline / plan artifact
reporter    -> report synthesis and revision
perception  -> document/image/local artifact perception
coder       -> code, shell, exact local computation
```

The existing `sp-reporter` already loads:

```python
skills=["stackplanner-reporting"]
```

So the fastest first improvement is to enhance `skills/public/stackplanner-reporting/SKILL.md`.

## 3. Target Architecture

### 3.1 New skills

Add SA-specific skills under `skills/public/`.

Recommended files:

```text
skills/public/scaffold-preresearch/SKILL.md
skills/public/scaffold-outline/SKILL.md
skills/public/scaffold-reporting/SKILL.md
skills/public/scaffold-quality-gate/SKILL.md
```

If we want the smallest possible first patch, skip new skill names and merge the reporting method into:

```text
skills/public/stackplanner-reporting/SKILL.md
```

But the cleaner architecture is separate skills:

| Skill | Used by | Purpose |
| --- | --- | --- |
| `scaffold-preresearch` | researcher / pre-researcher | Query decomposition, multi-angle retrieval, iterative gap-directed search, Research Summary |
| `scaffold-outline` | outline / scaffold-outliner | Initial ScopeTree generation from user query, seed evidence, Research Summary, and lightweight AGM state |
| `scaffold-reporting` | reporter | ScopeTree-grounded report writing, stable candidate set, comparison tables, evidence-grounded synthesis |
| `scaffold-quality-gate` | reporter or verifier | Check coverage, citation discipline, section alignment, evidence gaps |

### 3.2 Specialist agent options

There are two migration options.

#### Option A: reuse existing SP specialists

Modify `backend/packages/harness/deerflow/subagents/builtins/sp_specialists.py`:

```python
RESEARCHER_CONFIG.skills = ["scaffold-preresearch"]
OUTLINE_CONFIG.skills = ["scaffold-outline"]
REPORTER_CONFIG.skills = ["stackplanner-reporting", "scaffold-reporting", "scaffold-quality-gate"]
```

This keeps the SP action space unchanged:

```text
sp_delegate(target_agent="researcher")
sp_delegate(target_agent="outline")
sp_delegate(target_agent="reporter")
```

Pros:

- Least runtime change.
- CentralAgent prompt already knows these roles.
- Existing artifact adapter and result contract continue to work.

Cons:

- Existing `researcher` and `outline` roles become more SA-shaped for all SP report workflows.
- Harder to compare old vs new behavior side by side.

#### Option B: add new internal specialists

Add new `SubagentConfig`s in `sp_specialists.py`:

```text
sp-scaffold-preresearcher
sp-scaffold-outliner
sp-scaffold-reporter
sp-scaffold-verifier
```

Example shape:

```python
SCAFFOLD_PRERESEARCHER_CONFIG = SubagentConfig(
    name="sp-scaffold-preresearcher",
    description="SA-style pre-research specialist that builds a Research Summary before outline generation.",
    system_prompt=_prompt("scaffold pre-researcher", "..."),
    tools=None,
    disallowed_tools=["task", "ask_clarification", "present_files", "bash", "write_file", "str_replace"],
    skills=["scaffold-preresearch"],
    model="inherit",
    max_turns=80,
    internal=True,
)
```

Pros:

- Clear role boundaries.
- Old SP specialists remain available.
- Easier ablation.

Cons:

- CentralAgent prompt and `sp_delegate` target schema currently allow only `researcher`, `coder`, `reporter`, `outline`, and `perception`.
- To use new target names directly, we must change `sp_delegate` type hints, normalization, routing, frontend status labels, and tests.

Recommendation: use Option A for V1. Consider Option B only after V1 quality is confirmed.

## 4. CentralAgent SOP Changes

The most important migration point is not only adding skills. CentralAgent must be constrained to call the stages in SA order.

Modify:

```text
backend/packages/harness/deerflow/sp/central/prompt.py
```

Add an SA-specific workflow block under the substantial report/document workflow rules.

Proposed SOP:

```text
For substantial deep-research report requests:

1. If no current Research Summary artifact exists for this run:
   delegate to researcher with stage="research".
   The task is SA pre-research: decompose the user query, run multi-angle retrieval,
   and produce a Research Summary artifact.

2. If no current ScopeTree/outline artifact exists:
   delegate to outline with stage="planning".
   Input refs must include the Research Summary artifact and relevant evidence refs.
   Expected output is not only a static outline. The outline specialist must run a
   lightweight AGM state update:
   - initialize the ScopeTree;
   - bind seed evidence to nodes as an Evidence Map;
   - mark sparse, conflicting, over-broad, and over-narrow nodes;
   - maintain active nodes, unresolved questions, expansion targets, and utility hints.
   Expected artifacts are ScopeTree, Evidence Map, and AGM State.

3. If the ScopeTree misses explicit user dimensions, candidate/object scope,
   comparison dimensions, or answer-oriented conclusion structure:
   delegate an outline revision before reporting.

4. Delegate to reporter with stage="reporting".
   Input refs must include user request, Research Summary artifact, ScopeTree artifact,
   and materialized evidence bodies.
   V1 reporter may generate one complete report in a single call.

5. Inspect completion_status and evidence_gaps.
   If complete, finish with the report artifact.
   If partial or blocked, delegate focused research or outline/reporter revision.
```

Important CentralAgent constraints:

- Do not skip outline for substantial reports unless the user explicitly asks for a quick one-shot answer.
- Do not ask reporter to search.
- Do not finish from a Research Summary or ScopeTree alone.
- Report generation must carry `input_refs` for Research Summary and ScopeTree artifacts.
- Human feedback on outline or report is pinned and must be passed into the next outline/reporter revision.

## 5. Artifact and Workspace Storage

### 5.1 What must be persisted

Persist these intermediate products:

```text
Research Summary
ScopeTree / outline
Evidence bundle or evidence observations
Final report
Quality check result
```

In SP2, large bodies should be stored as artifacts, not only task memory. Task memory should keep compact summaries and artifact refs.

### 5.2 ScopeTree storage format

Use both a machine-friendly and a human-friendly representation when practical.

Preferred artifact:

```json
{
  "summary": "ScopeTree generated for the research report.",
  "artifact_content": {
    "title": "root title",
    "instruction": "whole-report goal",
    "stable_candidate_set": ["optional"],
    "children": [
      {
        "title": "section title",
        "instruction": "what this section must answer",
        "doc_ids": ["D1", "D3"],
        "children": []
      }
    ]
  },
  "artifact_type": "outline",
  "artifact_metadata": {
    "format": "scope_tree_json",
    "source_research_summary_ids": ["..."],
    "explicit_dimensions": ["..."],
    "evidence_bindings": {}
  }
}
```

Optional workspace files:

```text
/mnt/user-data/workspace/research_summary.md
/mnt/user-data/workspace/scope_tree.json
/mnt/user-data/workspace/scope_tree.md
/mnt/user-data/outputs/report-<action_id>.md
```

Recommendation:

- For V1, rely on SP artifact content and refs first.
- Add workspace files when the body is long, useful for inspection, or needed by follow-up tools.
- For reports, continue writing final deliverables under `/mnt/user-data/outputs`.

### 5.3 Markdown ScopeTree format

If the outline specialist writes Markdown, use this shape:

```markdown
---
type: scope_tree
version: 1
source_research_summary: <artifact-id>
---

# Report Title

instruction: What the full report must accomplish.
stable_candidate_set: A, B, C

## Section A

instruction: What this section must answer.
evidence_refs: D1, D3

### Leaf A1

instruction: Concrete answer content for this leaf.
evidence_refs: D1
```

Rules:

- Every node has an instruction.
- Leaf instructions must be answer-oriented.
- Evidence gaps are not top-level sections.
- Stable candidate/object set must be visible when the user asks comparison, ranking, recommendation, forecasting, or evaluation.

## 6. Skill Content Requirements

### 6.1 `scaffold-preresearch`

This skill should tell the researcher to:

- Decompose the query before searching.
- Generate queries covering object type, time range, geography, metrics, comparison dimensions, and output form.
- Prefer official reports, primary sources, statistics, benchmarks, annual reports, rating agencies, standards, APIs, or high-quality domain sources.
- For comparison tasks, search by dimension instead of only by object.
- Iterate if the first evidence batch misses explicit user dimensions.
- Deduplicate by URL/title.
- Produce a Research Summary that is for planning and report alignment, not a citation source.

Expected output:

```json
{
  "summary": "compact result for CentralAgent",
  "artifact_content": "Markdown Research Summary with evidence inventory and planning implications",
  "artifact_type": "research_observation",
  "artifact_metadata": {
    "completion_status": "complete|partial|blocked",
    "queries": [],
    "source_artifact_ids": [],
    "explicit_dimensions": [],
    "stable_candidate_set": [],
    "evidence_gaps": []
  }
}
```

### 6.2 `scaffold-outline`

This skill should tell the outline specialist to:

- Build a ScopeTree from user query, Research Summary, and evidence refs.
- Prefer 3-5 first-level sections.
- Keep total leaf sections at or below 10 unless user explicitly requests a long report.
- Preserve every explicit user dimension.
- Define stable candidate/object set for multi-object tasks.
- For comparison/evaluation/recommendation/ranking/forecasting tasks, include:
  - candidate/object scope
  - dimension-by-dimension comparison
  - answer-oriented synthesis/conclusion
- Avoid standalone methodology, validation plan, evidence gap, source limitation, and future research sections.
- Add one instruction per node.
- Add evidence requirements or refs per leaf.
- Run a lightweight AGM state update during outline construction:
  initialize active nodes, bind seed evidence to nodes, mark evidence gaps or conflicts,
  identify expansion targets, and keep utility hints for later focused research or revision.

Expected output:

```json
{
  "summary": "compact ScopeTree summary",
  "artifact_content": {
    "title": "...",
    "instruction": "...",
    "children": []
  },
  "artifact_type": "outline",
  "artifact_metadata": {
    "completion_status": "complete|partial|blocked",
    "format": "scope_tree_json",
    "leaf_count": 0,
    "explicit_dimensions_covered": [],
    "stable_candidate_set": [],
    "evidence_gaps": [],
    "evidence_map": {},
    "agm_state": {
      "active_nodes": [],
      "evidence_gaps": [],
      "pending_queries": [],
      "expansion_targets": [],
      "utility_signals": []
    }
  }
}
```

### 6.3 `scaffold-reporting`

This skill should tell the reporter to:

- Treat ScopeTree as the writing contract.
- Use Research Summary for global task alignment only, not as a factual citation source.
- Use materialized evidence bodies for facts.
- V1: write the full report in one call, but internally follow the ScopeTree section order.
- Preserve stable candidate/object set across the whole report.
- Prefer horizontal comparisons and Markdown tables for multi-object/multi-dimension tasks.
- Do not drop an object just because evidence is weaker.
- For each major section, close the loop: facts -> analysis -> judgment for the user question.
- Do not turn missing evidence into the main structure.
- If facts are absent, write a concise evidence boundary in the relevant section.
- Include source list and evidence mapping when source URLs exist.

Expected output:

```json
{
  "summary": "compact completion note",
  "artifact_content": "full Markdown report or null if written to output file",
  "artifact_type": "report_revision",
  "artifact_metadata": {
    "completion_status": "complete|partial|blocked",
    "created_paths": [],
    "source_artifact_ids": [],
    "scope_tree_id": "...",
    "research_summary_id": "...",
    "quality_checks": [],
    "evidence_gaps": []
  }
}
```

### 6.4 `scaffold-quality-gate`

This skill can be loaded by reporter in V1 or by a separate verifier in V2.

It should check:

- User explicit dimensions are covered.
- ScopeTree sections are all represented.
- Stable candidate set is preserved.
- Comparison tasks are actually comparative, not object-by-object summaries.
- Recommendations/rankings/conclusions directly answer the query.
- Citations/source references come from supplied evidence.
- No Research Summary or logic skeleton is cited as evidence.
- Evidence gaps are disclosed but not over-amplified.
- Report is final readable Markdown, not notes, outline, or transport JSON.

## 7. Concrete File Changes

### 7.1 V1 minimal implementation

Modify or add:

```text
skills/public/scaffold-preresearch/SKILL.md
skills/public/scaffold-outline/SKILL.md
skills/public/scaffold-reporting/SKILL.md
skills/public/scaffold-quality-gate/SKILL.md
```

Modify:

```text
backend/packages/harness/deerflow/subagents/builtins/sp_specialists.py
```

Change skill allowlists:

```python
RESEARCHER_CONFIG.skills = ["scaffold-preresearch"]
OUTLINE_CONFIG.skills = ["scaffold-outline"]
REPORTER_CONFIG.skills = [
    "stackplanner-reporting",
    "scaffold-reporting",
    "scaffold-quality-gate",
]
```

If we want to preserve current researcher/outline behavior for non-SA tasks, do not hard-code these on the built-ins. Instead, update CentralAgent to pass `metadata.skill_names` in `sp_delegate`.

Modify:

```text
backend/packages/harness/deerflow/sp/central/prompt.py
```

Add the SA report workflow SOP and require:

- researcher uses `metadata.skill_names=["scaffold-preresearch"]`
- outline uses `metadata.skill_names=["scaffold-outline"]`
- reporter uses `metadata.skill_names=["scaffold-reporting", "scaffold-quality-gate"]` or relies on reporter default skill allowlist

Important implementation check:

- Verify whether `metadata.skill_names` currently narrows the specialist skill set in the delegate handler path. If it only appears in prompt text and is not enforced, implement enforcement before depending on it.
- If enforcement is not ready, use static `SubagentConfig.skills` allowlists for V1.

### 7.2 V1 safest implementation

If we want the lowest-risk first patch:

1. Add `scaffold-reporting` and `scaffold-quality-gate`.
2. Set reporter skills to:

```python
skills=["stackplanner-reporting", "scaffold-reporting", "scaffold-quality-gate"]
```

3. Enhance CentralAgent substantial-report SOP to require pre-research and outline before reporter.
4. Do not modify researcher/outline skill allowlists yet.
5. Put the pre-research and outline constraints directly into CentralAgent delegation task text.

This gives SA-style report generation without changing too many subagent defaults.

### 7.3 V2 section-by-section reporter

After V1 works, implement section writing.

Possible approaches:

1. Prompt-only section writing:
   - Reporter receives full ScopeTree and writes the full report section by section in one call.
   - It uses headings from ScopeTree and performs internal quality checks.

2. Multi-delegation section writing:
   - CentralAgent delegates one reporter task per leaf or per major section.
   - Each section returns a `section_draft` artifact.
   - A final reporter call merges sections into one report.

3. Runtime pipeline:
   - Add an SP report builder that reads ScopeTree, iterates leaves, invokes reporter subagents, stores section artifacts, then synthesizes.
   - This is closest to SA but has the largest implementation surface.

Recommendation:

- V1: full-report one call.
- V2: prompt-only section discipline inside one call.
- V3: real section artifacts and merge pipeline.

## 8. Suggested V1 Execution Contract

CentralAgent should delegate with concrete expected outputs.

### 8.1 Pre-research delegate

```text
target_agent="researcher"
stage="research"
task="Run SA-style pre-research for the user's report request. Decompose the query, run multi-angle searches, deduplicate sources, and produce a Research Summary for outline generation."
expected_output="A Research Summary artifact containing stable candidate/object set, explicit user dimensions, source inventory, evidence coverage, and concise evidence gaps."
metadata={"skill_names": ["scaffold-preresearch"]}
```

### 8.2 Outline delegate

```text
target_agent="outline"
stage="planning"
input_refs=["<research_summary_artifact_id>", "<evidence_refs>"]
task="Generate an SA ScopeTree from the user query, Research Summary, and evidence. Also run a lightweight AGM state update: bind evidence to nodes, mark gaps/conflicts, and identify expansion targets. The ScopeTree must be suitable for direct report writing."
expected_output="ScopeTree artifact with title, instruction, <=10 leaf sections, stable candidate set, explicit-dimension coverage, leaf-level evidence map/requirements, and AGM State."
metadata={"skill_names": ["scaffold-outline"]}
```

### 8.3 Reporter delegate

```text
target_agent="reporter"
stage="reporting"
input_refs=["<research_summary_artifact_id>", "<scope_tree_artifact_id>", "<evidence_artifact_ids>"]
task="Write the final report using the ScopeTree as the writing contract and the Research Summary as global alignment context. Use only supplied evidence for factual claims."
expected_output="A complete Markdown report artifact that covers every ScopeTree section, preserves stable candidate set, includes source list/evidence mapping, and passes quality checks."
metadata={"skill_names": ["scaffold-reporting", "scaffold-quality-gate"]}
```

## 9. Validation Plan

Use a small evaluation set before broader DRB runs.

### 9.1 Smoke cases

1. Simple current-information report:
   - Should run pre-research, outline, reporter.
   - Should not skip directly to reporter.

2. Comparison report:
   - ScopeTree must include stable candidate/object set.
   - Report must compare across common dimensions.

3. Ranking/recommendation report:
   - ScopeTree must include ranking criteria.
   - Report must give a direct recommendation/ranking.

4. Evidence-sparse report:
   - Report should disclose boundary concisely.
   - It should not make "missing information" the main structure.

5. Revision:
   - Reporter should preserve valid sections and citations.
   - It should produce a complete new report version.

### 9.2 Checks

Inspect run events and artifacts:

- Was Research Summary created before ScopeTree?
- Was ScopeTree created before report?
- Did reporter input include ScopeTree and Research Summary refs?
- Did final report use ScopeTree headings or section responsibilities?
- Did `artifact_metadata.quality_checks` mention explicit dimensions and evidence gaps?
- Did final report avoid citing Research Summary as a source?

## 10. Risks and Mitigations

| Risk | Cause | Mitigation |
| --- | --- | --- |
| CentralAgent skips SA stages | Existing prompt allows least-expensive workflow | Add explicit substantial-report SOP and finish guard language |
| Reporter ignores ScopeTree | ScopeTree appears only as summary, not materialized body | Ensure reporter receives materialized `context_refs.artifact_bodies.items` for outline |
| Research Summary becomes fake citation source | Summary contains temporary aliases like D1 | Skill must state Summary is alignment context only |
| Outline becomes methodology/gap report | Model over-focuses on missing evidence | `scaffold-outline` bans gap/methodology as main sections |
| Report drops weak-evidence objects | Reporter optimizes for easiest evidence | Skill requires stable candidate set and conservative coverage |
| Metadata skill routing is not enforced | `metadata.skill_names` may be prompt-only | Prefer static subagent skill allowlists until verified |
| Too many new agents break SP action schema | `sp_delegate` target enum is fixed | Reuse existing researcher/outline/reporter for V1 |

## 11. Recommended Implementation Order

### Phase 0: Documentation and design

- Keep this document as the engineering plan.
- Keep `SA_METHOD_ABSTRACTION_AND_PROMPT_ESSENCE.md` as the prompt/method source.

### Phase 1: Prompt-only V1

1. Add `scaffold-reporting` and `scaffold-quality-gate` skills.
2. Add these skills to `REPORTER_CONFIG.skills`.
3. Update CentralAgent substantial-report SOP:
   - pre-research first
   - outline second
   - reporter third
   - no finish from Research Summary or ScopeTree alone
4. Add tests or manual traces for one comparison report.

### Phase 2: Full SA skill set

1. Add `scaffold-preresearch`.
2. Add `scaffold-outline`.
3. Either:
   - statically attach them to `RESEARCHER_CONFIG` and `OUTLINE_CONFIG`, or
   - enforce `metadata.skill_names` in delegate routing and pass them from CentralAgent.
4. Validate Research Summary and ScopeTree artifacts.

### Phase 3: Section-aware reporting

1. Extend `scaffold-reporting` with section-by-section discipline.
2. Optionally make reporter create section artifacts.
3. Add a merge/revision step.
4. Add stronger verifier checks.

### Phase 4: SA utility loop, optional

Only after the static pipeline works:

- Add outline revision actions inspired by SA expansion/revision/contraction.
- Add a quality scorer/evaluator.
- Add targeted evidence refresh per leaf.
- Consider a runtime section pipeline instead of prompt-only section writing.

## 12. Bottom Line

The right migration unit is not a single prompt and not a direct code port.

The V1 migration should implement:

```text
SA methods as skills
SA stages as existing SP specialists
SA order as CentralAgent SOP
SA intermediate state as artifacts
```

The first report pipeline should be:

```text
pre-research -> Research Summary artifact
outline -> ScopeTree artifact
reporter -> full Markdown report artifact
quality gate -> metadata checks
finish
```

This gives SP2 the main benefit of ScaffoldAgent: report generation is anchored by a stable ScopeTree and Research Summary before the reporter writes.
