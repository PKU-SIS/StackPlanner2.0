# SA 报告生成 V1.2 优化计划：分章节生成与上下文收敛

> 背景：V1.1 已让 SA 的 pre-research、outline、reporter skill routing 基本跑通，并加入 outline artifact guard。但首轮长报告实验显示，完整报告一次性 synthesis 仍然不稳定，reporter 多次返回空响应。
> 目标：把报告生成从“一次性长报告”改成“ScopeTree leaf/section 分块生成 + merge”，同时收紧 outline JSON contract、证据切片和 retry 策略。

## 1. 这次实验暴露的问题

实验请求：

```text
请写一份关于 2025-2026 年中国具身智能机器人产业链的深度研究报告...
```

实际链路：

```text
researcher -> outline v1 -> reporter blocked -> outline v2 -> reporter 多次空响应 -> CentralAgent 直接对话输出
```

结论：

```text
pre-research 和 ScopeTree 已经基本可用；
真正不稳定的是长报告一次性 reporter synthesis。
```

具体问题：

1. `web_search` 有结果，但 `web_fetch` 6/6 infrastructure failure。
2. `outline v1` 内容中有 `evidence_map / agm_state`，但没有作为 artifact ref metadata 顶层字段持久化。
3. `outline v1` 是“自然语言 + fenced JSON”，不是纯 JSON contract。
4. reporter 输入太大：Research Summary + Evidence Ledger + Numeric Claim Map + ScopeTree + 长任务说明一起进入上下文。
5. reporter 多次返回：

```text
[PARTIAL result: partial] No response generated
```

6. retry 策略不收敛：重复完整报告任务、切换“写文件/不写文件”、最后让 CentralAgent 直接输出。
7. 最终报告能输出，是因为 CentralAgent 绕过 reporter artifact contract 后直接写正文；这不是理想主路径。

## 2. V1.2 核心原则

V1.2 不再追求一次 reporter call 生成完整长报告。

新的主路径：

```text
researcher
  -> outline
  -> section reporter per major section / leaf group
  -> merge reporter
  -> finish
```

关键原则：

- outline 必须是机器可解析的纯 JSON。
- reporter 每次只接收当前 section 需要的 evidence slice。
- 长报告用 `write_file`，不要在 retry 中要求“不要写文件”。
- full-report reporter 最多尝试一次；失败后立即降级 section-by-section。
- `scaffold-quality-gate` 暂不进 first draft，只用于 merge 后 verifier / offline eval。

## 3. V1.2 目标流程

### 3.1 pre-research

保持 V1.1：

```text
Research Summary
Evidence Ledger [1]-[n]
Numeric Claim Map
evidence_gaps
seed evidence
```

新增要求：

- Research Summary 控制在可配置长度内，例如 1500-2500 中文字。
- Evidence Ledger 不直接全量塞给每个 section reporter。
- Numeric Claim Map 支持按 section/leaf 过滤。

### 3.2 outline

必须输出纯 JSON object，不允许：

```text
自然语言前缀
Markdown fence
内嵌 JSON 字符串
未转义引号
```

输出结构建议：

