# ScaffoldAgent 迁移到 StackPlanner2 的具体实现方案

> 日期：2026-08-06
> 目标：把 ScaffoldAgent Lite 中真正有效的部分迁移到 SP2，优先用 skills 和现有 SP 子智能体实现，并尽量不影响其他功能。
> 方法参考：`SA_METHOD_ABSTRACTION_AND_PROMPT_ESSENCE.md`

## 0. 先说结论：SA 重点不是 reporter，而是报告前结构控制

参考 `SA_METHOD_ABSTRACTION_AND_PROMPT_ESSENCE.md`，SA 的核心不是“最后让 reporter 写得更好”，而是先建立三个中间状态：

```text
Research Summary
ScopeTree
Evidence Map
```

它们分别解决：

| SA 状态 | 作用 | 如果没有它会怎样 |
| --- | --- | --- |
| `Research Summary` | 预检索后形成全局任务理解，稳定对象、维度、时间地域、证据边界和回答方向 | outline 容易只看用户 query，结构失焦 |
| `ScopeTree` | 报告大纲，同时也是检索路由、证据绑定和写作约束 | reporter 容易自由发挥，章节不稳定 |
| `Evidence Map` | 记录每个节点绑定哪些证据，避免全局资料乱用 | 事实引用边界混乱，弱证据对象容易被删 |

所以迁移的核心目标应该是：

```text
不要让 reporter 最后凭一个长 prompt 自由写。
要先让 SP2 生成 Research Summary，再生成 ScopeTree，再让 reporter 根据 ScopeTree 和 Evidence Map 写报告。
```

## 1. 到底要改什么

如果我们尽量不影响 SP2 其他功能，第一版不新增新的 `target_agent`，不改通用 lead agent，不搬 SA Python pipeline。

第一版要改四类东西：

```text
1. 新增 SA 方法 skills
2. 让现有 SP 子智能体加载或遵守这些 skills
3. 让 CentralAgent 在深度报告场景按 SA 顺序调用子智能体
4. 让 Research Summary / ScopeTree / Evidence Map 作为 artifact 保存和传递
```

改动清单：

| 要改什么 | 是否必须 | 文件 / 路径 | 影响范围 |
| --- | --- | --- | --- |
| 新增 SA skills | 必须 | `skills/public/scaffold-*/SKILL.md` | 很小，只新增文件 |
| 让 reporter 加载 SA reporting skill | 必须 | `backend/packages/harness/deerflow/subagents/builtins/sp_specialists.py` | 只影响 SP reporter |
| 让 researcher / outline 具备 SA 预检索和 ScopeTree 能力 | 建议必须 | 同上，或通过 CentralAgent delegation task 约束 | 影响 SP researcher / outline 的报告场景 |
| 增加 CentralAgent SA 报告 SOP | 必须 | `backend/packages/harness/deerflow/sp/central/prompt.py` | 影响 SP substantial report 流程 |
| 新增 SA 专用 target agents | 暂缓 | `sp_specialists.py` + `sp/agent_tools.py` 等 | 较大，不建议 V1 |
| 迁移 SA utility / 完整 AGM action loop | 暂缓 | 多个 runtime 模块 | 大，不建议 V1；但 outline 阶段应保留轻量 AGM State |

最小但真正有效的 V1：

```text
新增 scaffold-preresearch / scaffold-outline / scaffold-reporting / scaffold-quality-gate skills
+ 复用现有 researcher / outline / reporter 三个子智能体
+ 给 CentralAgent 加 SA report SOP
+ 用 artifacts 传 Research Summary / ScopeTree / Evidence Map
```

## 2. 子智能体要不要创建

应该有对应的检索、大纲、报告智能体。

但 V1 不建议新增新的 `target_agent` 名字，因为 SP2 已经有现成角色：

```text
researcher  -> 可作为 SA 预检索智能体
outline     -> 可作为 SA ScopeTree 生成智能体
reporter    -> 可作为 SA 报告生成智能体
```

推荐 V1 这样映射：

```text
SP researcher + scaffold-preresearch skill = SA 预检索智能体
SP outline    + scaffold-outline skill     = SA 大纲 / ScopeTree 智能体
SP reporter   + scaffold-reporting skill   = SA 报告智能体
SP reporter   + scaffold-quality-gate skill = SA 质量检查能力
```

这样概念上已经有 SA 对应智能体，但代码上仍然复用原来的：

```text
sp_delegate(target_agent="researcher")
sp_delegate(target_agent="outline")
sp_delegate(target_agent="reporter")
```

为什么不 V1 新增 `sp-sa-preresearcher` 这类名字：

