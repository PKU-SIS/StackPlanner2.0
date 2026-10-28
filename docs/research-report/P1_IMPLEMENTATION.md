# SP2.0 报告能力 P1 实施说明

- 日期：2026-10-20
- 分支：`zx_sp2research`
- 依据：`SP2_REPORT_IMPLEMENTATION_PLAN_v3.md`
- 状态：P1 实施与分层验证完成；**未**进入 P2/P3，**未**部署，**未**调用付费模型。

## 1. P1 范围

已打通：

```text
需求保存 -> 预检索与材料保存 -> Outline 规划/读取材料
-> 登记研究请求（gap） -> Central 派发 -> Outline 判断结果
-> 两轮未解决后主动问用户 -> 用户回复入账 -> Outline 继续处理
```

未包含：节点 Writer、局部写作反馈、拼接、引用编译、整合审查、终稿和交付门
（P2/P3）。P1 中 `sp_finish` 对活动报告一律拒绝。

## 2. 权威状态与文件

`report/current.json` 只保存 `report_id`；`report/<id>/ledger.json` 是报告业务状态
唯一权威。Artifact 只是有效文件的登记视图。

| 路径 | 内容 |
|---|---|
| `report/current.json` | 当前报告指针 |
| `report/<id>/request.md` | 全部可见用户消息按时间拼接的请求全文（不覆盖为最后一句） |
| `report/<id>/user_inputs/<input_id>.md` | 逐条用户输入，按 message id / interaction id 去重 |
| `report/<id>/ledger.json` | gap、action、轮次、用户决定、材料、summary 引用 |
| `report/<id>/materials/<source_id>/<sha>.txt` | 抓取工具实际返回文本 |
| `report/<id>/materials/catalog.md` | 由账本派生的材料目录 |
| `report/<id>/research/<action_id>/summary.md` | Researcher summary，或协议解析失败时保留的 raw result |
| `report/<id>/tree/<version>.json` | 接受后的树版本 |

单文件写入使用临时文件 + 原子替换；不引入数据库或第二套报告状态通道。

## 3. 角色与工具

| 角色 | 工具与输入 | 行为 |
|---|---|---|
| Central | 原有 `sp_*` 控制工具；每次模型调用前注入 ledger 派生控制摘要 | 显式 start/resume/close、派发、两轮后提问；不设计结构、不裁决证据 |
| Outline | `read_file/grep/glob/ls` + `report_register_research_request`；输入包含请求、输入文件、材料目录、全部 summary、当前树、开放 gap | 读材料、登记/继续 gap、输出树 JSON、对 `awaiting_outline` gap 给 `gap_outcomes` |
| Researcher | `web_search` + `report_fetch_material` + 只读文件工具；不暴露 `web_fetch` | 调用部署配置的抓取工具实例并落盘；按 gap 报 `searched/not_attempted/technical_error`，不裁决是否解决 |
| Reporter | P1 活动报告中拒绝委派 | P2 再接入 Writer |

`report_fetch_material` 不新建 HTTP 客户端；在 `direct_fetch` 提供方下它取原始响应
字节，按魔数/内容类型识别 PDF 并交给现有 `convert_file_to_markdown` 转成 Markdown
后保存，`content_scope=converted_pdf_markdown`；扫描件、空文本或转换失败不登记
材料。其它提供方仍保存运行时已装配的 `web_fetch` 实例返回文本，记录
`source_id/material_id/sha256/lines/content_scope/upstream_truncation`。已知 Jina
提供方内部截断到 4096 字符，异步工具实例按 `coroutine` 识别后标记
`upstream_truncation=true`；`direct_fetch` 按返回中的 `<truncated>` 标记；未知
提供方标记 `unknown`。失败或异常不登记为材料。

报告 Researcher 的输出协议解析失败、且没有 recovered tool evidence 时，适配器把
`raw_result` 作为 `artifact_content` 保存（上限 24k，超出保留头尾并加
`<raw-result-truncated; head and tail preserved>` 标记），`summary` 仍压缩到 700
字符；metadata 记录 `raw_result_preserved` / `raw_result_truncated`。保留原始内容
不等于协议完整：不填 `gap_attempts`、不伪造 `searched`、不改变完成状态。

## 4. 缺口与轮次

- 新 gap：`rounds=0, allowed_rounds=2`；`continue` 保留 gap id 和历史，不自动改变
  用户硬要求。
- `begin_dispatch` 只写 in-flight action；研究结果接受时按每个 gap 的声明执行情况
  计轮：真实成功执行才 `searched` 计一轮；无成功工具执行、异常或技术失败只记
  `technical_attempt`，不计有效轮次。