```json
{
  "summary": "compact note",
  "artifact_content": {
    "scope_tree": {
      "report_title": "",
      "sections": []
    }
  },
  "artifact_type": "outline",
  "artifact_metadata": {
    "completion_status": "complete|partial|blocked",
    "format": "scope_tree_json",
    "evidence_map": {
      "leaf_id": ["[1]", "[2]"]
    },
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

如果模型仍返回 fenced JSON，runtime 可以容错解析，但 skill contract 必须明确禁止。

### 3.3 section planning

CentralAgent 或 handler 从 ScopeTree 派生 section tasks。

V1.2 建议按一级章节分块，而不是每个 leaf 都单独生成：

```text
section_group_1: s1 + leaves
section_group_2: s2 + leaves
section_group_3: s3 + leaves
section_group_4: s4 + leaves
section_group_5: s5 + leaves
```

原因：

- 每个 leaf 单独写会增加调用次数和 merge 难度。
- 一级章节分块能明显降低上下文，又保持章节内部连贯。

### 3.4 evidence slicing

每个 section reporter 只接收：

```text
user request compact
Research Summary compact
current section subtree
section evidence_map
Evidence Ledger items referenced by this section
Numeric Claim Map items referenced by this section
global citation style
global writing constraints
```

不要接收：

```text
完整 Evidence Ledger [1]-[29]
完整 ScopeTree 全文
所有 materialized evidence bodies
重复的超长任务说明
```

### 3.5 section reporter

每个 section reporter 输出：

```json
{
  "summary": "section draft complete",
  "artifact_content": "Markdown section draft",
  "artifact_type": "section_draft",
  "artifact_metadata": {
    "completion_status": "complete|partial|blocked",
    "section_id": "s1",
    "covered_leaf_ids": ["s1n1", "s1n2"],
    "source_ids": [1, 5, 8, 9, 22],
    "numeric_claim_ids": ["C1", "C2"],
    "evidence_gaps": []
  }
}
```

如果当前 artifact system 暂不支持 `section_draft` 类型，可以先用：

```text
artifact_type="report_revision"
artifact_metadata.kind="section_draft"
```

但推荐新增或允许 `section_draft`，方便后续评测。

### 3.6 merge reporter

merge reporter 输入：

```text
user request compact
ScopeTree headings
all section_draft artifacts
global source list
global evidence_gaps
global citation style
```

merge reporter 不重新做事实写作，只做：

- 合并章节。
- 去重标题和重复段落。
- 统一语气。
- 统一 `[n]` 引用格式。
- 生成完整参考来源列表。
- 检查 ScopeTree section coverage。
- 写入 final report artifact。

merge reporter 输出：

```json
{
  "summary": "final report merged",
  "artifact_content": null,
  "artifact_type": "report_revision",
  "artifact_metadata": {
    "completion_status": "complete|partial|blocked",
    "created_paths": ["/mnt/user-data/outputs/report-...md"],
    "section_draft_ids": [],
    "scope_tree_id": "",
    "research_summary_id": "",
    "source_artifact_ids": [],
    "citation_style": "[n]",
    "quality_checks": [],
    "evidence_gaps": []
  }
}
```

## 4. retry 策略

V1.2 要避免无目标重试。

推荐策略：

```text
full report reporter:
  max_attempts = 1
  if empty/partial without artifact -> switch to section pipeline

section reporter:
  max_attempts = 1 per section
  if failed -> split section by leaf once

leaf reporter:
  max_attempts = 1
  if failed -> record blocked section with evidence_gaps

merge reporter:
  max_attempts = 1
  if failed -> CentralAgent can present section drafts with limitation
```

不允许：

```text
重复 3-6 次完整报告 reporter
在 retry 中切换 write_file / no write_file 指令
丢失 scaffold-reporting
最后一次只加载 stackplanner-reporting
```

## 5. reporter 交付方式

V1.2 统一长报告交付方式：

```text
long report -> reporter uses write_file -> report_revision artifact
```

不要在 reporter task 中写：

```text
不要写文件
不要调用任何工具
你的响应会自动持久化
```

原因：

- 这和 `stackplanner-reporting` 的 delivery contract 冲突。
- 长报告直接响应容易超上下文或被截断。
- 文件 artifact 更适合 trace、评测和下载。

如果要直接对话输出，应该由 CentralAgent 明确走 fallback，不再委托 reporter。

## 6. 具体代码改动位置

### 6.1 outline JSON contract

文件：

```text
skills/public/scaffold-outline/SKILL.md
backend/packages/harness/deerflow/sp/subagents/dr2_adapter.py
backend/packages/harness/deerflow/sp/actions/handlers/delegate.py
```

要做：

- skill 中禁止 prose + fenced JSON。
- adapter 中如果模型返回 fenced JSON，尽量解析外层 JSON object。
- delegate handler 写 outline artifact 时，如果 result.artifact_metadata 有 `evidence_map/agm_state`，确保进入 artifact ref metadata。
- 对 outline artifact 增加 acceptance check：`artifact_content` 不能是纯自然语言说明。

### 6.2 section artifact 类型

文件：

```text
backend/packages/harness/deerflow/sp/actions/handlers/delegate.py
backend/packages/harness/deerflow/sp/artifacts/adapter.py
backend/packages/harness/deerflow/agents/thread_state.py
frontend/src/core/artifacts/preview.ts
```

要做：

- 允许 `section_draft` artifact type。
- section_draft 默认写入：

```text
/mnt/user-data/outputs/sp/section_draft/
```

- 前端预览按 Markdown 处理。

如果想先最小化：

```text
暂不新增 artifact type，用 report_revision + metadata.kind="section_draft"
```

### 6.3 section delegation

文件：

```text
backend/packages/harness/deerflow/sp/central/prompt.py
backend/packages/harness/deerflow/sp/actions/handlers/delegate.py
```

方案 A：prompt-only V1.2

- CentralAgent 在 full report reporter 空响应后，按 ScopeTree 一级章节发多个 reporter delegation。
- 每个 delegation 明确 `metadata.section_id` 和 `metadata.kind="section_draft"`。

方案 B：handler/runtime pipeline

- 增加一个 report builder handler，从 outline artifact 自动派生 section tasks。
- 更稳定，但改动更大。

推荐先做方案 A。

### 6.4 evidence slicing

文件：

```text
backend/packages/harness/deerflow/sp/actions/handlers/delegate.py
```

当前 reporter materialization 会给很多 artifact body。V1.2 需要新增 section-aware slice：

```text
if target_agent == "reporter" and metadata.kind == "section_draft":
  read outline.evidence_map
  get section_id / leaf_ids
  keep only referenced source ids
  trim Evidence Ledger / Numeric Claim Map to referenced ids