- 当前 `sp_delegate.target_agent` 基本固定为 `researcher/coder/reporter/outline/perception`。
- 新增 target 需要改 schema、路由、状态展示和测试。
- 复用现有角色可以最小化影响范围。

如果后续要做 ablation 或同时保留旧流程，再考虑新增：

```text
sp-sa-preresearcher
sp-sa-outliner
sp-sa-reporter
```

## 3. 应该新增哪些 skills

V1 应该新增四个 skill，而不是只新增 reporter skill。

```text
StackPlanner2/skills/public/scaffold-preresearch/SKILL.md
StackPlanner2/skills/public/scaffold-outline/SKILL.md
StackPlanner2/skills/public/scaffold-reporting/SKILL.md
StackPlanner2/skills/public/scaffold-quality-gate/SKILL.md
```

V1 不新增 `central-agent` / `lead-agent` orchestration skill。

原因：

- 当前 CentralAgent 只看到 skill index，用它来决定 `metadata.skill_names`。
- 完整 `SKILL.md` body 是由被 delegate 的子智能体加载，不是 CentralAgent 加载。
- 因此，把 SA 调度顺序完全写进普通 skill 里不稳定；CentralAgent 可能根本不会读取完整 SOP。

V1 采用的边界：

```text
CentralAgent prompt:
  只写很窄的触发条件和调用顺序。
  substantial research/report/document -> researcher -> outline -> reporter

Subagent skills:
  写每一步具体怎么做。
  researcher -> scaffold-preresearch
  outline    -> scaffold-outline
  reporter   -> scaffold-reporting + scaffold-quality-gate
```

如果后续要做真正的 `scaffold-deepresearch-orchestration` skill，需要先改 runtime：让 CentralAgent 在命中特定场景时注入该 orchestration skill 的正文，而不只是看到 skill index。

### 3.0 方法精华覆盖矩阵

实现这些 skills 时，不只是参考概念，还要优先参考 `SA_METHOD_ABSTRACTION_AND_PROMPT_ESSENCE.md` 第 7-8 节列出的 SA prompt 入口。

| SA 方法精华 | 参考 prompt / 入口 | SP2 V1 落点 | V1 处理 |
| --- | --- | --- | --- |
| 预检索 query 规划 | `pre_research.py::generate_queries()` | `scaffold-preresearch` | 迁移 |
| 迭代式预检索规划 | `pre_research.py::plan_next_query()` | `scaffold-preresearch` | 首版不做完整迭代，但保留 gap-directed query 思路 |
| Research Summary 生成 | `pre_research.py::generate_brief()` | `research_observation` artifact | 迁移 |
| Initial ScopeTree 生成 | `pipeline.py::_generate_initial_outline()` | `scaffold-outline` | 迁移 |
| 非回答型章节过滤 | `pipeline.py::_remove_non_answer_outline_sections()` | `scaffold-outline` | 迁移 |
| node-evidence binding | Evidence Map / `doc_ids` | outline artifact metadata | 迁移 |
| 轻量 AGM State | 从 ScopeTree + Evidence Map 派生 | outline artifact metadata | 迁移状态层，不迁完整 loop |
| 报告前证据刷新 | `_refresh_report_evidence()` / `_build_report_refresh_queries()` | focused research 或 Research Summary refresh | V1 可选轻量做 |
| 统一章节写作约束 | `reporter.py::_build_section_prompt()` | `scaffold-reporting` | 迁移为整篇报告约束 |
| 两阶段 reporter | `_build_insight_prompt()` / `_build_two_stage_section_prompt()` | Phase 3 reporter 增强 | V1 暂缓 |
| Expansion / Revision / Contraction | `actions.py::expand()` / `revise()` / `contract()` | Phase 4 outline revision | V1 暂缓，但保留约束思想 |
| Utility / reward / citation verifier / NLI | evaluator / verifier 相关逻辑 | Phase 4+ verifier | V1 暂缓 |

V1 写 `SKILL.md` 时，要把 prompt 精华转成可执行约束，而不是照搬 SA pipeline：

```text
scaffold-preresearch:
  参考 generate_queries / plan_next_query / generate_brief

scaffold-outline:
  参考 _generate_initial_outline / _remove_non_answer_outline_sections

scaffold-reporting:
  参考 _build_section_prompt，把 section 约束提升为整篇报告约束

scaffold-quality-gate:
  参考 reporter prompt 里的 coverage / citation / evidence boundary 约束
```

### 3.1 `scaffold-preresearch`

给 `researcher` 用。

对应 SA 的：