- 每次研究结果都进入 `awaiting_outline`。Outline 输出必须覆盖所有
  `awaiting_outline` gap；`unresolved` 且轮次未满回 `open`，达到允许轮次进入
  `awaiting_user`。
- `sp_ask_human` 的 `metadata.report_gap_ids` 绑定 gap；回答只入账并回到
  `awaiting_outline`，不自动关闭 gap。`continue_research` 需正数
  `additional_rounds`；`change_scope` 关联用户输入；`no_decision` 保持未解决。
- 账本损坏时返回技术错误，不降级为“未启用”，也不恢复旧 Reporter 强制交付。

## 5. 公共接线

| 文件 | 必要性 |
|---|---|
| `sp/report/store.py` | 报告路径、原子写、材料目录派生 |
| `sp/report/ledger.py` | gap/action/轮次/输入/用户决定的确定性状态转换 |
| `sp/report/materials.py` | 绑定真实抓取工具并落盘 |
| `sp/report/tree.py` | 树结构、引用、定位、删除/父子校验与版本接受 |
| `sp/report/packets.py` | Outline/Researcher 的输入包 |
| `sp/report/hooks.py` | 模式解析、派发准备、结果接受、HITL、start/resume/close、finish 门 |
| `sp/report/context.py` | 每次 Central 模型调用前的派生摘要注入 |
| `sp/report/tools.py` | Outline 登记的 Agent 工具 |
| `sp/runtime.py` | 报告角色定向装配、60 动作预算、控制上下文中间件 |
| `sp/agent_tools.py` | `sp_replan/sp_ask_human` 可选 metadata；报告模式退出旧报告收尾规则；串行派发；报告预算 |
| `sp/actions/handlers/delegate.py` | 派发前绑定、成功登记前接受、执行异常释放 in-flight |
| `sp/actions/handlers/replan.py` | 显式 start/resume/close |
| `sp/actions/handlers/human.py` | HITL 问题绑定 gap |
| `sp/actions/handlers/finish.py` | P1 活动报告拒绝交付 |
| `sp/actions/router.py` | 报告角色跳过旧同目标阻断 |
| `sp/subagents/dr2_adapter.py` | 关闭报告 Researcher 账外自动续跑 |
| `skills/public/report-outline`、`report-research` | 条件角色 Skill |

## 6. 验证

测试文件：

- `backend/tests/test_sp_report_lifecycle.py`：需求全文、start/resume/close、gap
  new/continue、无效树不接受、两轮→提问→回答→处置、技术失败、执行异常释放、
  轮次与决定重放；普通补充 `apply_to_plan`、close 后新建/普通任务、delivered
  分支、并发登记。
- `backend/tests/test_sp_report_materials.py`：长/短返回逐字落盘、同 URL 来源身份、
  同内容复用、不同内容新材料、抓取失败/异常/写失败不登记、截断标记；同步/异步
  抓取、运行中事件循环、并发材料身份。
- `backend/tests/test_sp_report_wiring.py`：真实 `make_sp_agent` + fake 模型（W）：
  start、控制摘要注入、P1 finish 拒绝、账本故障不降级、中途预算 60、串行判定、
  报告工具 metadata、非报告角色不变、报告 Researcher 关闭自动续跑；HITL 与通用
  输入同步去重、HITL 失败传播、报告委派跳过旧关键词改派。
- `backend/tests/test_sp_report_executor_e2e.py`：真实 `DelegateHandler` +
  `DR2SubagentExecutorAdapter` + 真实 `SubagentExecutor` + 真实报告工具（E）：
  Outline 先登记 gap 再接受树；Researcher 通过异步受控抓取保存原文并计一轮；
  Outline 通过真实 `read_file` 读取已保存材料后再接受树；同一 ToolNode 响应内
  两个材料调用并发保存且身份/目录完整。

运行方式（242 的开发环境）：

```bash
cd backend
PYTHONPATH=$PWD/packages/harness:$PWD \
  /data2/pkuiti/songzhengxing_intern/sp2-242-venv/bin/python -m pytest \
  tests/test_sp_report_lifecycle.py tests/test_sp_report_materials.py \
  tests/test_sp_report_wiring.py tests/test_sp_report_executor_e2e.py -q
```

P1 交付时该组 **46** 项通过；原有 SP 回归 278 项、非报告/共享回归 211 项通过。
`ruff check` 与 `ruff format --check` 通过。

## 6.1 独立审查 R1—R6 收尾

- **R1 异步抓取**：材料工具优先调用配置工具的 `ainvoke`，同步工具仍走 `invoke`；
  在运行中的事件循环里用独立线程执行，避免嵌套 `asyncio.run`。提供方身份从
  `coroutine`/`func` 的真实 callable 取，StructuredTool 包装类不再遮住 Jina。
  已验证同步、异步、运行中事件循环、异常、截断，以及保存后真实 `read_file` 读取。
