# ScaffoldAgent 方法抽象与 Prompt 精华

> 日期：2026-08-06
> 位置：`StackPlanner2/docs/deepresearch/SA/`
> 关联迁移说明：`StackPlanner2/docs/SA_TO_SP2_SKILL_MIGRATION.md`
> 目的：概括 ScaffoldAgent Lite 中真正值得迁移到 SP2 DeepResearch Skill 的方法精华，尤其是 outline 格式、预检索 Summary 和关键 prompt。

## 1. SA 是什么

ScaffoldAgent 的核心不是“多智能体分工”，也不是单纯的 RAG 报告生成，而是一套用 `ScopeTree` 控制深度研究任务的结构化方法。

它把开放式研究任务拆成三层状态：

- `ScopeTree`：报告大纲，也是检索路由、证据绑定和写作约束。
- `Evidence Map`：每个节点绑定哪些证据，避免全局资料被随意复用。
- `Research Summary`：预检索后形成的全局任务理解，用来稳定候选对象、比较维度、证据边界和最终回答方向。

SA 的提分关键在于：先让模型“知道这份报告应该长什么样”，再让它沿着这个结构去检索、组织和写作。它不是让 reporter 最后凭一个长 prompt 自由发挥。

## 2. 第一版迁移取舍

面向 SP2 的第一版迁移，应先保留 SA 中最稳定、最直接提升报告质量的部分。

### 2.1 首期必须保留

| 模块 | 是否迁移 | 原因 |
| --- | --- | --- |
| 预检索 | 是 | 构建大纲前先形成任务骨架和证据初稿，是提升 comprehensiveness / instruction following 的关键 |
| Research Summary | 是 | 作为初始化 outline 和最终报告生成的全局上下文 |
| ScopeTree / outline 格式 | 是 | 后续所有报告生成都应复用这套结构 |
| 节点 instruction | 是 | 每个章节必须知道自己要回答什么，而不是只有标题 |
| node-evidence 绑定 | 是 | 保证写作有证据边界，减少无证据扩写 |
| 关键 prompt 模板 | 是 | SA 的实际效果主要来自这些任务约束 prompt |
| 统一 reporter | 是 | 第一版先整篇统一生成或统一写作，不拆多阶段 section writer |

### 2.2 首期先不迁移

| 模块 | 暂缓原因 |
| --- | --- |
| 完整 AGM / 多轮 action loop | 第一版先不引入复杂局部编辑循环，避免 SP2 迁移面过大；但 outline 阶段应输出轻量 AGM State |
| Utility / reward 评分 | 当前迁移重点是结构和 prompt，评分可后续作为 evaluator 增强 |
| embedding relevance / novelty | 属于 P2 工具化，不阻塞第一版 skill |
| citation verifier / NLI | 属于可信度增强，不作为第一版必需能力 |
| two-stage reporter | 第一版先统一写报告，避免报告链路过重 |
| 分章节 DFS writer | 第一版先不做“每个叶子单独写 section”的 pipeline |

这意味着首期 SP2 Skill 不复刻完整 SA pipeline，而迁移 SA 的“报告前结构控制能力”。这里暂缓的是完整 AGM 多轮 action loop，不是把 AGM 概念完全拿掉；V1 应在 outline artifact 中保留轻量 AGM State。

## 3. 第一版目标流程

建议 SP2 DeepResearch 第一版使用下面的流程：

```text
User Query
  -> Pre-research once
  -> Build Research Summary
  -> Build ScopeTree outline + Evidence Map + lightweight AGM State from Query + Summary + Evidence
  -> Optional one refresh pass before writing
  -> Rebuild / update Research Summary from final evidence
  -> Unified Reporter writes final report using Query + ScopeTree + Summary + Evidence
```

关键点：

- 预检索只做一次，目标是让 outline 初始化时有足够任务上下文。
- 写作前可以再做一次文档整理，把已有证据构建成最终 `Research Summary`。
- 第一版 reporter 不拆成每个 leaf 单独写，不做 two-stage insight + section draft。
- 但 reporter 必须吃到完整 `ScopeTree`、全局 `Research Summary` 和证据列表。

## 4. ScopeTree 格式必须保留

SA 的 outline 格式是最值得复用的部分。建议 SP2 后续所有 deep research/report skill 都用这个格式。

### 4.1 JSON 结构

```json
{
  "title": "root title",
  "instruction": "one sentence describing what the whole report should accomplish",
  "children": [
    {
      "title": "section title",
      "instruction": "one sentence describing what this section should write and how it serves the original query",
      "doc_ids": ["doc_id_1", "doc_id_2"],
      "children": []
    }
  ]
}
```

### 4.2 Markdown 展示

```markdown
- Root title
  instruction: root report goal
  - Section A
    instruction: what Section A must answer
  - Section B
    instruction: what Section B must answer
```

### 4.3 格式约束