- `pre_research.py::generate_queries()`
- `pre_research.py::plan_next_query()`
- `pre_research.py::generate_brief()`

要实现的重点：

- 搜索前先拆解用户问题。
- query 覆盖对象类型、时间范围、地域范围、比较维度、指标词、权威来源倾向词。
- 比较型任务按维度拆 query，不要只搜总览。
- 排名 / 推荐 / 评估任务要搜评价口径、指标、候选对象范围。
- 不在用户没有给定时硬编码具体机构。
- 检索结果去重。
- 输出 `Research Summary`。
- `Research Summary` 是规划上下文，不是最终事实引用来源。

输出建议：

```json
{
  "summary": "预检索完成，形成 Research Summary。",
  "artifact_content": "Markdown Research Summary",
  "artifact_type": "research_observation",
  "artifact_metadata": {
    "completion_status": "complete|partial|blocked",
    "queries": [],
    "explicit_dimensions": [],
    "stable_candidate_set": [],
    "evidence_gaps": [],
    "source_refs": []
  }
}
```

### 3.2 `scaffold-outline`

给 `outline` 用。

对应 SA 的：

- `pipeline.py::_generate_initial_outline()`
- `pipeline.py::_remove_non_answer_outline_sections()`
- `Evidence Map / node-evidence binding`

要实现的重点：

- 输入必须包含用户 query、Research Summary、seed evidence。
- 输出 `ScopeTree`，不是普通大纲。
- outline 阶段必须包含轻量 AGM，不只是一次性生成静态大纲。
- AGM 在 V1 不等于完整多轮 action loop；它先作为 outline artifact 的状态层存在。
- AGM State 至少记录 active nodes、evidence gaps、pending queries、expansion targets、utility signals。
- 一级章节优先 3-5 个。
- 总 leaf 默认不超过 10 个。
- 每个节点必须有 `instruction`。
- 用户显式维度必须进入章节、子章节或 instruction。
- 多对象任务必须定义稳定候选集合。
- 比较 / 评估 / 推荐 / 排名 / 预测任务必须包含：
  - 候选对象范围
  - 分维度比较
  - 综合判断 / 建议 / 结论
- 不把 evidence gap、source limitation、methodology、future research 写成主章节。
- 每个 leaf 要有 evidence refs 或 evidence requirements，形成 Evidence Map。

输出建议：

```json
{
  "summary": "ScopeTree generated.",
  "artifact_content": {
    "title": "root title",
    "instruction": "whole report goal",
    "stable_candidate_set": [],
    "children": [
      {
        "title": "section title",
        "instruction": "what this section must answer",
        "doc_ids": [],
        "children": []
      }
    ]
  },
  "artifact_type": "outline",
  "artifact_metadata": {
    "completion_status": "complete|partial|blocked",
    "format": "scope_tree_json",
    "leaf_count": 0,
    "explicit_dimensions_covered": [],
    "evidence_map": {},
    "evidence_gaps": [],
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

### 3.3 `scaffold-reporting`

给 `reporter` 用。

对应 SA 的：

- `reporter.py::_build_section_prompt()`
- V1 暂时不迁 `two-stage reporter`
- V1 暂时不做每个 leaf 单独写

要实现的重点：

- ScopeTree 是写作契约。
- Research Summary 只做全局任务对齐，不作为事实引用来源。
- 事实只来自 materialized evidence bodies。
- V1 reporter 一次性生成完整报告，但必须按 ScopeTree 的章节责任写。
- 稳定候选集合贯穿全文。
- 多对象 / 多维度任务要横向比较，优先表格。
- 不因某对象证据弱就删除对象。
- 每个主要章节要完成“事实 -> 分析 -> 判断”闭环。
- 推荐 / 排名 / 预测 / 评估类任务必须直接给结论。
- 缺证只在相关章节简短说明 evidence boundary，不写成主线。
- 输出完整 Markdown 报告。

### 3.4 `scaffold-quality-gate`

给 `reporter` 或后续 verifier 用。

要实现的重点：

- 用户显式要求是否覆盖。
- ScopeTree 章节是否都被写到。
- 节点 instruction 是否被遵守。
- 稳定候选集合是否贯穿全文。
- 比较任务是否真正在横向比较。
- 结论是否直接回答问题。
- 引用是否来自证据，不来自 Research Summary 或 logic skeleton。
- evidence gaps 是否披露但没有喧宾夺主。
- 输出是否是最终报告，不是 outline、notes 或 JSON envelope。

V1 可以让 reporter 在 `artifact_metadata.quality_checks` 中记录自检。

## 4. 要改哪些代码文件

### 4.1 必须改：让 skills 真正加载进子智能体

文件：

```text
StackPlanner2/backend/packages/harness/deerflow/subagents/builtins/sp_specialists.py
```

推荐 V1 修改：

```python
RESEARCHER_CONFIG.skills = ["scaffold-preresearch"]
OUTLINE_CONFIG.skills = ["scaffold-outline"]
REPORTER_CONFIG.skills = [
    "stackplanner-reporting",
    "scaffold-reporting",
    "scaffold-quality-gate",
]
```

如果担心 researcher / outline 被全局影响，可以先只静态改 reporter：

```python
REPORTER_CONFIG.skills = [
    "stackplanner-reporting",
    "scaffold-reporting",
    "scaffold-quality-gate",
]
```

然后通过 CentralAgent delegation task 明确要求 researcher 做 SA 预检索、outline 做 SA ScopeTree。

但要注意：

```text
只新增 SKILL.md 文件不会自动生效。
subagent 必须在 SubagentConfig.skills 里加载它，或者 runtime 必须支持 metadata.skill_names 动态加载。
```

### 4.2 必须改：CentralAgent SA 报告 SOP

文件：

```text
StackPlanner2/backend/packages/harness/deerflow/sp/central/prompt.py
```

原因：

skills 只能告诉某个子智能体“被调用后怎么做”。

当前 SP2 里，CentralAgent 只能看到 available skills 的索引，并在 `sp_delegate.metadata.skill_names` 里选择给哪个子智能体加载哪些 skills；它不会像子智能体一样自动加载完整 `SKILL.md` 正文。因此这里不能只靠一个普通 skill 承担流程编排。

但 SA 的核心顺序：

```text
pre-research -> Research Summary -> ScopeTree -> Evidence Map + AGM State -> Report
```

是 CentralAgent 决定的。

所以如果不改 CentralAgent，流程不稳定。它可能直接调 reporter，导致 reporter 没有 Research Summary 和 ScopeTree 可用。

建议加很窄的 SOP，只作用于 substantial research/report/document tasks：

```text
For substantial research/report/document tasks, use the SA-style scaffolded report workflow unless the user explicitly asks for a quick one-shot answer:

