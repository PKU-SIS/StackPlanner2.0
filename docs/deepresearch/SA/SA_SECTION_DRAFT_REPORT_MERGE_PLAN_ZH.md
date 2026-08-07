# SA Section Draft 合并为最终报告的修复计划

> 日期：2026-08-07
> 背景任务：国际综合实力前十保险公司横向比较报告
> 目标：修复 SA 式报告流水线在 section fallback 后未合并、直接 FINISH 最后一个章节草稿的问题。

## 1. 问题结论

本次任务的关键问题不是“章节没有生成”，而是：

```text
full report reporter failed
-> section fallback 生成 s1 / s2 / s3 / s4
-> 缺少 report_merge
-> CentralAgent 对最后一个 section draft 执行 FINISH
```

理想链路应该是：

```text
researcher
-> research_observation
-> outline
-> outline / ScopeTree
-> reporter full report
-> 如果 full report 失败，进入 section fallback
-> s1 / s2 / s3 / s4 section_draft
-> report_merge
-> final report_revision
-> FINISH
```

当前实际链路变成：

```text
researcher
-> outline
-> full report failed
-> s1 / s2 / s3 / s4 section_draft
-> FINISH(s4)
```

因此用户看到“报告完成”，但 artifact 实际只是第四章。

## 2. 修复原则

本修复不应让 reporter 重复整篇写作，也不应让 CentralAgent 自己拼接正文。

应坚持三个原则：

1. `section_draft` 是中间产物，SOP 不应把它当作最终报告交付。
2. `report_merge` 是 section fallback 的必经终态。
3. 只有合并后的 `report_revision` 才能作为最终报告 artifact。

## 3. 短期手工恢复流程

在不改代码的情况下，可以用一次显式 reporter merge 子任务恢复本次报告。

### 3.1 输入

`input_refs` 应包含：

```text
research_observation-v1
outline-v1
research_observation-v2
s1 section draft
s2 section draft
s3 section draft
s4 section draft
```

### 3.2 reporter merge 任务

给 reporter 的任务应明确：

```text
请将 s1、s2、s3、s4 四个 section_draft 合并为一份完整中文 Markdown 报告。

要求：
- metadata.kind="report_merge"
- artifact_type="report_revision"
- 不新增事实、数字、公司、来源或引用
- 保留稳定数字引用 [1]-[18]
- 统一标题层级、表格格式、语气、限制说明和证据来源列表
- 去除重复过渡句和重复说明
- 检查四个一级章节全部覆盖
- 输出完整 report_revision，而不是章节草稿
```

### 3.3 FINISH

只有 merge 任务生成的 artifact 才能进入：

```text
sp_finish(final_artifact_ref=<merged_report_ref>, required_artifact_type="report")
```

SOP 上不要把 s1 / s2 / s3 / s4 中任何一个章节草稿作为最终交付；如果模型误走到章节草稿，应优先用 reporter merge 恢复，而不是增加全局 FINISH 门禁。

## 4. 代码层修复方案

### 4.1 修复点一：SOP 识别 section draft 并要求 report_merge

目标：不改全局 FINISH 门禁，避免误伤其他报告/修订任务；只在 SA deep research 报告 SOP 中明确 section fallback 的终态必须是 `report_merge`。

建议规则：

```text
如果 full-report reporter 失败并进入 section fallback：
  section_draft 只作为中间上下文
  所有 section_draft 完成后，下一步必须是 reporter report_merge
  report_merge 生成完整 report_revision 后，才进入最终交付
```

SOP 文案建议：

```text
Section drafts are not the final SA deep research deliverable. After all delegated section drafts complete, delegate reporter once with metadata.kind="report_merge" and include all section draft refs.
```

推荐修改位置：

```text
StackPlanner2/backend/packages/harness/deerflow/sp/central/prompt.py
StackPlanner2/skills/public/scaffold-reporting/SKILL.md
```

验收标准：

```text
SA deep research SOP 明确要求 section fallback 后执行 report_merge；
scaffold-reporting skill 明确 report_merge 的输入、输出和 metadata。
```

### 4.2 修复点二：section fallback 完成后自动触发 report_merge

目标：小改 SOP，让所有章节草稿完成后，CentralAgent 优先发起 reporter merge。第一版不做 router / FINISH handler 的全局硬拦截。

触发条件：