- 每个节点必须有 `title`。
- 每个节点必须有 `instruction`。
- 叶子节点必须能直接生成一段报告正文。
- 多对象、多维度任务必须保持稳定候选集合。
- 不要把“资料不足、验证计划、研究方法、证据缺口”作为主章节。
- 证据不足只能写进 instruction 的保守表达要求，不能变成报告主结构。

## 5. Research Summary 是第一版提分核心

SA 测试里，`id=10` 这类复杂评估框架任务的提升，很大程度来自构建大纲前的预检索 Summary。

Summary 的作用不是普通摘要，而是为后续大纲和报告提供全局任务对齐：

- 用户最终要什么交付物。
- 明确对象范围。
- 明确比较维度。
- 明确时间、地域、行业或技术边界。
- 形成稳定候选集合。
- 提醒哪些判断需要保守表达。
- 给 reporter 一个全局方向，但不作为事实引用来源。

### 5.1 Summary 应该输入给两个阶段

第一，初始化 ScopeTree：

```text
User Query + Seed Evidence + Research Summary -> Initial ScopeTree
```

这里的初始化不是单纯生成静态大纲。`scaffold-outline` 应同时维护轻量 AGM State：当前 active nodes、node-evidence 绑定、证据稀疏或冲突节点、pending queries、expansion targets 和 utility signals。

第二，最终报告生成：

```text
User Query + Final ScopeTree + Final Research Summary + Evidence -> Report
```

### 5.2 Summary 不应该做什么

- 不输出 checklist。
- 不输出研究计划。
- 不把 evidence gaps 变成主线。
- 不生成未在资料中出现的引用编号。
- 不为了表格完整而编造字段。

## 6. SA 提分关键在哪里

结合已有 DRB / RACE 分析，SA 真正有效的点主要是以下几个。

### 6.1 预检索 query 规划

普通搜索容易只找到泛文章。SA 的预检索 prompt 会要求 query 覆盖：

- 对象类型
- 时间范围
- 地域范围
- 比较维度
- 指标词
- 权威来源倾向词

这对复杂报告非常关键。没有这一层，outline 会从一开始就失焦。

### 6.2 Research Summary 约束大纲

大纲生成不是只看用户 query，而是同时看预检索资料和 Summary。这样能让大纲继承已经发现的候选对象、关键维度和证据边界。

### 6.3 节点 instruction

SA 的章节不是简单标题，而是 `title + instruction`。这让 reporter 明确知道：

- 这一节负责回答什么。
- 怎样服务原始问题。
- 是否需要横向比较。
- 是否需要给结论、排序、建议或量化框架。

这是 SA 比普通 outline prompting 更强的地方。

### 6.4 Prompt 里反复压制的错误模式

SA prompt 的精华不是“写得详细”，而是持续压制几个常见坏模式：

- 把证据缺口写成章节。
- 每节选择不同候选对象。
- 因某个对象证据弱就删除对象。
- 排名任务用代表性案例冒充 TopN。
- 比较任务写成逐对象介绍。
- 推荐/结论章节不直接给判断。
- 引用 Research Summary 或逻辑骨架当作事实来源。

这些约束应该迁移进 SP2 Skill。

## 7. 关键 Prompt 在哪些文件

下面列的是 SA 方法精华所在位置。迁移时优先读这些 prompt，而不是优先读 Utility。

| 能力 | 文件 | 入口 | 迁移价值 |
| --- | --- | --- | --- |
| 预检索 query 生成 | `scaffold_agent_lite/src/scaffold_agent/pre_research.py` | `generate_queries()` | 负责把用户问题拆成高质量搜索 query |
| 迭代式预检索规划 | `scaffold_agent_lite/src/scaffold_agent/pre_research.py` | `plan_next_query()` | 根据已有资料补证据缺口，首版可不启用迭代，但 prompt 思路值得保留 |
| Research Summary 生成 | `scaffold_agent_lite/src/scaffold_agent/pre_research.py` | `generate_brief()` | 构建初始化大纲和报告全局上下文 |
| 初始 ScopeTree 生成 | `scaffold_agent_lite/src/scaffold_agent/pipeline.py` | `_generate_initial_outline()` | 最重要的 outline prompt，定义节点 instruction、候选集合、比较/排名/建议结构 |
| Expansion prompt | `scaffold_agent_lite/src/scaffold_agent/actions.py` | `expand()` | 首版可不迁 action loop，但其中“局部扩展不改任务契约”的约束值得沉淀 |
| Revision prompt | `scaffold_agent_lite/src/scaffold_agent/actions.py` | `revise()` | 首版可不迁，但 coverage-preserving refinement 是后续局部更新关键 |
| Contraction prompt | `scaffold_agent_lite/src/scaffold_agent/actions.py` | `contract()` | 首版可不迁，但去重且不压缩主比较骨架的约束应保留 |
| 报告前证据刷新 | `scaffold_agent_lite/src/scaffold_agent/pipeline.py` | `_refresh_report_evidence()` / `_build_report_refresh_queries()` | 第一版可以只做一次轻量 refresh 或最终 evidence summary |
| 统一章节写作 prompt | `scaffold_agent_lite/src/scaffold_agent/reporter.py` | `_build_section_prompt()` | 虽然首版不分章节写，但这里的 reporter 约束是最终报告 prompt 的核心来源 |
| 两阶段 reporter | `scaffold_agent_lite/src/scaffold_agent/reporter.py` | `_build_insight_prompt()` / `_build_two_stage_section_prompt()` | 首版先不做，后续提升 insight 时再迁 |