1. Pre-research first.
   If no current Research Summary or research observation exists for this run,
   delegate to researcher with stage="research".
   It must decompose the query, search from multiple dimensions, deduplicate sources,
   and produce a Research Summary for outline generation and report alignment.

2. ScopeTree + AGM second.
   If no current ScopeTree/outline artifact exists, delegate to outline with stage="planning".
   Input refs must include the Research Summary and relevant evidence refs.
   The outline must produce a ScopeTree with node instructions, Evidence Map, and lightweight AGM State.
   AGM State tracks active nodes, evidence gaps, pending queries, expansion targets, and utility signals.

3. Report third.
   Delegate to reporter with stage="reporting".
   Input refs must include the user request, Research Summary artifact,
   ScopeTree artifact, and materialized evidence bodies.
   Reporter must not search. Missing evidence must be returned as evidence_gaps.

4. Finish only after report.
   Do not finish from Research Summary or ScopeTree alone.
   Inspect reporter completion_status and evidence_gaps.
```

这个 SOP 不放复杂方法细节，只负责触发和编排。复杂方法细节仍放在四个 `scaffold-*` skills 里。

### 4.3 V1 不改：新增 target agent

V1 不改：

```text
StackPlanner2/backend/packages/harness/deerflow/sp/agent_tools.py
StackPlanner2/backend/packages/harness/deerflow/sp/subagents/dr2_adapter.py
StackPlanner2/backend/packages/harness/deerflow/subagents/registry.py
```

因为 V1 不新增 `sp-sa-preresearcher` 这种 target。

继续使用：

```text
target_agent="researcher"
target_agent="outline"
target_agent="reporter"
```

### 4.4 V1 不改：SA Python pipeline

V1 不迁移这些：

```text
完整 AGM / 多轮 action loop
Utility / reward scorer
embedding relevance / novelty
two-stage reporter
section-by-section DFS writer
citation verifier / NLI
```

这些是后续增强，不是首版必要条件。注意：这里暂缓的是完整多轮 AGM action loop；V1 的 outline artifact 仍应输出轻量 AGM State，帮助后续 focused research、outline revision 和 quality gate 接续。

## 5. Research Summary / ScopeTree / Evidence Map 怎么保存

### 5.1 V1 最小保存方式

使用 SP2 现有 artifact contract：

```text
Research Summary -> artifact_type="research_observation"
ScopeTree         -> artifact_type="outline"
Report            -> artifact_type="report_revision"
```

task memory 只保存短摘要和 artifact refs。

### 5.2 ScopeTree 是否要写成 md 文件

V1 不强制，但建议后续写。

V1 可以先让 outline artifact 的 `artifact_content` 是 JSON ScopeTree。

后续 V2/V3 再写 workspace 文件：

```text
/mnt/user-data/workspace/research_summary.md
/mnt/user-data/workspace/scope_tree.json
/mnt/user-data/workspace/scope_tree.md
/mnt/user-data/outputs/report-<action_id>.md
```

为什么后面要写：

- reporter 可以稳定读取。
- verifier 可以检查报告是否覆盖 ScopeTree。
- revision 可以基于同一版 ScopeTree。
- section writer 可以逐 leaf 生成。

## 6. V1 推荐执行流程

CentralAgent 在 substantial report 场景下应形成这个流程：

```text
1. researcher
   输入：user query
   使用：scaffold-preresearch
   输出：Research Summary + seed evidence

