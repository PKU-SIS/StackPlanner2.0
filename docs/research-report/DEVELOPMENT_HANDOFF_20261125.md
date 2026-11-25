# 多轮报告开发交接（2026-11-25）

## 当前可交接程度

可以接手功能开发与回归。基础版本隔离、Writer-first 局部改稿、条件 Outline/Researcher 调度已有实现和真实试验记录；当前不是“所有复杂需求已稳定通过”的发布状态。不要从零重做，也不要通过新增拒绝门槛来改善表面成绩。

## 工作区与修改状态

- 开发目录：`/data/sp/yzb/StackPlanner2/StackPlanner2.0_Report_multiturn`。
- 分支：`feature/report-multiturn`；上游：`origin/sp2DeepResearch`；本次提示/交接提交的父提交：`7f08988`，当时领先上游缓存 55 个提交；接手时以实际 `git log` 与远程状态为准。
- 本次交接提交包含最新提示调整：`README.md`、`docs/research-report/MULTITURN_IMPLEMENTATION.md`、三份 `skills/report-multiturn/{report-dialogue-routing,report-local-edit,report-node-revision}/SKILL.md`。新目录 `prompt-precision-20261125/` 是测试准备材料，本交接文件一并纳入提交。
- 从远程 `feature/report-multiturn` 接手，并确认包含上述提示/交接提交。如在同一台服务器接手，先检查实际工作区状态；多人同时开发应使用独立工作区并划分文件，避免共同覆盖。
- 旧目录 `/data/sp/yzb/StackPlanner2/StackPlanner2.0_Report` 保持只读；其 `zx_sp2research` / `f843873` 与原四处 tracked 修改不能动。不要 stash/reset/clean、切分支或复制整个旧目录与 `.env`。
- 本轮没有推送、合并、部署或重启服务。

## 必读与实现入口

先读根 `AGENTS.md`，涉及后端读 `backend/AGENTS.md`，涉及界面读 `frontend/AGENTS.md`；再读 `MDPLAN.md` 的最新 Writer-first 调整，以及 `MULTITURN_IMPLEMENTATION.md`、`WRITER_FIRST_20261123.md`。

| 入口 | 用途 |
|---|---|
| `backend/packages/harness/deerflow/sp/report/turns/` | 快照、fork/resume、版本读取、发布适配 |
| `turns/local_edit.py` | Writer 定位、读取、精确替换及原生交付接点 |
| `skills/report-multiturn/` | 仅在已绑定且有已接受报告的后续轮加载的提示 |
| `backend/tests/test_sp_report_writer_first.py` | 局部改稿及版本/范围回归 |
| `backend/tests/test_sp_report_turns_graph.py` | 原生中枢图、首轮开关不引入额外步骤 |
| `scripts/report_pilot_{entry,worker,audit,admission}.py` | 实验入口与费用观察，不是第二个中枢 |
| `docs/research-report/fresh-e2e-20261125/CONTINUATION_RESULT.md` | 从零生成后续研究、结构增删与只读的真实结果及失败记录 |
| `docs/research-report/prompt-precision-20261125/` | 当前四轮测试请求、计划和离线结果 |

## 已完成与尚未验证

- 已有简单改稿直接 Central → Writer 的实现；新证据和章节树变化按需走 Outline/Researcher，GitAgent 可选。
- 已执行从空会话生成报告，再逐步扩写、新检索、增加独立章节、再检索核对、删除章节、只读问答。是分阶段修复后完成的链条，不能称冻结代码一遍全部通过。旧失败必须保留。
- 当前 UI 已做报告文件明细折叠、交付卡去重与本轮 Writer 进度过滤；不要把前端折叠说成底层文件 diff 已修正。
- 最新仅改三份多轮提示，强调一段、三条、删除及重构的修改粒度，未增删程序护栏，未改默认首轮提示、工具或预算。
- 该修改后 57 项离线回归通过，实际导入模块路径是新 checkout。离线不代表真实模型改稿精度通过。
- 当前四轮真实测试**尚未执行**：自动审批在启动命令执行前拒绝，要求明确授权将这份测试报告相关正文/材料发送至 DeepSeek 官方 API；本批请求 0 次，费用 0 元。不得换脚本/API 绕过拒绝。
- 本批离线导入/独立恢复前两版通过，原会话 6 个已接受版本的 SHA 保持，详见 `prompt-precision-20261125/OFFLINE.json`。
- 已有来源复用和新检索路径证据；没有充分证明“真实新证据推翻报告原有错误判断”的完整能力。引用事实准确率按用户要求暂缓，不改变现有引用验收。

