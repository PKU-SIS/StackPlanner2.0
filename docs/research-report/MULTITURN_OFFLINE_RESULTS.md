# 多轮报告：第一阶段离线实现与回归结果

日期：2026-11-22。所有代码来自独立目录
`/data/sp/yzb/StackPlanner2/StackPlanner2.0_Report_multiturn`。
分支 `feature/report-multiturn`，上游 `origin/sp2DeepResearch`，初始 checkout HEAD
`f94e06612d363b6a46f3d4bf59b342f77b786729`；这是首次实现时点的基线记录；后续离线审查、本地冻结及最新结果见 [审查记录](MULTITURN_REVIEW_20261122.md)。
精确身份、源材料 SHA、实际模块导入路径见 [工作区记录](MULTITURN_WORKSPACE_20261122.json)。

## 最终结果

| 检查 | 结果 | 说明 |
|---|---|---|
| 修改前 P3 / 显式入口基线 | 30 passed | 新 checkout、原生路径 |
| 最终 SP / 报告 / invalid-tool repair 广域回归 | **1033 passed in 35.22s** | 包含普通非报告、首轮、全部报告阶段与新增多轮测试 |
| 新增多轮测试 | 22 项包含在上列总数中 | 不是额外相加的 22 次真实模型实验 |
| 历史一万档产物只读验证 | 通过 | 230 个源文件 SHA 不变；215 个 canonical 文件在副本中逐字节保持 |
| 恢复、原生 resume、合同继承、取消 | 通过 | 恢复后的原生 final 绑定有效；副本 resume 后旧 final/delivery 不作为新交付；取消不推进 head |
| Ruff 0.14.11 lint / format | 通过 | 本次变更的 Python 文件 |
| 新增四个 Skill 的 quick_validate | 4/4 通过 | 位于独立目录，不进默认 public Skill 发现 |
| `git diff --check` | 通过 | 未推送、合并或部署 |

测试原始输出：[MULTITURN_TEST_RUN_20261122.txt](MULTITURN_TEST_RUN_20261122.txt)。
报告实现与已知边界：[MULTITURN_IMPLEMENTATION.md](MULTITURN_IMPLEMENTATION.md)。

## 可复验命令

在新 checkout 根目录执行。旧解释器只提供依赖，不能省掉 PYTHONPATH。
`PYTHONDONTWRITEBYTECODE` 防止向借用环境/源码写入 pycache，`DEER_FLOW_HOME` 隔离测试运行数据。
本机受限进程沙箱内的真实执行器测试会停滞；本次在获准的隔离测试进程中运行，使用 fake LLM，并关闭外部追踪。

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false \
DEER_FLOW_HOME=/tmp/sp2-multiturn-final-regression-v4 \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$PWD/backend/packages/harness:$PWD/backend" \
timeout 120 \
/data/sp/yzb/StackPlanner2/StackPlanner2.0_Report/backend/.venv/bin/python \
  -m pytest -p no:cacheprovider \
  backend/tests/test_sp_*.py backend/tests/test_report*.py \
  backend/tests/test_invalid_tool_call_repair_middleware.py -q
```

实际 `hooks`、`turn_store`、`runtime` 的 `__file__` 均位于新 checkout。没有复制旧 `.env`、
`config.yaml`、用户补丁或旧目录全部文件；只有按要求复制的 MDPLAN，以及只读输入到 `/tmp` 的历史报告快照。

历史产物复验命令（work-dir 必须不存在；再次执行请选新名字）：

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$PWD/backend/packages/harness:$PWD/backend" \
/data/sp/yzb/StackPlanner2/StackPlanner2.0_Report/backend/.venv/bin/python \
  scripts/validate_report_multiturn_snapshot.py \
  --source-outputs /data/sp/yzb/StackPlanner2/StackPlanner2.0_Report/evaluation_runs/report_stability_10k_20261121/075632Z/repeat-17-a/17/runtime/users/full-eval/threads/full-stability17a/user-data/outputs \
  --work-dir /tmp/sp2-multiturn-historical-verified-20261122
```

完整结果保存在该独立 work-dir 的 `validation.json`，也收录于工作区记录。这个命令只复制、验证、
恢复、resume 和取消，不检索、不写新报告、不调用任何模型。原稳定性批次没有重跑或覆盖。

## 实现期间发现并修正的问题

- GitAgent 的 8 步图上限会在两次正常工具调用中触发 GraphRecursionError。现保留 8 次工具调用上限，
  图内部步骤使用现有配置、最多 50；真实 SubagentExecutor + ToolNode 的 fake LLM 往返已通过。
- 路径保护的条件曾让 Researcher 配置多读一次不需要的 ledger，广域回归中 1 项失败。现只对
  Outline/Writer 读取；未改原测试，最后完整回归通过。
- 第二版带有 fork 来源，不能据此允许下一轮原地 resume。现在还要求来源消息对应的 prepared turn
  及程序分配的副本 ID 匹配，第三轮旧版本仍受保护。
- 发布索引故障注入验证了“native delivered 已存在但 accepted head 尚未发布”的区分。
  同一 Finish 恢复索引，不重复 Writer，也不把准备/执行完成误当交付。
- 旧测试默认写入 checkout 的运行目录，以及受限沙箱中的异步停滞，通过独立 DEER_FLOW_HOME 和
  有时间上限的隔离进程解决，没有放宽证据、长度、哈希或验收条件。

## 差异范围

新增 `sp/report/turns/` 存储、适配器和受限工具；新增四个条件加载 Skill、两份回归文件及一个离线验证脚本。
原生接点集中在 report hooks/context/entry、委派后的上下文收紧、Finish 归档及其可用性，
结束清理复用已有 TaskMemory 生命周期。原生章节 ledger、Outline/Writer 协议及验收继续复用。
没有改 Central 基础提示、动作枚举、模型工厂、默认 Skill、篇幅合同或全局预算。

旧目录仍为 `zx_sp2research` / `f843873`，跟踪改动仍是原四处。独立整合目录只读核对为
`integrate/sp2-report-threebands-20261121` / `f94e066`。没有操作旧 Demo、重启服务或改变其他远程分支。

离线通过证明程序隔离、协议与固定输入轨迹。它不证明任意自然语言撤销的语义正确性、事实正确性、
最优历史基线选择或真实模型成本收益。付费多轮实验、RACE/FACT、UI、主线合并和部署均未执行。
