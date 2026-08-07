# SA 报告生成 V1.1 修复计划

> 目标：修复当前 SP2 生成深度报告时暴露的问题，让 SA 流程真正约束最终报告，而不是只做到 skill 被加载。
> 范围：outline artifact gating、单次 reporter 生成、统一引用格式、Research Summary / Evidence Ledger / Numeric Claim Map、trace 可评测。

## 1. 当前问题判断

首轮 case 已经证明：

```text
researcher 加载了 scaffold-preresearch
outline 加载了 scaffold-outline
reporter 加载过 scaffold-reporting
最终 report 文件也生成成功
```

但这还不是理想的 SA 报告链路。核心问题是：

```text
skill routing 生效了
artifact gating 没有稳定生效
最终 reporter 路径没有稳定继承 SA 约束
引用链路没有从检索到报告统一
```

具体表现：

1. `outline` 被调用了，但没有看到持久化的 `outline` artifact。
2. `ScopeTree / Evidence Map / lightweight AGM State` 没有稳定成为 reporter 的输入。
3. reporter 被多次调用，最终真正写文件的一次只加载了 `stackplanner-reporting`，丢了 `scaffold-reporting`。
4. 首轮 reporter 加载 `scaffold-quality-gate` 会增加复杂度，容易触发 partial / retry。
5. Research Summary 目前更像长证据汇总，和 SA 里的紧凑规划摘要不一致。
6. 引用、数字、口径冲突没有形成贯穿全流程的结构化映射。

## 2. V1.1 目标流程

V1.1 先收敛为稳定主链路：

```text
researcher -> outline -> reporter -> finish
```

正常情况下 reporter 只调用一次。

不在 first draft 使用 `scaffold-quality-gate`。质量检查先放到后续 revision、verifier 或 offline evaluation。

### 2.1 researcher

必须输出：

```text
Research Summary
Evidence Ledger
Numeric Claim Map
evidence_gaps
```

要求：

- `Research Summary` 是压缩后的事实上下文和规划摘要，可以被 outline/reporter 使用。
- `Research Summary` 可以承载事实压缩，但其中每个关键事实和数字必须能回溯到 `[n]` 引用。
- `Evidence Ledger` 保存所有来源条目，统一编号为 `[1]`, `[2]`, `[3]`。
- `Numeric Claim Map` 保存数字、单位、指标、实体、时间范围、口径、来源编号。
- 从检索阶段开始就使用 `[n]` 引用编号，不使用 `E1` / `N1` 这类内部格式作为最终报告引用。

### 2.2 outline

必须输出持久化 `outline` artifact。

`artifact_content` 必须包含：

```text
ScopeTree
```

`artifact_metadata` 必须包含：

```text
evidence_map
agm_state
stable_candidate_set
explicit_dimensions_covered
evidence_gaps
```

如果 outline 没有成功持久化 artifact，不允许进入 reporter。

### 2.3 reporter

first draft 只加载：

```python
metadata={"skill_names": ["stackplanner-reporting", "scaffold-reporting"]}
```

暂时不加载：

```python
metadata={"skill_names": ["scaffold-quality-gate"]}
```

reporter 输入必须包含：

```text
user query
Research Summary
Evidence Ledger
Numeric Claim Map
ScopeTree
Evidence Map
materialized evidence bodies
```

最终输出：

```text
report_revision artifact
final Markdown report
```

## 3. 关于 Research Summary 是否能作为事实来源

这里调整原来的说法。

不应该说：

```text
禁止把 Research Summary 当事实来源。
```

更准确的约束是：

```text
Research Summary 可以作为压缩后的事实上下文使用；
但 Summary 中的关键事实、数字、判断必须带 [n] 引用；
最终报告不能引用无来源的 Summary 内容。
```

原因：

- 深度报告上下文很长，reporter 不可能每次只依赖原始网页全文。
- SA 的 Summary 本来就是信息压缩层，用于减少上下文压力。
- 真正要禁止的是“不可追溯的 Summary 事实”，不是 Summary 本身。

因此 V1.1 的规则是：

```text
Research Summary = 可用的压缩事实层 + 规划层
Evidence Ledger = 引用和来源真值表
Numeric Claim Map = 数字和口径真值表
Final Report = 只能使用可追溯到 [n] 的事实和数字
```