```text
当前 run 存在 outline artifact
当前 run 存在多个 section_draft artifact
section_draft 覆盖了 ScopeTree 所有 first-level section 或 covered_leaf_ids
当前 run 尚不存在 metadata.kind="report_merge" 的完整 report_revision
```

动作：

```text
delegate reporter
stage="reporting"
metadata.skill_names=["stackplanner-reporting", "scaffold-reporting"]
metadata.kind="report_merge"
input_refs=[outline, research_observation, all_section_draft_refs]
```

推荐修改位置：

```text
StackPlanner2/backend/packages/harness/deerflow/sp/central/prompt.py
StackPlanner2/skills/public/scaffold-reporting/SKILL.md
```

优先级建议：

```text
V1 只改 SOP / skill；
如果后续仍不稳定，再评估是否在 reporter 相关 contract 层增加更强约束。
```

### 4.3 修复点三：区分 section draft contract 与 report merge contract

当前 scaffold reporter contract 只要求：

```text
artifact_type in {"report", "report_revision", "final_report"}
artifact_content 非空
```

这不足以区分章节草稿和最终报告。

建议增加两类 contract：

#### Section Draft Contract

适用于：

```text
metadata.kind="section_draft"
```

要求：

```text
artifact_content 非空
artifact_type 可以暂时为 report_revision
artifact_metadata.kind == "section_draft"
artifact_metadata.section_id 非空
artifact_metadata.covered_leaf_ids 非空
completion_status in {"complete", "partial"}
```

但它必须标记为：

```text
artifact_metadata.kind="section_draft"
```

#### Report Merge Contract

适用于：

```text
metadata.kind="report_merge"
```

要求：

```text
artifact_type == "report_revision"
artifact_content 非空或 created_paths 非空
artifact_metadata.kind == "report_merge"
artifact_metadata.merged_section_refs 非空
artifact_metadata.section_coverage 覆盖所有要求章节
completion_status == "complete"
```

推荐修改位置：

```text
StackPlanner2/backend/packages/harness/deerflow/sp/actions/handlers/delegate.py
```

当前相关函数：

```text
_scaffold_delegate_artifact_contract_gap(...)
```

### 4.4 修复点四：最终报告 artifact 元数据

report_merge 产物建议包含：

```json
{
  "kind": "report_merge",
  "completion_status": "complete",
  "merged_section_refs": [],
  "section_coverage": {
    "required_section_ids": [],
    "covered_section_ids": [],
    "missing_section_ids": []
  },
  "source_artifact_ids": [],
  "scope_tree_artifact_id": "",
  "research_summary_artifact_id": "",
  "citation_style": "[n]",
  "evidence_gaps": []
}
```

这能让 reporter / CentralAgent 明确知道：

```text
这是合并后的完整报告，不是单章草稿。
```

## 5. 是否需要新增 artifact_type

有两种实现路线。

### 路线 A：最小改动

继续允许 section draft 使用：

```text
artifact_type="report_revision"
metadata.kind="section_draft"
```

优点：

```text
改动小，不需要扩展 artifact type schema。
```

缺点：

```text
仍需要依赖 SA 报告 SOP 和 reporter contract 识别 metadata.kind。
不在 V1 修改全局 FINISH handler，避免影响其他任务。
```

### 路线 B：更干净的长期方案

新增：

```text
artifact_type="section_draft"
```

优点：

```text
语义清晰，天然不可 FINISH。
report_revision 只表示完整报告版本。
```

缺点：

```text
需要修改 artifact adapter、UI 展示、current artifact 选择、测试。
```

建议：

```text
V1 采用路线 A；
V2 再评估是否新增 artifact_type="section_draft"。
```

## 6. 测试计划

### 6.1 单元测试：scaffold reporter contract 区分 section draft / report merge

构造：

```text
case A:
  artifact_type="report_revision"
  metadata.kind="section_draft"
  metadata.section_id 非空

case B:
  artifact_type="report_revision"
  metadata.kind="report_merge"
  metadata.merged_section_refs 非空
  metadata.section_coverage.missing_section_ids=[]
```

执行：

```text
调用 scaffold reporter artifact contract 检查
```

期望：

```text
case A 被识别为章节草稿，可作为中间产物
case B 被识别为完整合并报告
contract 错误信息只影响 scaffold reporter delegate，不影响其他任务
```

### 6.2 集成测试：section fallback SOP 触发 merge

