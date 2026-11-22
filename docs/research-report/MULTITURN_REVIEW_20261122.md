# 多轮报告离线审查与冻结

本次在 `feature/report-multiturn` 独立 checkout 继续审查，基线仍为
`f94e06612d363b6a46f3d4bf59b342f77b786729`。未运行模型 API、未操作旧 Demo、未推送/合并/部署。

## 审查发现与修复

| 问题 | 修复和验证 |
|---|---|
| 后续消息漏带 opt-in 时，旧原生 resume/close 能改已封存版本 | 对已登记封存的 native report 加持久保护；不依赖后续开关。没有版本索引的原生首轮路径继续保留。resume/close 负向测试验证旧全量 SHA 不变。 |
| 激活在 native resume 后失败，turn 卡在 prepared | 先恢复原指针，记录 failed/activation 诊断；下一来源消息可准备新副本。 |
| 快照 rename 已成功、manifest 写入失败后不能恢复 | 只在完整副本逐文件匹配源时补齐 manifest；继续拒绝损坏副本。恢复不重复建轮次。 |
| 首轮交付前的 HITL 修正不在初始 request 中 | 懒登记保存已接受 requirements、输入来源、applied decisions、长度合同。GitAgent 明确把初始 request 当来源，不能当成已修正后的完整需求。后续版本继续以完整 resolved_query 为权威。 |
| 原始 live 目录移除后，restore 会重用已归档 native report ID | 分配 ID 同时检查磁盘和版本索引/轮次保留身份。 |
| 直接存储调用可在修订进行中发起 read-only/ExactReuse | prepare/reuse 使用相同的轮次互斥门。 |
| 绑定报告仍可能派不受版本写路径限制的 Coder | 绑定报告仅允许 GitAgent 和原生报告角色；普通未绑定任务继续原路径。 |
| 指针漂移后，native delegation 可退回通用角色路径 | 派发前核对 turn 的工作副本必须是当前 active native report。 |
| 原生 close 结束副本但未释放 dialogue turn | 在 ledger 锁之外完成 sidecar cancellation，恢复已接受指针，下一轮可继续。 |

先添加失败测试重现，再实施修复。没有修改证据、长度、哈希或原生交付门槛。

## 首轮和中枢路径证据

`test_sp_report_turns_graph.py` 使用真实 `make_sp_agent` 图和 fake model：

- 开关关闭 vs 开关开启但无基线：首次 Central 请求的完整提示文本、工具集合一致，均只有一次探针调用。
  仅固定与功能无关的随机 task-memory UUID；未归一化或删除需求/提示文本。
- read-only 和 ExactReuse：Central 自己选择 perception GitAgent；完成结构化 handoff 后再 Finish。
  两次 Central fake 请求、一次 perception 委派，没有 Researcher/Writer。
- 夹具使用原生首轮 DynamicContext 处理后的消息形态和原生 `sp_finish(summary=...)` 接口，
  不把未经历首轮中间件的人工 seed 当成真实持久化历史。

另外核对 `config.example.yaml`、runtime/model budget 装配、默认报告 prompts、动作 schema、
subagent 默认配置及三个默认报告 Skill 与基线逐字节一致。局部接点不新增首轮 LLM 路由或生命周期图节点。
这不是随机真实生成逐字同文的承诺。

## 离线结果

最终完整命令包含所有 `test_sp_*.py`、`test_report*.py` 及 invalid-tool repair：
**1053 passed in 36.84s**。其中本次增加 20 项审查/中枢图/冻结材料测试，累计多轮相关新增 42 项，均已包含在 1053 中。
Ruff 0.14.11 lint/format、Skill schema 和 diff 检查在本地冻结前执行。
前一阶段的 1033 项输出保留在原文件，未覆盖历史结果。

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false \
DEER_FLOW_HOME=/tmp/sp2-multiturn-reviewed-regression \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$PWD/backend/packages/harness:$PWD/backend" \
timeout 120 \
/data/sp/yzb/StackPlanner2/StackPlanner2.0_Report/backend/.venv/bin/python \
  -m pytest -p no:cacheprovider \
  backend/tests/test_sp_*.py backend/tests/test_report*.py \
  backend/tests/test_invalid_tool_call_repair_middleware.py -q
```

旧目录的四个原有跟踪改动 SHA 与上一阶段记录一致，HEAD 仍为 `f843873`；仅只读核对。
所有实际模块导入与测试均指向新 checkout。历史产物只做离线恢复/哈希复验，不重跑原生成批次。

## 冻结的验收材料

[验收协议](multiturn-evaluation-20261122/PLAN.md)和两题各七轮的 JSON 已冻结。
模型可见请求与验收预期分文件，读取工具有字段白名单；防泄漏、未来/未接受版本引用、
冻结后静默改动、幂等和 API 禁用标记都有离线测试。

```bash
python scripts/validate_report_multiturn_cases.py
```

当前没有 API 运行器，manifest 不授权付费实验。后续需确认配置、预算、语义预期和停止条件，
再单独安排一万档小样本。FreshSolve 内部模式、UI、并发在途修改等限制见实现说明，未以本轮冻结掩盖。