## 4. 统一引用格式

V1.1 全流程统一使用数字引用：

```text
[1], [2], [3]
```

不用：

```text
E1, E2, N1, N2
裸 URL
脚注式混乱编号
每个阶段重新编号
```

### 4.1 Evidence Ledger

推荐结构：

```json
{
  "source_id": 1,
  "citation": "[1]",
  "title": "",
  "url": "",
  "publisher": "",
  "published_at": "",
  "retrieved_at": "",
  "source_type": "official|research|media|database|company|other",
  "reliability": "high|medium|low",
  "claim_summary": "",
  "used_for": []
}
```

### 4.2 Numeric Claim Map

推荐结构：

```json
{
  "claim_id": "C1",
  "claim_text": "",
  "value": "",
  "unit": "",
  "metric": "",
  "entity": "",
  "time_scope": "",
  "region_scope": "",
  "methodology_or_scope": "",
  "source_ids": [1],
  "citation": "[1]",
  "confidence": "high|medium|low",
  "conflicts_with": []
}
```

注意：

- `claim_id` 只用于内部追踪，不作为报告引用格式。
- 报告正文引用只出现 `[1]`、`[2]` 这种数字编号。
- 如果一个数字来自多个来源，使用 `[1][3]` 或 `[1,3]`，具体格式全报告保持一致。

### 4.3 Research Summary 引用

Research Summary 中可以写：

```text
2025 年中国具身智能产业链进入量产验证阶段，核心变化包括整机厂商融资加速、核心零部件国产化推进、工业和商业服务场景先落地 [1][2]。
```

不可以写：

```text
据多方资料显示，产业链正在快速发展。
```

除非这句话只是规划性判断，不作为事实依据。

### 4.4 Final Report 引用

最终报告要求：

- 所有关键数字必须带 `[n]`。
- 所有公司订单、融资、出货、市场规模、增速、政策事实必须带 `[n]`。
- 来源列表按 `[n]` 输出。
- 同一来源在全流程中编号保持稳定。
- 不允许 report 阶段重新编一套和 researcher 不一致的编号。

## 5. `stackplanner-reporting` 是否必须保留

短期保留。

原因不是 `scaffold-reporting` 不能写报告，而是现在两个 skill 的职责不同：

```text
stackplanner-reporting:
  SP2 交付协议
  report_revision artifact
  write_file / created_paths
  metadata
  revision 版本语义

scaffold-reporting:
  SA 写作方法
  ScopeTree 约束
  Evidence Map 使用
  稳定候选集合
  横向比较
  引用和数字可追溯
```

所以 V1.1 先保留：

```text
stackplanner-reporting + scaffold-reporting
```

但要把问题记清楚：

```text
长期更理想的状态是把 SP2 delivery contract 合并进 scaffold-reporting，
让 SA 报告模式只加载一个 scaffold-reporting。
```

在 artifact / 文件 / revision contract 稳定前，不建议直接移除 `stackplanner-reporting`。

## 6. `scaffold-quality-gate` 暂缓

V1.1 first draft 不加载 `scaffold-quality-gate`。

原因：

- 当前问题是主链路不稳定，不是缺少质量检查。
- quality gate 会增加首轮 reporter prompt 负担。
- 没有稳定 outline artifact 时，quality gate 无法可靠检查 ScopeTree coverage。
- 正常无校验路径应该只调用一次 reporter。

后续使用位置：

```text
人工 revision
verifier subagent
offline eval
数据集跑分后的失败样例复盘
```

## 7. reporter 多次调用的修复目标

正常路径：

```text
researcher -> outline -> reporter -> finish
```

reporter 只调用一次。

允许再次调用 reporter 的情况：

1. 用户明确要求修改报告。
2. reporter 返回 `completion_status=partial|blocked`，且已有 report artifact 可修。
3. offline verifier 或人工确认指出具体失败项。

不允许：

```text
outline artifact 缺失时反复调用 reporter
quality gate 触发无目标 retry
最终生成路径只加载 stackplanner-reporting 而丢失 scaffold-reporting
```

修复要求：

- finalization 路径必须继承 first draft 的 `skill_names`。
- 如果 finalization 是单独 reporter call，也必须加载 `scaffold-reporting`。
- 更好的做法是 first draft reporter 直接完成文件生成，不再额外 finalization call。