- **R2 账本并发**：`store.report_lock` 按键串行化单个 ledger 的读—改—写（进程内
  RLock，当前单 Gateway 部署边界）；材料抓取在锁外，返回后在锁内基于最新账本分配
  `S/m` 身份并保存。登记工具、接受、HITL、输入同步、start/resume/close 使用同一
  机制。已用两个并发材料调用和两个并发 gap 登记验证身份与目录完整。
- **R3 轮次事实**：只有 host 记录的 `host_tool_execution_records` 与材料工具账本
  执行记录能计轮；模型自填的 `tool_execution_records` 被忽略。仅
  `web_search/web_fetch/report_fetch_material` 算研究执行，普通 `read_file` 不计。
  搜索成功但无命中计一轮；全部技术失败、无 host 执行、仅模型声明均不计轮，只记
  `technical_attempt`。
- **R4 输入应用与 HITL**：`user_decisions` 新增 `apply_to_plan`（`gap_ids=[]`），
  普通补充不必虚构 gap；HITL 同时记录 `interaction_id` 与 `source_message_id`，
  通用输入同步按同一消息身份去重；HITL/输入同步异常不再静默吞掉。
- **R5 报告生命周期**：active 不自动覆盖；delivered 可 resume 或新建；abandoned
  保留历史但允许新建普通任务；close 必须引用真实用户取消输入
  `report_input_id`，不能只凭任意 reason。
- **R6 角色路由**：报告 active 时，已明确为 outline/researcher/reporter 的委派在
  `_action_payload` 跳过旧关键词改派；router 的同目标豁免也只作用于这三个角色，
  coder/perception 仍保留旧规则。

## 7. 可追踪轨迹（P1 实测）

同一条 `r1` 轨迹（`outputs/P1_TRACE_20261020.json`）：

| 步骤 | 可观察值 |
|---|---|
| 需求保存 | `request_chars=3187`，输入 `u1/u2`，`report/r1/request.md` |
| 预检索 | 保存 orientation summary，无 gap、无轮次 |
| Outline 登记 | `report_register_research_request` -> `g1 open rounds=0 allowed=2`，`node_ids=[]` |
| 树接受 | tree v1，`n2.gap_ids=["g1"]`，账本 gap 关联 `n2` |
| 材料保存 | `S1/m1`，65 chars/1 line，`sha256=cdc74d…`，路径 `report/r1/materials/S1/…txt`；Outline 按行读取内容一致，`read_truncated=false` |
| 研究第 1 轮 | summary 保存到 `research/a-r1/summary.md`；`g1 awaiting_outline rounds=1` |
| Outline 判定 1 | `unresolved` -> `g1 open rounds=1` |
| 研究第 2 轮 | `g1 awaiting_outline rounds=2` |
| Outline 判定 2 | `unresolved` -> `g1 awaiting_user rounds=2`，control `next_action=ask_human` |
| 用户提问 | `sp_ask_human` 绑定 `g1`，HITL `sphitl_trace` |
| 用户回复 | 入账 `u3`，gap 仅回到 `awaiting_outline`，不自动关闭 |
| Outline 处置 | tree v4，`g1 resolved`，`u3 applied=true`；`sp_finish` 仍因 P1 无终稿被拒 |

## 8. 未覆盖与风险

1. 真实模型规划质量、工具选择、`report_register_research_request` 参数正确性
   未验证；fake 模型只证明接线。
2. 未部署、未配置真实 `web_fetch` 提供方；上游截断只做已知提供方标注，未在目标
   provider 上验证全文可得性。
3. 远程 sandbox（AIO/E2B 等）是否与 host outputs 同路径挂载未验证。本轮 E 测试在
   local sandbox + 实际 thread outputs 上验证了材料保存后真实 `read_file` 可读；
   远程 provider 仍需单独核对。
4. Outline 输入包对超大 summary/树采用显式截断 + 路径，未验证真实大树的输出容量。
5. P1 没有 Writer/终稿；任何活动报告都不能 `sp_finish`，这是阶段性限制而非最终
   交付语义。
6. 报告账本锁是进程内 `threading.RLock`，按单个 ledger 路径串行；当前单 Gateway
   进程边界成立，多进程/多实例写入未覆盖。
7. 真实模型的规划质量、工具选择、`user_decisions`/`gap_attempts` 参数正确性仍未
   验证；fake 只证明接线与 host 约束。
8. `report_fetch_material` 仍以 `Error:` 约定识别提供方文本错误；不符合该约定的
   错误文本可能被保存为材料，需在目标 provider 上核对。
