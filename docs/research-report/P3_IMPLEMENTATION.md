# SP2.0 报告能力 P3 实施说明

- 日期：2026-10-21
- 分支：`zx_sp2research`
- 范围：确定性装配、稳定引用/来源列表、Outline `integrate_review`、局部编辑、最终报告、交付与已交付报告 resume
- 不包含：P4 真实模型验收、部署、付费模型调用；不新增 reviewer/常驻 Agent

## 1. 生产调用与顺序

1. Central 用现有 `sp_delegate` 调用 `outline`，metadata
   `report_task="integrate_review"`、stage `verification`。host 在派发前调用
   `assembly.ensure_candidate`，生成或复用当前候选并构造审查 packet。
2. Outline 返回 `artifact_content` JSON（权威 review），`artifact_metadata` 只保留
   `artifact_type`、`report_task`、简要 `status`。
3. host 校验 digest/candidate hash；局部 edits 先按唯一 anchor 应用到当前候选，
   写新候选并标记 `pending_review`；本次响应中的 ready 不成立。Central 必须通过
   新的 `sp_delegate` 再次派发，host 不调用模型、不自动循环。
4. 无 edits 的 ready 审查通过后，host 复制候选到 `final/report-<n>.md`，登记
   `final_report` Artifact，再提交 ledger 的 final/review 状态。
5. 首次 `sp_finish` 从当前 final/ready review 完成 delivery 事务；不要求预先存在
   delivered。失败不写 delivered。重复 finish 幂等。
6. delivered 报告 `sp_replan resume`：历史 delivery/final 保留，当前 review 失效；
   重新装配/审查/登记 final/交付后才可再次 finish。

## 2. 数据与文件

- `assembly/<candidate_id>.md`、`assembly/<candidate_id>.json`：候选正文和 manifest
  （按树顺序的 sections、source table、citation map、edits）。
- `review/<review_id>.json`：完整审查结构（binding、findings、edits、结论）。
- `final/report-<n>.md`、`final/report-<n>.json`：已审查 final 及绑定记录。
- `final/dlv<n>.json`：delivery 记录。
- ledger 新增：`candidates/candidate`、`reviews/review`、`final_reports/
  final_report`、`delivery_history/delivered`、`resume`。
- 原始 drafts 和旧候选不被覆盖；替换只改 current 指针。

## 3. 关键约束落实

### 3.1 候选复用与编辑

- `base_inputs_digest` 覆盖 tree/hash、requirements、inputs、draft hashes、
  materials versions。
- `inputs_digest` 再绑定当前候选 hash 和已应用 edits。
- 底层输入未变时，`ensure_candidate` 直接复用当前编辑后候选（包括
  `pending_review`），不从 drafts 重建；连续两轮编辑保留先前编辑。
- 树、草稿或相关输入变化时重建候选；旧候选 superseded、旧 review stale、
  旧 final superseded；不重放旧 edits。
- 编辑仅允许 `bridge/terminology/format/dedupe`，anchor 在当前候选对应节点
  区段恰好出现一次；拒绝 heading/结构变化、引用集合变化、重复锚点、非当前
  draft。host 不按文本长度判断编辑性质。

### 3.2 审查绑定

- review 必须回传 packet 的 `inputs_digest` 和 `candidate_sha256`；不匹配直接
  拒绝。
- `control_summary` 动态检查 base/candidate/review hash；输入变化显示 `stale`。
- `sp_finish` 逐项检查 unfinished write requests（含 pending/technical_failed）、
  在途 research、feedback（含 handed_off）、gap、未应用输入、draft review、
  ready review 和 current final。
- 待整合审查/open edits 不阻止再次审查；但它们阻止 final/finish。

### 3.3 final 与 delivery

- final 只在 ready 且绑定当前 candidate 后登记；final 文件/Artifact 先成功，再写
  ledger。
- delivery 在 finish 事务中写 `final/dlv<n>.json` 和 ledger；任一步失败不写
  `delivered`。
- 重复同 final finish 返回同一 delivery；不重复登记。
- 来源：保留 `S<n>` 身份；一个 source 的多个 material 版本并列；程序校验
  source/material 引用身份，证据支持由 Outline 判断；来源列表由 host 确定性生成。
- 不新增强制终稿用户确认。

### 3.4 已交付 revise

- resume 将旧 delivery 放入 `delivery_history`，旧 review 置 stale，旧 final 置
  historical 且不再作为 current。
- 保留 tree/drafts；当前候选可复用，未受影响草稿无需重写。
- 必须重新 ready review + 新 final + 新 delivery；旧 delivered 不满足新 finish。

### 3.5 语义职责边界

程序只校验编辑的允许 kind、当前候选区段唯一 anchor、节点剩余非空、引用身份和
标题/结构形式；它不判断一句话是否改变了事实、结论、口径或证据支持。不得把上述
校验描述成程序“保证事实边界不变”。事实/结论/证据是否仍成立，是 Outline 的编辑
约束和 findings/复审职责；实质变化必须按契约返回 revise/Writer。

## 4. 验证