## 建议师妹接的工作（按顺序）

1. **把当前四轮测试整理成可复跑、可审阅的独立实验入口。** 以 `prompt-precision-20261125/REQUESTS.json` 第 3–6 轮为准，前两版导入、无需重跑首轮。复用现有 worker/原生图，加入执行后的范围/段落/条数评估与角色、token、耗时导出。评估预期不能注入 Agent，也不能变成线上护栏。当前 `/tmp/sp2_precision_run.py` 是临时待实测脚本，不作为稳定接口；旧 pilot 的 `inspect_turn` 对第 5/6 轮内置只读/ExactReuse 预期，不能直接用于本批四次改稿。
2. **验证并精简多轮提示。** 先固定请求再测试，保留失败；重点判断“补一段却扩成多段”“三条被追加而不是替换”“删除波及其他修改”。优先调整条件提示，不改首轮，不增加审查 Agent、字数硬门槛或重试循环。不以原生交付成功替代范围/语义通过。
3. **补足页面结果摘要与人工验收体验。** 如继续 UI，默认简洁显示修改位置、最终报告、耗时；复杂材料/快照路径仍可展开查询。验证刷新恢复、首轮与普通非报告任务，避免已折叠明细再次铺满聊天。不要改旧 Demo 或自行更新运行中的测试实例。
4. **随后补真正的证据冲突案例。** 当前先做用例设计和离线准备；如做真实联网调用，需保持独立会话、来源/费用记录和明确授权。引用准确率全面修复不是当前优先项。

建议先让师妹独立负责第 1 项，完成后审查实验入口与报告，再扩大到第 2/3 项；不必等所有质量问题解决才交接。

## 测试与费用边界

在新 checkout 执行，借用旧虚拟环境只用它的解释器与依赖：

```bash
cd /data/sp/yzb/StackPlanner2/StackPlanner2.0_Report_multiturn
export PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1
export LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false
export PYTHONPATH="$PWD/backend/packages/harness:$PWD/backend:$PWD/scripts"
REPORT_PYTHON=/data/sp/yzb/StackPlanner2/StackPlanner2.0_Report/backend/.venv/bin/python
"$REPORT_PYTHON" -c 'import deerflow.sp.report.turns.adapter as a; print(a.__file__)'
"$REPORT_PYTHON" -m pytest -p no:cacheprovider -q \
  backend/tests/test_sp_report_writer_first.py \
  backend/tests/test_sp_report_turns.py \
  backend/tests/test_sp_report_turns_graph.py \
  backend/tests/test_sp_report_turns_executor.py
```

这组执行器使用 fake model，无需真实凭据。受限沙箱曾卡在 asyncio 事件循环，宿主运行同组 57 项在 8.17 秒通过；不要为环境卡点改执行器业务逻辑。

既有用户批准累计 100 元额度，但不等于任意出站操作获准。最后记录的累计已知费用保守上估为 45.47842112 元，另保留未知费用 6.493064 元；非供应商账单。开始新实验前重读最新 usage 和数值日志；多个独立实验 worker 不能各用同一旧余额快照并发花费。不要清除预留或重放未知请求。

不打印/提交密钥，不复制完整旧 `.env`；交接本文件不包含密码。付费请求、推送、合并与部署的授权分别处理。当前不得自动发布。
