# 多轮报告开发交接（2026-11-25）

## 当前可交接程度

可以接手功能开发与回归。基础版本隔离、Writer-first 局部改稿、条件 Outline/Researcher 调度已有实现和真实试验记录；当前不是“所有复杂需求已稳定通过”的发布状态。不要从零重做，也不要通过新增拒绝门槛来改善表面成绩。

## 代码获取与开发位置

- 代码来源：<https://github.com/PKU-SIS/StackPlanner2.0/tree/feature/report-multiturn>。
- 获取 `origin/feature/report-multiturn` 最新 HEAD，并在自己的 checkout/worktree 创建工作分支开发。不要在作者现有服务源码目录直接改代码。
- 已推送交接起点为 `44dea49`，包含提示实现 `4f02cf9`；最新 HEAD 以实际远程分支为准，不固定在旧提交。clone/分支命令见 `JUNIOR_HANDOFF_PROMPT_20261126.md`。
- 当前开发分支跟踪 `origin/feature/report-multiturn`；报告主线仍是 `sp2DeepResearch`，两者不能混淆。
- 提示、说明、测试准备材料已入 Git；历史运行快照与凭据不随 clone 自动取得，需要时单独获取经过校验的只读实验基线。
- 仅授权开发分支推送，不合并主线、不部署、不重启服务。

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

## 师妹接手的四项工作（2026-11-26 更新）

最新任务与可直接粘贴给 Codex 的编号 Prompt 见 [JUNIOR_HANDOFF_PROMPT_20261126.md](JUNIOR_HANDOFF_PROMPT_20261126.md)。优先顺序以该任务书为准：

1. **自己运行系统并体验端到端**：从零生成报告，连续改稿、刷新和下载，记录问题，不把导入基线当作从零测试。
2. **优化 UI**：根据真实体验修复刷屏、重复交付卡、过时进度和状态不清楚，提供前后对比与刷新验证。
3. **系统测试多轮局部改稿**：可导入已接受报告，用固定 query 测增删、精简、重构、补引用、撤销、旧版继续、只读与复用；核对正文差异和 SHA，记录角色、耗时/token。
4. **优化复杂任务的效果**：测试按需 Outline / Researcher、新检索补证、章节结构增删和后续继续修改，优先调条件 prompt，不增删护栏、不重构公共流程。

不懂的先请 Codex 结合实际代码解释并带着操作；权限、缺失数据或产品目标等无法自行核实的信息再找负责人。Codex 应完成实际复现、检查、修改与验证，不止给计划，也不能把预期说成实测。

四轮精度实验仍是第 3 项的现成起点；不再要求先做这个单一入口才允许体验系统/UI。引用事实准确率全面修复继续暂缓。

## 测试与费用安排建议

先按仓库安装说明在自己的 checkout 建立 Python 环境，再在该 checkout 执行：

```bash
cd /path/to/your/StackPlanner2.0_multiturn
export PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1
export LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false
export PYTHONPATH="$PWD/backend/packages/harness:$PWD/backend:$PWD/scripts"
REPORT_PYTHON="$PWD/backend/.venv/bin/python"
"$REPORT_PYTHON" -c 'import deerflow.sp.report.turns.adapter as a; print(a.__file__)'
"$REPORT_PYTHON" -m pytest -p no:cacheprovider -q \
  backend/tests/test_sp_report_writer_first.py \
  backend/tests/test_sp_report_turns.py \
  backend/tests/test_sp_report_turns_graph.py \
  backend/tests/test_sp_report_turns_executor.py
```

这组执行器使用 fake model，无需真实凭据。受限沙箱曾卡在 asyncio 事件循环，宿主运行同组 57 项在 8.17 秒通过；不要为环境卡点改执行器业务逻辑。

费用与实验时段作为安排建议，不把此前的 100 元预算讨论写成师妹接手开发的固定额度要求。建议大规模或高并发 API 实验优先安排在 DeepSeek 波谷时段（**北京时间 / UTC+8**）：

- 周六、周日全天，以及中国法定节假日全天。
- 工作日 12:00–14:00。
- 工作日 18:00–次日 09:00。

日常代码开发、离线测试和少量串行 API 调试按需要进行，无需为了等波谷暂停开发。实验仍建议记录实际 token、耗时与费用，方便比较成本。
时段依据 [DeepSeek 官方价格说明](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)（2026-11-26 核对）；大批量启动前建议再核对一次最新价格。

历史费用仅供查档：最后记录的累计已知费用保守上估为 45.47842112 元，另有未知费用预留 6.493064 元，非供应商账单，不作为师妹新开发任务的预算额度。复用旧实验记录时保留其原始数值，不改写历史结果。

不打印/提交密钥，不复制完整旧 `.env`；交接本文件不包含密码。付费请求、推送、合并与部署的授权分别处理。当前不得自动发布。

## 作者服务器目录（仅供查档）

以下不是接手后的代码来源或开发目录，仅供负责人定位 Git 未包含的历史实验产物：

- 服务器 `123.59.6.244`，SSH 用户 `sp-yzb`，端口 `22`，已配置时别名 `SPA800YZB`。
- 原独立开发目录 `/data/sp/yzb/StackPlanner2/StackPlanner2.0_Report_multiturn`。
- 旧运行目录 `/data/sp/yzb/StackPlanner2/StackPlanner2.0_Report`，`zx_sp2research` / `f843873`，原四处 tracked 修改必须保留；只读，不 stash/reset/clean、切分支或覆盖文件。
- 若取得历史报告基线，只读取并复制必要产物到自己的独立实验目录；不要复制完整源码目录或 `.env`，不改变原运行服务。