2. outline + AGM
   输入：user query + Research Summary + seed evidence
   使用：scaffold-outline + lightweight AGM state update
   过程：
     - 初始化 ScopeTree
     - 将 seed evidence 绑定到节点，形成 Evidence Map
     - 标记证据稀疏、冲突、过宽/过窄的节点
     - 维护 active nodes / unresolved questions / expansion targets / utility signals
   输出：ScopeTree + Evidence Map + AGM State

3. reporter
   输入：user query + Research Summary + ScopeTree + materialized evidence bodies
   使用：stackplanner-reporting + scaffold-reporting + scaffold-quality-gate
   输出：完整 Markdown report

4. CentralAgent
   检查 completion_status / evidence_gaps
   complete -> finish
   partial / blocked -> focused research 或 reporter revision
```

### 6.1 需要补强的四个产品化重点

除了 SA 核心结构迁移，V1 还要把下面四件事纳入实现范围，否则即使 prompt 链路跑通，后续也很难稳定评估和迭代。

| 重点 | 要解决的问题 | V1 建议做法 | 验收标准 |
| --- | --- | --- | --- |
| 人机交互的大纲确认 | ScopeTree 直接进入 reporter，容易把错误结构放大成整篇报告问题 | 在 outline 生成后增加可选确认点：展示 ScopeTree、稳定候选集、核心维度、证据缺口；用户确认后再进入 reporter，用户修改意见必须作为 pinned feedback 传给 outline/reporter | 有需要时流程停在 outline review；用户反馈能进入下一版 ScopeTree 或 reporter 输入；不能从未确认的大纲直接生成长报告 |
| 字数控制、等待时间与 AGM 的取舍 | 长报告质量、延迟、成本互相牵制；完整 AGM 还没实现，不能把它写成已完成能力 | V1 明确提供 report profile：quick / standard / deep。quick 跳过人工确认和复杂修订；standard 使用 Research Summary + ScopeTree + 一次 reporter；deep 才启用大纲确认、focused research、更多 revision。AGM 在 V1 只实现 lightweight AGM State，不实现完整 action loop | 每次 run 的 trace 里记录 profile、目标字数、实际字数、耗时、是否人工确认、是否触发 focused research；文档中明确完整 AGM 属于 Phase 4 |
| 报告引用升级 | 报告引用如果只靠 Research Summary 或零散来源，后续很难查证，也容易引用错位 | reporter 只能引用 materialized evidence bodies；ScopeTree leaf 需要绑定 evidence refs；最终报告保留 source list / evidence mapping；禁止把 Research Summary 当事实来源引用 | 每个关键事实能追到 source artifact / URL / doc id；final report metadata 记录 source_artifact_ids、scope_tree_id、research_summary_id、citation_coverage |
| SP2 trace 数据整理与保存 | 后续跑数据集、做 ablation、复盘失败样例，需要结构化 trace，而不是只看自然语言日志 | 为每次 scaffolded report run 保存结构化 trace：delegation 顺序、input_refs/output_artifacts、skill_names、completion_status、evidence_gaps、quality_checks、字数/耗时/token 或成本字段 | 能从 trace 批量抽取：是否按 researcher -> outline -> reporter 执行、每步产物 ID、报告质量检查结果、失败原因；可以直接用于后续数据集评测 |

这四点的优先级建议是：

```text
P0: trace 整理保存 + 引用升级
P1: 人工大纲确认
P1: 字数/等待时间 profile
P2: 完整 AGM action loop
```

原因是 trace 和引用是后续评测的基础设施；没有它们，即使主观感觉报告变好，也很难跑数据集证明效果。人工大纲确认和字数/等待时间 profile 是产品体验层的关键控制点，但可以在核心 artifact 链路稳定后接入。完整 AGM 不放进 V1，V1 只保存 lightweight AGM State，避免把高成本循环提前塞进主路径。

## 7. 分阶段迁移

### Phase 1：SA 核心结构迁移

目标：

```text
先实现 SA 最关键的 Research Summary + ScopeTree + Evidence Map + lightweight AGM State + unified reporter。
```

改动：

```text
新增：
skills/public/scaffold-preresearch/SKILL.md
skills/public/scaffold-outline/SKILL.md
skills/public/scaffold-reporting/SKILL.md
skills/public/scaffold-quality-gate/SKILL.md