## 8. outline artifact gating

CentralAgent prompt 先加硬约束：

```text
Do not delegate reporter until a current outline artifact exists.
The outline artifact must contain ScopeTree in artifact_content.
The outline artifact metadata must contain evidence_map and agm_state.
If outline artifact is missing, retry outline once before reporter.
```

如果 prompt 仍不稳定，再在 delegate handler 加 runtime guard：

```text
if target_agent == "reporter" and is_substantial_report:
    require research_observation artifact
    require outline artifact
    require outline.artifact_metadata.evidence_map
    require outline.artifact_metadata.agm_state
```

## 9. 实施优先级

```text
P0:
1. 修改 scaffold-preresearch：
   - Research Summary 压缩
   - Evidence Ledger 使用 [n]
   - Numeric Claim Map 使用 source_ids / [n]

2. 修改 scaffold-outline：
   - 必须持久化 outline artifact
   - artifact_content = ScopeTree
   - artifact_metadata 包含 evidence_map / agm_state

3. 修改 CentralAgent prompt：
   - reporter 前必须有 outline artifact
   - first draft reporter 不加载 scaffold-quality-gate
   - reporter skill_names = ["stackplanner-reporting", "scaffold-reporting"]

P1:
4. 修改 scaffold-reporting：
   - 接受 Summary 作为压缩事实上下文
   - 但事实和数字必须可追溯到 [n]
   - 最终报告使用统一 [n] 引用

5. 修复 reporter finalization：
   - 避免无必要二次/三次 reporter
   - 如果必须 finalization，也继承 scaffold-reporting

P2:
6. 加 runtime artifact guard。

7. 再接入 scaffold-quality-gate 到 revision / verifier / offline eval。
```

## 10. 验收标准

单个 case 至少满足：

```text
1. delegation 顺序是 researcher -> outline -> reporter -> finish
2. 正常无校验路径 reporter 只调用一次
3. artifacts 至少包含：
   - research_observation
   - outline
   - report_revision
4. outline artifact metadata 包含：
   - evidence_map
   - agm_state
5. first draft reporter 加载：
   - stackplanner-reporting
   - scaffold-reporting
6. first draft reporter 不加载：
   - scaffold-quality-gate
7. 最终报告使用 [n] 引用格式
8. 关键数字能回溯到 Numeric Claim Map
9. Research Summary 中事实也带 [n]，但不出现不可追溯事实
10. trace 能导出每步 input_refs / output_artifacts / skill_names / completion_status
```

## 11. 具体代码改动位置

V1.1 不是只改文档。需要改四类代码 / prompt 文件：

```text
skills/public/scaffold-preresearch/SKILL.md
skills/public/scaffold-outline/SKILL.md
skills/public/scaffold-reporting/SKILL.md
backend/packages/harness/deerflow/sp/central/prompt.py
backend/packages/harness/deerflow/sp/actions/handlers/delegate.py
backend/tests/test_sp_action_system.py
backend/tests/test_sp_specialist_subagents.py
backend/tests/test_sp_runtime_context.py
```

### 11.1 改 `scaffold-preresearch`

文件：

```text
skills/public/scaffold-preresearch/SKILL.md
```

当前问题：

```text
Research Summary 仍被描述成“不是 factual citation source”。
输出 contract 只有 source_refs，没有 Evidence Ledger / Numeric Claim Map。
引用格式没有强制从检索阶段开始使用 [n]。
```

要改：

1. 把 Research Summary 定义改成：

```text
Research Summary 是压缩后的事实层 + 规划层。
可以被 outline/reporter 使用。
但 Summary 中所有关键事实和数字必须带 [n]，并能回溯到 Evidence Ledger / Numeric Claim Map。
```

2. 增加 Evidence Ledger 输出要求：

```json
{
  "source_id": 1,
  "citation": "[1]",
  "title": "",
  "url": "",
  "publisher": "",
  "published_at": "",
  "retrieved_at": "",
  "source_type": "",
  "reliability": "",
  "claim_summary": "",
  "used_for": []
}
```

3. 增加 Numeric Claim Map 输出要求：

