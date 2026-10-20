# SP2.0 报告能力 P2 实施说明

- 日期：2026-10-20
- 分支：`zx_sp2research`
- 依据：`SP2_REPORT_IMPLEMENTATION_PLAN_v3.md` 第 8.2 节及 P2 补充约束
- 范围：节点写作、node_feedback、局部试写回看、草稿 keep/rewrite/drop、规格变化检查
- 不包含：P3 拼接、引用编译、整合审查、终稿交付；不部署；不调用真实付费模型

## 1. 已实现行为

### 1.1 Writer 输入和指定路径

Outline 的 `write_requests` 每个 key 只指向一个 writable 节点；`feedback_wanted`
决定成功试写后是否必须回看。host 绑定 Writer packet：完整节点规格、引用需求
正文、证据 material_id/路径/行范围/use、父子/相邻/关系上下文、语言风格；rewrite
时附旧稿路径与内容。host 分配
`report/<id>/drafts/<node_id>/<draft_id>.md`，Writer 只能写该路径。

### 1.2 草稿接受

host 在首次 Artifact 登记前校验 host 分配路径、UTF-8 非空、node_id、大小上限；
通过后登记 `node_draft` Artifact，写 draft 记录（生成指纹、生成依据、sha256、
review 状态），只移除该 write key 的 pending。报告保持 active，其他写作/审查
仍是阻塞项，`sp_finish` 不因一节完成而可用。

`feedback_wanted=true` 的成功草稿会置 `draft_review_due`；Outline packet 同时提供
草稿完整路径和截断标记正文，回看处置后清除。false 不强制回看。

### 1.3 node_feedback

Writer 可独立返回 `node_feedback`，不需要正文文件。host 只校验 node/引用/枚举，
记录 feedback open，标记 request `feedback_pending`、`outline_review_due`；不产生
accepted draft，不接受“缺乏证据”占位正文作为完成稿。草稿验收和反馈接受分开。

Outline 在 tree 的 `feedback_dispositions` 中处置：`revise|needs_research|keep|
dismiss`。`needs_research` 只把 feedback 标记为 `handed_off` 并引用
`report_register_research_request` 返回的 gap_id，不解决 gap，也不自动恢复写作；
request 进入 blocked。后续 tree 只有在该 gap resolved/not_needed 后包含同一 write
key 才允许继续，否则明确拒绝。研究返回后由 Outline 重新规划。

### 1.4 write request 身份

write_requests 持久化 `pending|in_flight|feedback_pending|blocked|
technical_failed|completed|superseded`、round、attempts、action_id、draft_id。
完整树重复同一个 completed key 不会重新入队；技术失败保留在
`technical_failed`，新的 action 作为新 attempt 重试；新一轮写作需要新 key 或
明确的 continuation，不复用已完成请求身份。

### 1.5 keep/rewrite/drop 与变化检测

host 计算节点 spec fingerprint：标题、问题、论证、结论、证据、约束、
requirement_ids 及其需求正文/硬约束、父节点标题/类型、关系节点标题/问题。
tree 接受时，若当前草稿的 applicable fingerprint 变化、review open 或节点被删除，
必须提供 `draft_dispositions`；缺失则拒绝整棵树并列出原因。`keep` 记录 Outline
对新规格的适用确认（新 applicable/confirmed fingerprint、keep reason），同时保留
草稿原始生成依据（generated fingerprint/spec snapshot）；同一确认规格下次不再
重复要求处置。`rewrite/drop` 将旧稿退出 current，保留历史，不自动用旧稿。
指纹未变仍允许 Outline 主动 keep/rewrite/drop。

树级 `last_changes` 记录 requirement 新增/删除/正文改变、节点新增/删除/改名/
移动/关系变化；Outline packet 和 control summary 可见。程序只提示变化和要求
必要处置，不判断语义影响、不自动重写。

### 1.6 Writer 工具与 thinking

Writer 沿用 `reporter` 角色，但条件装配为 `read_file/grep/glob/ls/write_file`，
Skill 为 `report-node-writing`，system prompt 为 Writer 专用契约。runtime 对
`report_role=="writer"` 通过现有 `SubagentExecutor(thinking_enabled=...)` 参数
显式传 `False` 并记录 task metadata；Outline/Researcher/普通角色不受影响。

## 2. 文件职责

| 文件 | 职责 |
|---|---|
| `sp/report/store.py` | drafts 路径 |
| `sp/report/writing.py` | write request、draft、feedback、disposition、指纹变化的上层状态转换 |
| `sp/report/ledger.py` | P2 状态字段、control summary、finish blockers |
| `sp/report/tree.py` | fingerprint、树级 change summary |
| `sp/report/packets.py` | Writer packet、Outline packet 的 drafts/feedback/write 状态 |
| `sp/report/hooks.py` | Writer 派发、草稿接受、feedback、树接受顺序、finish 仍拒 |
| `sp/runtime.py` | Writer 条件装配与 thinking=False 辅助 |
| `sp/actions/handlers/delegate.py` | 活动报告跳过旧 reporter retry/revision guard |
| `sp/report/prompts.py`、`skills/public/report-node-writing/SKILL.md` | Writer 契约 |

## 3. 验证

测试：

- `backend/tests/test_sp_report_writing.py`：10 项。覆盖 feedback_wanted 成功回看、
  draft content/path、completed request 重交不重跑、feedback-only、needs_research
  阻塞与解除、requirement 正文/父节点变化、keep 保留生成依据且同规格不重复、
  指纹未变主动 keep、rewrite/drop、技术失败重试、Writer 角色工具/prompt/skill、
  thinking 辅助。
- `backend/tests/test_sp_report_executor_e2e.py`：真实 `DelegateHandler` +
  `SubagentExecutor` + ToolNode + 真实 `write_file`/`read_file`（LLM fake）；
  Writer 按 host 虚拟路径写入、host 接受；`create_chat_model` 捕获
  `thinking_enabled=False`（模型 supports_thinking=true 的配置下）。
- 原 P1 46 项继续通过。

结果：报告组 **57 passed**；原有 SP + 非报告/共享回归 **489 passed**；
`ruff check` / `ruff format --check` 通过。

## 4. 未覆盖边界

1. 真实模型写作质量、node_feedback 语义、keep/rewrite 判断未验证；本轮 fake LLM
   只验证接线和 host 约束。
2. 远程 sandbox 的 write_file 路径映射未验证；E 测试使用 local sandbox 与隔离
   临时 outputs。
3. P2 的 draft 大小上限 2MB，未验证超大节点正文。
4. 多进程账本锁未覆盖（沿用 P1 的进程内 RLock）。
5. 未实现 P3 拼接/引用编译/整合审查/终稿；`sp_finish` 对活动报告仍拒绝。
6. 需求正文变化只进入 fingerprint 和可见 change summary；程序不做语义影响判断，
   由 Outline 选择 keep/rewrite/drop。

## 5. 提交

- `6be0155` feat(sp-report): add P2 draft, feedback, and spec-change state
- `d3aeb56` feat(sp-report): wire Writer drafts, feedback, and thinking-off role
- `6b67288` test(sp-report): cover P2 writer, feedback, keeper, and spec-change behavior
- `8d29b0a` docs(sp-report): record P2 scope, state contract, and verification
- `8f0ff31` chore(sp-report): align P2 finish wording and module doc