不新增：
skills/public/scaffold-deepresearch-orchestration/SKILL.md

原因：
CentralAgent V1 只需要窄 SOP 做流程编排；普通 skill body 只会稳定注入给子智能体。

修改：
backend/packages/harness/deerflow/subagents/builtins/sp_specialists.py
backend/packages/harness/deerflow/sp/central/prompt.py
```

同时把下面两项作为 Phase 1 的硬性验收：

- 引用链路升级：reporter 只基于 materialized evidence 写事实，最终报告 metadata 保留 source refs 和 evidence mapping。
- trace 可评测：每次 scaffolded report run 能导出 delegation 顺序、artifact refs、skill_names、质量检查和失败原因。

人工大纲确认和字数/等待时间 profile 可以先用最小实现接入，但不能阻塞无交互的批量评测路径。

### Phase 2：更低影响的动态 skill routing

如果担心静态给 researcher / outline 加 SA skill 会影响其他任务，可以后续实现或确认：

```text
sp_delegate.metadata.skill_names
```

让 CentralAgent 在报告场景动态传：

```python
metadata={"skill_names": ["scaffold-preresearch"]}
metadata={"skill_names": ["scaffold-outline"]}
metadata={"skill_names": ["scaffold-reporting", "scaffold-quality-gate"]}
```

但前提是 runtime 真的会根据 `metadata.skill_names` 控制 subagent 加载的 skill。没有 enforce 前，不要依赖它。

### Phase 3：section-by-section reporter

迁移 SA 的 reporter pipeline：

```text
ScopeTree leaf -> section draft
section drafts -> merged report
quality gate -> revision
```

这一步可以参考：

- `reporter.py::_build_section_prompt()`
- `reporter.py::_build_insight_prompt()`
- `reporter.py::_build_two_stage_section_prompt()`

### Phase 4：AGM / utility loop

最后再考虑：

- expansion
- revision
- contraction
- utility scorer
- generation probe
- evidence refresh

这些对效果可能有帮助，但不是把 SA 核心迁入 SP2 的第一优先级。

注意：Phase 4 才是完整 AGM action loop。Phase 1 只要求 outline artifact 中保存 lightweight AGM State，用于 trace、focused research 和后续 revision，不要求实现 expansion/revision/contraction 的闭环优化。

## 8. 首轮 case 暴露问题与 V1.1 优化计划

首轮测试 case：

```text
请写一份关于 2025-2026 年中国具身智能机器人产业链的深度研究报告...
```

实际结果说明 skill routing 已经生效：

```text
researcher -> loaded scaffold-preresearch
outline    -> loaded scaffold-outline
reporter   -> loaded stackplanner-reporting + scaffold-reporting + scaffold-quality-gate
```

但还没有证明 artifact gating 生效。主要问题：

1. `outline` 被调用了，但没有看到持久化的 `outline` artifact。
2. 最终报告主要依赖 `Research Summary v1/v2`，`ScopeTree / Evidence Map / AGM State` 没有稳定传到最终 reporter。
3. reporter 被调用多次，最后一次只加载了 `stackplanner-reporting`，说明补救 / finalization 路径没有稳定继承 SA skills。
4. `scaffold-quality-gate` 放进首轮 reporter 会增加复杂度，可能诱发 partial/retry。
5. 当前 Research Summary 太像“带很多引用的长证据汇总”，和 SA 里更紧凑的 planning summary 还有差距。
6. 引用、数字、口径冲突没有单独形成机器可检查的 evidence ledger / numeric claim map。

### 8.1 V1.1 流程调整

V1.1 推荐先收敛为更硬、更短的主链路：

```text
1. researcher
   输出：
   - Research Summary artifact
   - Evidence Ledger / Numeric Claim Map
   - evidence_gaps