```json
{
  "claim_id": "C1",
  "claim_text": "",
  "value": "",
  "unit": "",
  "metric": "",
  "entity": "",
  "time_scope": "",
  "region_scope": "",
  "methodology_or_scope": "",
  "source_ids": [1],
  "citation": "[1]",
  "confidence": "high|medium|low",
  "conflicts_with": []
}
```

4. 输出 contract 改成至少包含：

```json
{
  "completion_status": "complete|partial|blocked",
  "queries": [],
  "explicit_dimensions": [],
  "stable_candidate_set": [],
  "evidence_ledger": [],
  "numeric_claim_map": [],
  "evidence_gaps": []
}
```

### 11.2 改 `scaffold-outline`

文件：

```text
skills/public/scaffold-outline/SKILL.md
```

当前问题：

```text
skill 已要求 outline artifact，但首轮 case 没看到持久化 outline。
需要把 artifact_content / metadata 的硬要求写得更强，减少模型返回普通大纲或空 artifact_content。
```

要改：

1. 明确 `artifact_content` 必须是 ScopeTree JSON，不是 Markdown 普通大纲。
2. 明确 `artifact_metadata.evidence_map` 必须使用 `[n]` source id 绑定到 leaf。
3. 明确 `agm_state` 必须存在，即使只是 lightweight。
4. 加失败规则：

```text
如果无法生成 ScopeTree JSON，completion_status 必须是 partial 或 blocked。
不能返回 complete。
不能 artifact_content=null。
```

### 11.3 改 `scaffold-reporting`

文件：

```text
skills/public/scaffold-reporting/SKILL.md
```

当前问题：

```text
现在写着 Research Summary 只做 task alignment，不能作为 factual citation source。
这和 V1.1 设计不一致。
```

要改：

1. 改成：

```text
Research Summary 可以作为压缩后的事实上下文。
但报告中的关键事实和数字必须能回溯到 [n]。
```

2. 强制最终报告引用格式：

```text
正文引用使用 [1], [2], [3]。
关键数字必须带 [n]。
来源列表按 Evidence Ledger 的 source_id 排序输出。
不允许 report 阶段重新编号。
```

3. 强制使用 Numeric Claim Map：

```text
所有市场规模、增速、融资、订单、出货量、排名、占比等数字必须来自 numeric_claim_map。
如果同一指标多来源冲突，必须并列说明口径差异。
```

### 11.4 改 CentralAgent SOP

文件：

```text
backend/packages/harness/deerflow/sp/central/prompt.py
```

当前位置：

```text
CENTRAL_AGENT_ACTION_PROMPT
  "For a substantial research/report/document request..."
```

当前问题：

```text
reporter metadata.skill_names 仍包含 scaffold-quality-gate。
没有足够硬地说 outline artifact 缺失时不能进入 reporter。
```

要改：

1. 第 1 步 researcher expected output 增加：

```text
Research Summary + Evidence Ledger + Numeric Claim Map, all using stable [n] citation ids.
```

2. 第 2 步 outline 增加：

```text
The outline must persist artifact_type="outline".
artifact_content must contain ScopeTree JSON.
artifact_metadata must contain evidence_map and agm_state.
```

3. 第 4 步 reporter 改成：

```text
metadata.skill_names=["stackplanner-reporting", "scaffold-reporting"]
```

删除 first draft 里的：

```text
scaffold-quality-gate
```

4. 增加 reporter 前置门槛：

```text
Do not delegate reporter until the current run has an outline artifact.
If outline artifact is missing or lacks evidence_map/agm_state, retry outline once before reporting.
```

5. 明确正常路径：

```text
Without human revision or verifier failure, call reporter once.
```

### 11.5 改 delegate handler，加 runtime guard

文件：

```text
backend/packages/harness/deerflow/sp/actions/handlers/delegate.py
```

建议先改 prompt。如果仍然出现 reporter 绕过 outline，再改这里。

推荐位置：

```python
class DelegateHandler:
    def handle(self, action: SPAction, context: HandlerContext) -> HandlerResult:
```

在 `_prepare_partial_report_retry` 之后、创建 `delegate_entry` 之前，加 reporter 前置检查。

需要新增 helper：

```python
def _current_artifact_ref(state: Mapping[str, Any], artifact_type: str) -> dict[str, Any] | None:
    ...

def _has_valid_scaffold_outline(state: Mapping[str, Any], *, run_id: str | None = None) -> bool:
    ...

def _is_scaffold_reporter_delegate(action: SPAction) -> bool:
    ...
```