## 8. 第一版 SP2 Skill 应迁移哪些 prompt 精华

### 8.1 Pre-research prompt 精华

需要保留的规则：

- 先拆解用户问题，再生成搜索 query。
- query 必须覆盖对象、时间、地域、指标和输出要求。
- 比较型任务按维度拆 query。
- 排名型任务必须加入排名字段、收益口径、主体类型和权威来源倾向词。
- 不要在用户没有给定时硬编码具体机构。
- 中英文关键词可以混合。

### 8.2 Research Summary prompt 精华

需要保留的规则：

- 只用资料可见信息。
- 摘要围绕最终回答目标，而不是围绕检索过程。
- 保留用户明确点名的对象范围、比较维度、时间/地域、输出要求。
- 可以用高密度表格承载对象和维度。
- 证据不足只用 1-2 句话说明边界。
- Summary 是全局上下文，不是最终引用来源。

### 8.3 Initial ScopeTree prompt 精华

需要保留的规则：

- 3-5 个一级章节。
- 总 leaf 不超过 10 个。
- 用户显式维度必须出现在章节、子章节或 instruction 中。
- 多对象任务必须定义稳定候选集合。
- 比较/评估/推荐/排名/预测/方案选择任务必须有：
  - 候选对象范围
  - 逐维度比较
  - 综合判断/建议/结论
- 每个节点必须有 instruction。
- 不把资料不足、验证计划、方法说明写成主章节。

### 8.4 Reporter prompt 精华

第一版统一 reporter 可以吸收 `_build_section_prompt()` 的规则，但改成整篇报告维度：

- 必须遵守 ScopeTree，每个一级章节都要覆盖。
- ScopeTree instruction 优先于局部证据强弱。
- 全局 Research Summary 只用于任务对齐，不作为引用来源。
- 如果 Summary 或 ScopeTree 给出稳定候选集合，整篇报告必须沿用。
- 比较/评估/预测/推荐/排名任务必须形成横向可比结构。
- 不因证据弱删除用户明确要求的对象。
- 综合结论必须直接给排序、推荐、判断或可执行结论。
- 缺失处理只在相关章节末尾简短说明，不写成缺口清单。
- 数字、排名、年份、机构、财务数据必须来自证据。

## 9. 第一版统一 Reporter 建议

第一版不做 SA 当前的 leaf DFS section writer，而采用统一报告生成：

```text
输入：
- User Query
- Final ScopeTree
- Final Research Summary
- Evidence List with doc ids

输出：
- Markdown report
- Reference list
- Evidence mapping
```

统一 reporter prompt 应要求：

- 按 ScopeTree 的章节顺序写。
- 不新增主章节。
- 每个章节显式完成对应 instruction。
- 需要表格时优先用表格。
- 结尾给出直接回答用户问题的结论。
- 附上证据映射，说明每个主要章节使用了哪些 doc。

这样可以保留 SA 的结构控制精华，同时避免第一版报告生成链路过重。

## 10. 后续再迁移什么

第一版跑通后，再考虑把 SA 的其余能力逐步迁移：

### 10.1 局部编辑

把 Expansion / Revision / Contraction 作为 outline 修订工具接入 SP2，并复用 V1 outline artifact 中的轻量 AGM State。

优先级：

1. Revision：修正节点 instruction 和标题。
2. Expansion：只扩展叶子节点。
3. Contraction：合并重复兄弟节点。

### 10.2 Utility / reward

等结构和 prompt 稳定后，再引入打分：

- evidence relevance
- novelty
- coverage
- citation support
- redundancy
- task completion

第一版不建议迁，因为它会分散重点。

### 10.3 分章节 reporter

当统一 reporter 遇到超长报告 token 压力，再迁移 SA 的 leaf DFS writer 和 two-stage reporter。

迁移顺序：

1. 单章节 `_build_section_prompt()`。
2. `_build_insight_prompt()`。
3. `_build_two_stage_section_prompt()`。
4. citation verifier。

## 11. 给 SP2 Skill 的一句话抽象

SA 第一版迁移可以概括为：

```text
先预检索并生成 Research Summary，再用 Summary 和证据生成带 instruction 的 ScopeTree、Evidence Map 和轻量 AGM State，最后让统一 Reporter 严格沿 ScopeTree 和 Summary 写报告；首版不迁完整 Utility 评分和多轮 AGM action loop，但保留这些 prompt 中对任务完整性、稳定候选集合、证据边界和横向比较的约束。
```