2. outline
   输入：
   - user query
   - Research Summary
   - Evidence Ledger / Numeric Claim Map
   输出：
   - outline artifact
   - ScopeTree
   - Evidence Map
   - lightweight AGM State

3. reporter first draft
   输入：
   - user query
   - Research Summary
   - ScopeTree
   - Evidence Map
   - materialized evidence bodies
   - Numeric Claim Map
   使用：
   - stackplanner-reporting
   - scaffold-reporting
   输出：
   - final Markdown report

4. quality gate
   V1.1 暂不放进首轮主路径。
   后续只在 verifier / revision / offline eval 中使用 scaffold-quality-gate。
```

### 8.2 `stackplanner-reporting` 和 `scaffold-reporting` 的边界

`scaffold-reporting` 应该能写报告，但它目前只承载 SA 方法约束；`stackplanner-reporting` 仍承担 SP2 的交付协议：

```text
stackplanner-reporting:
  - artifact_content / created_paths
  - write_file 规则
  - report_revision artifact 类型
  - revision 版本语义
  - source_artifact_ids / quality_checks / evidence_gaps metadata

scaffold-reporting:
  - 按 ScopeTree 写
  - 不把 Research Summary 当事实来源
  - 稳定候选集合
  - 横向比较
  - 缺证不删除对象
  - 结论直接回答问题
```

所以 V1.1 首轮 reporter 先保留两者：

```python
metadata={"skill_names": ["stackplanner-reporting", "scaffold-reporting"]}
```

但这确实有设计味道不清的问题。后续可以二选一：

```text
方案 A：保留 stackplanner-reporting 作为 SP2 delivery contract，scaffold-reporting 只管 SA 方法。
方案 B：把 stackplanner-reporting 的交付协议合并进 scaffold-reporting，然后首轮 reporter 只加载 scaffold-reporting。
```

推荐 V1.1 先用方案 A，等链路稳定后再做方案 B。否则现在直接去掉 `stackplanner-reporting`，风险是报告可能写出来，但 artifact / 文件 / revision contract 不稳定。

### 8.3 `scaffold-quality-gate` 暂缓

V1.1 首轮 reporter 不加载 `scaffold-quality-gate`。

原因：

- 当前最大问题是 artifact 链路不稳定，不是质量检查不足。
- quality gate 会增加 prompt 长度和约束数量，容易让 reporter 返回 partial 或触发多次 retry。
- 没有稳定 `ScopeTree / Evidence Map` artifact 时，quality gate 很难可靠判断 coverage。

暂时定位：

```text
scaffold-quality-gate:
  - 不进入 first draft
  - 用于 report 生成后的 revision / verifier / offline eval
  - 等 outline artifact gating 稳定后再接入主路径
```

### 8.4 Research Summary 应该更像 SA

当前 researcher 输出的问题：

```text
Research Summary 太长
引用直接塞进正文
证据、数字、冲突口径和规划判断混在一起
reporter 很容易把 Summary 当事实来源
```

V1.1 应把 researcher 输出拆成两个层次：

```text
Research Summary:
  给 outline/reporter 的紧凑任务理解。
  只保留对象范围、维度、边界、稳定候选集合、关键判断方向、证据缺口。

Evidence Ledger / Numeric Claim Map:
  给 reporter 的事实边界。
  记录每条证据、每个数字、每个口径冲突来自哪里。
```

推荐 researcher artifact 结构：

```json
{
  "artifact_type": "research_observation",
  "artifact_content": {
    "research_summary": "compact markdown summary",
    "evidence_ledger": [
      {
        "evidence_id": "E1",
        "source_title": "",
        "url": "",
        "publisher": "",
        "published_at": "",
        "retrieved_at": "",
        "claim_summary": "",
        "reliability": "high|medium|low",
        "used_for_nodes": []
      }
    ],
    "numeric_claim_map": [
      {
        "claim_id": "N1",
        "value": "",
        "unit": "",
        "metric": "",
        "entity": "",
        "time_scope": "",
        "source_evidence_id": "E1",
        "quote_or_context": "",
        "confidence": "high|medium|low",
        "conflicts_with": []
      }
    ]
  },
  "artifact_metadata": {
    "completion_status": "complete|partial|blocked",
    "stable_candidate_set": [],
    "explicit_dimensions": [],
    "evidence_gaps": [],
    "source_refs": ["E1", "E2"],
    "numeric_claim_ids": ["N1", "N2"]
  }
}
```

如果 SP2 artifact adapter 不适合 `artifact_content` 放 JSON object，可以退一步：`artifact_content` 用 Markdown，`artifact_metadata` 放 `evidence_ledger` 和 `numeric_claim_map` 的结构化 JSON。

### 8.5 引用格式约束

V1.1 统一使用证据 ID，不让正文里到处混杂裸 URL：

```text
Research Summary:
  使用 [E1], [E2], [N1] 这类内部证据 ID。