检查逻辑：

```text
如果 action.target_agent == "reporter"
且 metadata.skill_names 包含 scaffold-reporting
且不是 revision
则要求：
  - 当前 run 有 outline artifact
  - outline.metadata.evidence_map 存在
  - outline.metadata.agm_state 存在
否则返回 error_recoverable，让 CentralAgent 先补 outline。
```

注意：

- 不要拦普通 reporter。
- 不要拦已有 report 的 revision。
- 不要拦非 SA 报告模式。
- 第一版可以只对 `metadata.skill_names` 包含 `scaffold-reporting` 的 reporter 生效。

### 11.6 检查动态 skill routing

文件：

```text
backend/packages/harness/deerflow/sp/runtime.py
```

相关函数：

```python
_apply_task_skill_policy(...)
_infer_task_skill_selection(...)
```

当前机制已经支持 `metadata.skill_names` 覆盖 subagent 默认 skills：

```text
如果 metadata.skill_names 存在，就用请求的 skill 列表替换 SubagentConfig.skills。
```

所以 first draft reporter 必须由 CentralAgent 显式传：

```python
metadata={"skill_names": ["stackplanner-reporting", "scaffold-reporting"]}
```

否则 reporter 默认只加载：

```python
skills=["stackplanner-reporting"]
```

### 11.7 是否改 `sp_specialists.py`

文件：

```text
backend/packages/harness/deerflow/subagents/builtins/sp_specialists.py
```

短期不建议静态改成：

```python
REPORTER_CONFIG.skills = ["stackplanner-reporting", "scaffold-reporting"]
```

原因：

```text
这会影响所有普通 SP reporter。
V1.1 只应该在 SA scaffolded report workflow 下加载 scaffold-reporting。
```

所以 V1.1 推荐：

```text
普通 reporter 默认保持 stackplanner-reporting。
SA 报告由 CentralAgent 通过 metadata.skill_names 显式选择 stackplanner-reporting + scaffold-reporting。
```

### 11.8 测试位置

至少加 / 改这些测试：

```text
backend/tests/test_sp_action_system.py
backend/tests/test_sp_runtime_context.py
backend/tests/test_sp_specialist_subagents.py
```

测试点：

1. `test_sp_runtime_context.py`

已有动态 skill policy 测试。补一条：

```text
reporter metadata.skill_names=["stackplanner-reporting", "scaffold-reporting"]
时，最终 selected.skills 正好是这两个，不包含 scaffold-quality-gate。
```

2. `test_sp_specialist_subagents.py`

保持：

```text
REPORTER_CONFIG.skills == ["stackplanner-reporting"]
```

并说明 SA skills 通过 delegation metadata 动态加载。

3. `test_sp_action_system.py`

新增 handler 级测试：

```text
SA reporter delegation 没有 outline artifact -> error_recoverable
SA reporter delegation 有 outline artifact 但没有 evidence_map/agm_state -> error_recoverable
SA reporter delegation 有合法 outline artifact -> executor 被调用
```

4. 如果只先改 prompt，不改 runtime guard，则先加 prompt 文本断言：

```text
CENTRAL_AGENT_ACTION_PROMPT 包含：
- metadata.skill_names=["stackplanner-reporting", "scaffold-reporting"]
- 不包含 first draft scaffold-quality-gate
- Do not delegate reporter until the current run has an outline artifact
```

## 12. 最小可执行改动顺序

建议按下面顺序提交：

```text
Commit 1: prompt/skill 修复
  - scaffold-preresearch
  - scaffold-outline
  - scaffold-reporting
  - central/prompt.py
  - prompt/skill tests

Commit 2: handler guard
  - delegate.py
  - test_sp_action_system.py

Commit 3: trace/eval 补强
  - 确认 run_events 已有 skill_names/input_refs/output_artifacts
  - 如缺字段，再补 run event metadata
```

先做 Commit 1 就能验证：

```text
quality gate 是否从 first draft 消失
最终 reporter 是否带 scaffold-reporting
Summary 和报告引用是否统一为 [n]
```

如果还出现没有 outline artifact 就进入 reporter，再做 Commit 2 的 runtime guard。
