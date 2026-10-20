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
| `report/<id>/research/<action_id>/summary.md` | Researcher 完整 summary |
| `report/<id>/tree/<version>.json` | 接受后的树版本 |

单文件写入使用临时文件 + 原子替换；不引入数据库或第二套报告状态通道。

## 3. 角色与工具

| 角色 | 工具与输入 | 行为 |
|---|---|---|
| Central | 原有 `sp_*` 控制工具；每次模型调用前注入 ledger 派生控制摘要 | 显式 start/resume/close、派发、两轮后提问；不设计结构、不裁决证据 |
| Outline | `read_file/grep/glob/ls` + `report_register_research_request`；输入包含请求、输入文件、材料目录、全部 summary、当前树、开放 gap | 读材料、登记/继续 gap、输出树 JSON、对 `awaiting_outline` gap 给 `gap_outcomes` |
| Researcher | `web_search` + `report_fetch_material` + 只读文件工具；不暴露 `web_fetch` | 调用部署配置的抓取工具实例并落盘；按 gap 报 `searched/not_attempted/technical_error`，不裁决是否解决 |
| Reporter | P1 活动报告中拒绝委派 | P2 再接入 Writer |

`report_fetch_material` 不新建 HTTP 客户端；它绑定运行时已装配的 `web_fetch`
实例，保存其返回文本，记录 `source_id/material_id/sha256/lines/content_scope/
upstream_truncation`。已知 Jina 提供方内部截断到 4096 字符，因此标记
`upstream_truncation=true`；`direct_fetch` 按返回中的 `<truncated>` 标记；未知
提供方标记 `unknown`。失败或异常不登记为材料。

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
  轮次与决定重放。
- `backend/tests/test_sp_report_materials.py`：长/短返回逐字落盘、同 URL 来源身份、
  同内容复用、不同内容新材料、抓取失败/异常/写失败不登记、截断标记。
- `backend/tests/test_sp_report_wiring.py`：真实 `make_sp_agent` + fake 模型（W）：
  start、控制摘要注入、P1 finish 拒绝、账本故障不降级、中途预算 60、串行判定、
  报告工具 metadata、非报告角色不变、报告 Researcher 关闭自动续跑。
- `backend/tests/test_sp_report_executor_e2e.py`：真实 `DelegateHandler` +
  `DR2SubagentExecutorAdapter` + 真实 `SubagentExecutor` + 真实报告工具（E）：
  Outline 先登记 gap 再接受树；Researcher 通过受控抓取工具保存原文并计一轮。

运行方式（242 的开发环境）：

```bash
cd backend
PYTHONPATH=$PWD/packages/harness:$PWD \
  /data2/pkuiti/songzhengxing_intern/sp2-242-venv/bin/python -m pytest \
  tests/test_sp_report_lifecycle.py tests/test_sp_report_materials.py \
  tests/test_sp_report_wiring.py tests/test_sp_report_executor_e2e.py -q
```

P1 交付时该组 28 项通过；原有 SP 回归 278 项、非报告/共享回归 211 项通过。
`ruff check` 与 `ruff format --check` 通过。

## 7. 未覆盖与风险

1. 真实模型规划质量、工具选择、`report_register_research_request` 参数正确性
   未验证；fake 模型只证明接线。
2. 未部署、未配置真实 `web_fetch` 提供方；上游截断只做已知提供方标注，未在目标
   provider 上验证全文可得性。
3. 远程 sandbox（AIO/E2B 等）是否与 host outputs 同路径挂载未验证；当前材料工具
   写 host outputs，Outline 通过 sandbox 文件工具读虚拟路径，需在目标 provider
   验证同一文件可读。
4. Outline 输入包对超大 summary/树采用显式截断 + 路径，未验证真实大树的输出容量。
5. P1 没有 Writer/终稿；任何活动报告都不能 `sp_finish`，这是阶段性限制而非最终
   交付语义。
6. `report_fetch_material` 只移除 Researcher 的 `web_fetch`；非报告角色集合不变。
7. 真实 provider 的 reasoning/thinking 设置按角色记录在 task metadata，但 P1 未
   强制某角色开关；Writer 的显式关闭留到 P2。