```

最小实现可以先不做结构化裁剪，只在 prompt 层要求 reporter 只使用当前 section refs。更稳的是 handler 层裁剪。

### 6.5 merge reporter

文件：

```text
backend/packages/harness/deerflow/sp/central/prompt.py
skills/public/scaffold-reporting/SKILL.md
skills/public/stackplanner-reporting/SKILL.md
```

要做：

- 增加 merge mode 指令：

```text
metadata.kind="report_merge"
```

- merge mode 不新增事实，只合并 section drafts。
- merge mode 必须 write_file，输出 final `report_revision`。

## 7. prompt / skill 改动

### 7.1 `scaffold-outline`

新增：

```text
Your final response must be one raw JSON object only.
Do not include prose before or after JSON.
Do not wrap JSON in Markdown fences.
Do not use unescaped quotes inside JSON strings.
If you cannot produce valid JSON, return completion_status="partial" with the gap.
```

### 7.2 `scaffold-reporting`

新增 section mode：

```text
If metadata.kind="section_draft":
  Write only the delegated section or leaf group.
  Use only the supplied section subtree and evidence slice.
  Preserve [n] citations exactly.
  Do not write intro/conclusion unless this section asks for it.
  Return artifact_type="section_draft" or metadata.kind="section_draft".
```

新增 merge mode：

```text
If metadata.kind="report_merge":
  Merge supplied section drafts into one final Markdown report.
  Do not invent new facts.
  Normalize headings, transitions, citation formatting, and source list.
  Write final report file and return report_revision.
```

### 7.3 CentralAgent prompt

新增：

```text
For substantial reports, try full-report reporter at most once.
If reporter returns empty response, no artifact, or completion_status partial because of output failure:
  switch to section-by-section reporting using the current ScopeTree.
Do not repeat the same full report delegation.
```

## 8. trace 与评测字段

V1.2 trace 必须能看出：

```text
report_mode = full_report | section_draft | report_merge
section_id
covered_leaf_ids
source_ids
numeric_claim_ids
input_token_estimate
output_artifact_id
completion_status
empty_response
fallback_reason
```

这些字段可以放在：

```text
SPSubagentTask.metadata
artifact_metadata
run_events payload
```

## 9. 验收标准

单个深度报告样本通过标准：

```text
1. researcher 产出 Research Summary + Evidence Ledger + Numeric Claim Map。
2. outline 产出纯 JSON outline artifact。
3. outline artifact ref metadata 或 artifact body 可被机器识别为含 evidence_map / agm_state。
4. full reporter 最多调用一次。
5. 如果 full reporter 失败，自动进入 section pipeline。
6. section drafts 至少覆盖所有一级章节或全部 leaf。
7. merge reporter 产出 final report_revision artifact。
8. final report 使用稳定 [n] 引用。
9. final report 中关键数字可回溯到 Numeric Claim Map。
10. trace 能显示每个 section 使用了哪些 source_ids 和 leaf_ids。
```

## 10. 推荐实施顺序

```text
Phase 1: 低风险 prompt 修复
  - 强化 scaffold-outline raw JSON contract
  - scaffold-reporting 增加 section_draft / report_merge mode
  - CentralAgent 限制 full reporter retry 次数

Phase 2: prompt-only section pipeline
  - CentralAgent 根据 ScopeTree 一级章节委托 section reporter
  - section draft 暂用 report_revision + metadata.kind="section_draft"
  - merge reporter 合并成 final report_revision

Phase 3: runtime 支持
  - 新增 section_draft artifact type
  - handler 做 evidence slicing
  - run_events 记录 section_id/source_ids/token estimate

Phase 4: eval
  - 跑 3-5 个深度报告样本
  - 比较 full-report vs section pipeline 的成功率、引用覆盖率、token、耗时
```

## 11. 当前实验的直接结论

这次样本不应该继续靠“再试一次 reporter”解决。

下一步应该实现：

```text
full report reporter failed once
  -> section-by-section reporter
  -> merge reporter
```

这是 SA 迁移在 SP2 里的自然下一步：让 ScopeTree 不只是写作约束，而是实际驱动报告生成的分块执行计划。