- 报告组：**76 passed**（含 P1/P2 回归与 P3）。
- 原有 SP/非报告/共享回归：**489 passed**。
- `ruff check` / `ruff format --check`：通过。
- P3 定点测试 `backend/tests/test_sp_report_p3.py`（17 项）：
  - 正常候选 → ready → final → 首次 finish；校验 delivered 之前 final 已存在。
  - 连续两轮局部编辑：复用 c2/c3，第二/第一编辑保留，final 内容与审查绑定，
    交付后内容一致。
  - 实质 revise/blocking：不登记 final，finish 拒绝。
  - 输入变化：旧 review stale、旧 final 失效、finish 拒绝；应用输入后重审。
  - pending/technical_failed 写作请求：仍可再次委派 integrate_review，但 finish
    必须被未完成请求阻止；标记 completed 后才交付。
  - delivery 文件损坏：不写 delivered；修复后成功；重复 finish 幂等。
  - 已交付 resume：历史 delivery 保留、旧 review stale、旧 final historical、
    重新审查/交付。
  - source 多 material 版本与稳定 `S<n>` 来源列表。
  - 同一候选主动重审：允许，新 final review id/版本，旧 final 不再 current。
- `backend/tests/test_sp_report_executor_e2e.py`：
  `test_real_executor_integration_review_and_first_finish` 使用真实
  `DelegateHandler`、`DR2SubagentExecutorAdapter`、`SubagentExecutor`、ToolNode
  和真实 Outline 角色装配；fake LLM 返回权威 review JSON，host 完成 final 和
  首次 finish。

## 5. 独立复核修复 R1-R5

- **R1 终稿引用一致性**：活动报告 finish 从账本当前 `final_report` 取 artifact id；
  显式旧/其他 ref 明确拒绝，不写 delivery；成功返回、`sp_last_final_artifact_ref`
  和 delivery 均指向同一当前 final。`test_finish_rejects_old_final_ref_and_returns_current_final`
  使用内容不同的 v1/v2，旧 ref 被拒，新 ref 与 delivery 一致。
- **R2 历史报告不接管普通 finish**：`deliver_report` 只处理 active 报告；
  abandoned/delivered 报告仅在显式引用其历史 final 时拒绝，普通 finish 继续走原
  路径。`test_cancelled_or_delivered_report_does_not_take_over_ordinary_finish`
  通过真实 `FinishHandler` 验证取消后、交付后的普通任务均可结束，并验证显式历史
  ref 被拒。
- **R3 父子顺序装配**：按 parent_id 建立 children，按节点数组中的相对顺序做确定性
  前序遍历，不把 nodes 数组直接当章节顺序。`test_assembly_follows_parent_child_order_not_array_order`
  使用 `[A, B, A_child, B_child]` 验证输出 A→A_child→B→B_child。
- **R4 实际文件绑定**：候选复用、审查接受、交付的 digest/binding 读取实际
  tree/draft/input/material 文件并校验 ledger hash；候选/final 文件自身也按实际
  hash 校验。`test_draft_file_change_after_review_blocks_reuse_and_delivery`、
  `test_review_acceptance_rejects_changed_input_files` 验证修改草稿后不能再复用旧
  candidate/ready 或交付。
- **R5 dedupe 空 replacement**：仅 `kind=dedupe` 允许空 replacement；anchor 唯一、
  引用集合、heading 结构、节点剩余非空仍校验。`test_dedupe_edit_can_delete_redundant_sentence`
  验证删除重复句且原 draft 不变。
- **交付重放**：delivered 记录携带原 finish 的 `action_id`/`run_id`/final artifact。
  同一 host action/run 重放时直接返回同一 delivery id 和 final artifact，不再次写
  delivery；不带显式引用、带当前 final 引用两种形式都覆盖。新的普通任务不匹配
  action/run，继续走原 finish；新请求显式引用历史 final 仍拒绝。
  `test_finish_replay_returns_same_delivery_and_final_ref_without_outer_state`
  故意不应用第一次 `state_update`，证明依赖账本而不是外层缓存。

## 6. 提交

- `8a27ceb` feat(sp-report): add P3 candidate assembly, citation digest, and local edits
- `1190c59` feat(sp-report): wire integrate review, final delivery, and delivered resume
- `7370a63` test(sp-report): cover P3 assembly, edits, delivery, and resume
- `ff50d57` docs(sp-report): record P3 implementation and verification
- `93051a4` fix(sp-report): bind P3 finish refs, file hashes, and tree order
- `a161099` test(sp-report): cover P3 review findings R1-R5

## 7. 未覆盖边界

1. 真实模型对 integration review、局部编辑、findings 的判断质量未验证；fake LLM
   只验证 host 契约和状态转换。
2. 远程 sandbox 的路径映射未验证；E 测试使用 local sandbox/隔离临时 outputs。
3. 组装只支持当前 tree 的 writable 节点顺序和 Markdown 标题层级；复杂目录/模板
   未实现。
4. 多进程账本锁未覆盖，仍沿用进程内 `RLock`。
5. 未实现 P4 真实验收，也未部署或调用真实付费模型。
6. `sp_finish` 的 delivery 事务在 ledger 写失败时可能留下孤儿 delivery/final 文件；
   不会错误标记 delivered，但未做跨文件回滚清理。