Evidence Ledger:
  保存 E1/E2 对应的 title/url/publisher/date/reliability。

Numeric Claim Map:
  保存 N1/N2 对应的数字、单位、口径、实体、时间范围和来源 evidence_id。

Final Report:
  可以用可读引用格式，但每个关键数字必须能回溯到 Numeric Claim Map。
```

报告写作约束：

- 不引用 `Research Summary` 作为事实来源。
- 不引用 `ScopeTree` 或 logic skeleton。
- 所有数字、排名、订单金额、出货量、融资金额必须来自 `numeric_claim_map`。
- 如果同一指标多口径冲突，报告必须并列说明，不能选一个方便口径。
- 来源列表按 Evidence Ledger 输出，不在正文里反复塞长 URL。

### 8.6 outline artifact gating

V1.1 必须把 outline artifact 作为 reporter 前置条件。

CentralAgent prompt 要明确：

```text
Do not delegate reporter until a current outline artifact exists.
The outline artifact must contain ScopeTree in artifact_content and
evidence_map + agm_state in artifact_metadata.
If outline returns partial/blocked or no outline artifact is persisted,
retry outline once with a narrower expected_output before reporting.
```

runtime / handler 层后续可加硬保护：

```text
如果 target_agent="reporter" 且是 substantial report 场景：
  - input_refs 必须包含 research_observation
  - input_refs 必须包含 outline
  - outline artifact metadata 必须包含 evidence_map / agm_state
否则 block reporter delegation，并让 CentralAgent 先补 outline。
```

V1.1 可以先只改 CentralAgent prompt 和 skills；如果仍出现 reporter 绕过 outline，再改 delegate handler 做硬拦截。

### 8.7 reporter 多次调用的收敛策略

目标是首版主路径只调用一次 reporter：

```text
researcher -> outline -> reporter -> finish
```

允许额外 reporter 调用的情况只保留三种：

1. 用户明确要求 revision。
2. reporter 返回 `completion_status=partial|blocked` 且已有 report artifact 可以修。
3. offline/verifier 明确指出具体失败项。

不允许：

```text
因为 outline artifact 缺失而直接反复 reporter。
因为 quality gate 过重而反复 reporter。
最后一次 reporter 丢失 scaffold-reporting。
```

### 8.8 V1.1 具体改动清单

优先级从高到低：

```text
P0:
1. 修改 CentralAgent prompt：
   - reporter 前必须有 outline artifact
   - first draft reporter skill_names = ["stackplanner-reporting", "scaffold-reporting"]
   - scaffold-quality-gate 不进入 first draft

2. 修改 scaffold-outline：
   - artifact_content 必须是 ScopeTree JSON
   - artifact_metadata 必须包含 evidence_map / agm_state
   - 无法生成则 partial/blocked，不允许伪 complete

3. 修改 scaffold-preresearch：
   - Research Summary 压缩为 planning summary
   - 增加 Evidence Ledger / Numeric Claim Map
   - 引用统一用 E*/N* ID

P1:
4. 修改 scaffold-reporting：
   - 明确只用 Evidence Ledger / Numeric Claim Map 写事实和数字
   - 最终来源列表从 Evidence Ledger 生成

5. 加一个轻量测试用例：
   - 输入深度报告 query
   - 断言 delegation 顺序包含 researcher -> outline -> reporter
   - 断言 artifacts 包含 research_observation / outline / report_revision
   - 断言 outline metadata 包含 evidence_map / agm_state
   - 断言 first draft reporter 没有加载 scaffold-quality-gate

P2:
6. 如果 prompt 约束仍不稳定，再在 delegate handler 加 reporter 前置 artifact guard。
```

## 9. 最终推荐

这份迁移不应该理解成“只给 reporter 加一个 skill”。

更准确的 V1 是：

```text
SA 预检索能力 -> scaffold-preresearch skill -> researcher
SA ScopeTree 能力 -> scaffold-outline skill -> outline
SA 报告能力 -> scaffold-reporting skill -> reporter
SA 质量门 -> scaffold-quality-gate skill -> reporter/verifier
SA 调用顺序 -> CentralAgent SA report SOP
SA 中间状态 -> Research Summary / ScopeTree / Evidence Map artifacts
```

一句话：

```text
V1 要先迁移 SA 的“报告前结构控制能力”，而不是只修最后的报告 prompt。
```
