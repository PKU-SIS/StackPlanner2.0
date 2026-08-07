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

修改：
backend/packages/harness/deerflow/subagents/builtins/sp_specialists.py
backend/packages/harness/deerflow/sp/central/prompt.py
```

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

## 8. 最终推荐

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