构造：

```text
full report reporter 返回 No response generated
outline 有 4 个 first-level sections
s1 / s2 / s3 / s4 均成功
```

期望：

```text
CentralAgent 下一步 delegate reporter metadata.kind="report_merge"
不会重复 full-report reporter
```

### 6.3 集成测试：merge 后 FINISH 成功

构造：

```text
report_merge artifact:
  artifact_type="report_revision"
  metadata.kind="report_merge"
  completion_status="complete"
  section_coverage.missing_section_ids=[]
```

执行：

```text
sp_finish(final_artifact_ref=<merged_ref>, required_artifact_type="report")
```

期望：

```text
FINISH 成功
最终回复引用 merged report artifact
```

### 6.4 回归测试：不要重复 full report

构造：

```text
full report reporter 已经失败一次
section fallback 已启动
```

期望：

```text
系统不得再次调用 full-report reporter
只能继续 section_draft 或 report_merge
```

## 7. 推荐实施顺序

### Phase 1：Prompt 与手工恢复

目标：立刻让后续任务知道必须 merge。

动作：

```text
强化 central prompt 中 section fallback 的 report_merge 规则
强化 scaffold-reporting skill 的 Report Merge Mode 输出 metadata 要求
用手工 merge 子任务恢复本次保险公司报告
```

验收：

```text
能生成一份完整合并报告 artifact。
```

### Phase 2：Reporter Contract 与 metadata 完整化

目标：让 scaffold reporter delegate 能机器判断章节草稿和合并报告。

动作：

```text
扩展 scaffold reporter contract
增加 section_draft 与 report_merge 两种 metadata 验收
记录 merged_section_refs 和 section_coverage
```

验收：

```text
日志、memory、artifact metadata 都能解释当前报告处于 section_draft 还是 final merged report。
```

### Phase 3：Reporter-only merge 稳定化

目标：只通过 reporter SOP / skill 让 merge 更稳定，不改全局 FINISH handler。

动作：

```text
强化 scaffold-reporting skill 的 Report Merge Mode
要求 merge task 不新增事实，只合并章节草稿
要求输出 metadata.kind="report_merge"
```

验收：

```text
s1-s4 完成后，SOP 指向 reporter report_merge；
report_merge 输出完整 report_revision。
```

### Phase 4：后续可选强化

目标：如果 SOP 仍不稳定，再评估更强的 runtime 约束。

动作：

```text
评估是否新增 artifact_type="section_draft"
评估是否在 reporter 相关 recovery 层提示 report_merge
暂不默认修改全局 FINISH handler
```

验收：

```text
只有在证明 SOP/contract 不够稳定时，才扩大 runtime 改动面。
```

## 8. 本次保险公司报告的具体合并建议

本次已有 artifact：

```text
research_observation-v1-spart_571af3835a59b059.md
outline-v1-spart_c9bed1839d26cfd8.md
research_observation-v2-spart_7441958720aa4f57.md
report-spact_call_00_mp2qpHhH8mht7ZuwQRWn8682-s1.md
s2-spact_call_01_PSDmSsrtG92Q5WzF5TSw3828.md
report_revision-v1-spart_22e895e5f8d0dda8.md
s4-spact_call_00_bgSo4qKSIDBpvRcIlBU37703.md
```

其中：

```text
s1 = 第一章：引言与排名依据
s2 = 第二章：融资与偿付能力及信誉度横向比较
s3 = 第三章：五年增长、分红与中国发展潜力横向比较
s4 = 第四章：综合评估与推荐
```

下一步应执行：

```text
delegate reporter:
  metadata.kind="report_merge"
  input_refs=[s1, s2, s3, s4, research_observation-v1, research_observation-v2, outline-v1]
```

输出：

```text
artifact_type="report_revision"
metadata.kind="report_merge"
completion_status="complete"
```

然后再 FINISH 该 merged report。

## 9. 最小验收标准

完成修复后，系统必须满足：

```text
1. SA 报告 SOP 明确 section fallback 完成后必须 report_merge
2. scaffold reporter contract 能区分 section_draft 与 report_merge
3. report_merge 不新增事实，只合并已有章节
4. final report_revision 覆盖全部 ScopeTree 一级章节
5. V1 不修改全局 FINISH handler，避免影响其他任务
```

只要这五点成立，当前“每一块都生成了但最后没合并”的问题就能闭环。
