# 给师妹的快速开发交接 Prompt

下面整段可以交给你的 coding agent 执行。

---

你接手 StackPlanner 2.0 报告系统的“成稿后多轮对话与局部改稿”。在现有实现上继续开发，不重做报告系统。

## 1. 先确认代码位置

- 仓库：<https://github.com/PKU-SIS/StackPlanner2.0.git>
- 服务器：`123.59.6.244`，SSH 用户 `sp-yzb`，端口 `22`；本机配置过别名时可用 `ssh SPA800YZB`。
- 本次开发目录：`/data/sp/yzb/StackPlanner2/StackPlanner2.0_Report_multiturn`；接手请从下面的 GitHub 开发分支建立自己的 checkout。
- 开发分支：`feature/report-multiturn`；本次交接核对的最新提交：`4f02cf9`。
- 获取代码用 `feature/report-multiturn`，不要使用默认 `main` 或仅拉取报告主线 `sp2DeepResearch`。本文件随交接提交一并推送；最新 HEAD 以远程分支为准。
- 旧目录 `/data/sp/yzb/StackPlanner2/StackPlanner2.0_Report` 只能只读参考，不在那里开发，不 stash/reset/clean、不切分支、不覆盖原改动。

先从 GitHub 获取开发分支，再创建自己的工作分支：

```bash
git clone --branch feature/report-multiturn --single-branch https://github.com/PKU-SIS/StackPlanner2.0.git StackPlanner2.0_multiturn
cd StackPlanner2.0_multiturn
git switch -c feature/report-edit-precision
git log -1 --oneline
git status --short --branch
```

已有 clone 时，先保护自己的未提交修改，再 `git fetch origin feature/report-multiturn`，从 `origin/feature/report-multiturn` 创建独立工作分支或 worktree。不要为每个报告对话轮次建代码分支。
运行时数据、原始实验报告和凭据不会随普通 Git checkout 自动转移；离线单测可以先做，真实实验基线需另外取得独立只读副本。不要复制整个旧目录或 `.env`。

## 2. 当前系统怎么工作

- 首轮报告和普通任务保留原流程。
- 已绑定、已有交付报告的后续轮，由原中枢调度。
- 现有章节的改写：程序建立独立副本，再由 Writer 定位、读取、局部 patch，沿用原生交付。
- 新增/删除独立章节按需调用 Outline；需要新证据时走原生 Outline/Researcher 路径。
- GitAgent 是可选复杂协调路径，不要恢复成每轮必调；不要另加外置 LLM 总路由或评审 Agent。
- 原版和范围外内容应保留，只有真实原生交付成功才发布新版本。

已有从零生成→多轮改稿/新检索/章节增删→只读问答的分阶段真实测试，保留了中间失败，不是一次冻结代码全部成功。
最新 `4f02cf9` **只微调了多轮 prompt**，强调按用户要求控制修改范围；57 项离线回归通过。调整后的四轮真实 API 测试尚未运行，不能声称精度已经提升。

## 3. 你先做的第一项任务

**把“局部改稿精度”四轮测试做成可复现的实验入口和结果报告。先完成这一项，再扩大功能。**

固定输入在 `docs/research-report/prompt-precision-20261125/REQUESTS.json`，人工可读版是同目录 `CASES.md`。

使用已有低代码报告的前两版作为独立测试基线，依次执行文件中的第 3–6 轮，不重新生成首轮：

1. 新增一个以【交接复核建议】开头的自然段，其他文字保留。
2. 将原【迁移演练建议】整块**替换**成三条，保留刚新增的段落。
3. 只删【交接复核建议】这一段，不回退整篇，三条保持。
4. 在那三条内按“准备、执行、交接”重构，不恢复已删除段落。

需要交付：

- 可审阅的运行入口、固定请求、明确的基线及实际导入模块路径。
- 离线验证通过后，再在明确获得数据出站授权时运行真实 API。
- 每轮分别报告：原生交付是否成功、是否满足用户意图、范围外是否保持、原版 SHA 是否保持、实际调用哪些角色，以及耗时/token/费用。
- 段落数、条目数和 diff 检查是**实验后的评估**，不加入生产执行路径，不变成新的护栏。
- 失败保留原记录并停止依赖轮次，不靠手工改报告或自动重跑替换失败成绩。后续修复只能新建试次。

注意：现有 `report_pilot_entry.py` 的 `inspect_turn` 对第 5/6 轮内置了旧题的只读/ExactReuse 预期，不能直接用于本批四轮改稿。复用原生执行与费用观察，评估应适配本批实际请求。`/tmp/sp2_precision_run.py` 只是本地临时待实测脚本，不是稳定入口，也不要绕过此前审批拒绝直接执行它。

## 4. 修改原则

- **优先改条件加载的多轮 prompt，不新增、不删除护栏。** 不增加字数硬门槛、额外分类/审查 Agent、重试循环；不要改共享执行器来掩盖个别失败。
- 加一段保持一段，压成三条要替换而非追加，删除不另补总结；重构只影响指定部分。用最小完整 patch，范围外标题、正文、表格和引用尽量逐字保持。
- 只改 `skills/report-multiturn/` 等明确的后续轮扩展点，不改所有首轮会加载的默认 Skill。
- 保留原生证据、引用、哈希与当前轮显式长度检查；首轮路径不改。
- 引用事实准确率全面修复暂缓，先把增删改范围做准，不降低现有引用验收。
- 不改旧 Demo，不重启服务，不自行合并、部署或改 main/Qwen/DCC/审计分支。

## 5. 必读与入口

先读根 `AGENTS.md` 和涉及模块的指南（后端 `backend/AGENTS.md`；界面 `frontend/AGENTS.md`）。再读：

- `MDPLAN.md`：以最新 Writer-first 调整为准，不照历史固定 GitAgent 流程实现。
- `docs/research-report/MULTITURN_IMPLEMENTATION.md`
- `docs/research-report/DEVELOPMENT_HANDOFF_20261125.md`
- `docs/research-report/fresh-e2e-20261125/CONTINUATION_RESULT.md`
- `docs/research-report/prompt-precision-20261125/{PLAN.md,CASES.md,OFFLINE.json}`

主要代码：`backend/packages/harness/deerflow/sp/report/turns/`，尤其 `local_edit.py`；多轮提示：`skills/report-multiturn/`。
测试优先看 `backend/tests/test_sp_report_writer_first.py`、`test_sp_report_turns*.py`；已有费用观察在 `scripts/report_pilot_*.py`。

借用旧虚拟环境时，必须显式设置新工作区的 `PYTHONPATH`，并检查 `deerflow.sp.report.turns.adapter.__file__`。具体 57 项回归命令见完整交接文档；不要在新目录运行却导入旧代码。

## 6. API 与交付边界

此前用户批准累计 100 元额度；已有费用和未知预留必须继承，不是每个人各有 100 元，也不能多个 worker 各自读同一份旧余额并发花费。

上一轮 API 启动在执行前被自动审批拒绝：要求明确确认将该测试报告相关正文/材料发送至 DeepSeek 官方 API。本批尚无调用与费用；接手不视为解决了该授权问题。先完成离线工作，不绕过拒绝，不在聊天里索取或粘贴密钥。

第一阶段完成后，请提交：实际 diff、小范围提交、离线测试结果、四轮逐项评估、用量与失败记录、仍存在的问题。不要只再写一份大计划就结束，也不要把“有最终报告文件”当成改稿正确。
