# 七轮连续离线演练

在独立 `feature/report-multiturn` 上，从本地冻结提交 `7eb7125` 继续。
使用真实 `make_sp_agent` 中枢图、`SubagentExecutor`、ToolNode、原生报告工具、Writer pipeline、
装配/审查/Finish 和文件快照。模型回答为脚本，检索返回为内存中的合成资料；socket 连接被测试驱动阻断。
原生一万档合同、证据/引用身份、哈希、篇幅和交付门槛没有放宽。

本次演练选冻结材料中的 `library-rag` 一题，保留完整历史，连续执行七轮。
另在独立线程存储中运行同一七轮，注入第 3 轮取消、同来源重复请求和新来源显式重试。
第二道校园题及任何真实模型 API 实验未执行。

## 逐轮结果

| 用户轮 | 本轮操作 | 实际基线（用户轮） | 新原生交付 | head / active（用户轮） | 全文估算 |
|---|---|---|---|---|---:|
| 1 | 原生首轮：预研、Outline、固定补研、整合、4 个 Writer、审查、Finish | — | 是 | 1 / 1 | 9800 |
| 2 | 仅运维章增加更新频率比较 | 1 | 是 | 2 / 2 | 9778 |
| 3 | 保留轮 2，改权限章增加表格 | 2 | 是 | 3 / 3 | 9764 |
| 4 | 选第一版，撤销频率比较，保留权限表 | 1 | 是 | 4 / 4 | 9786 |
| 5 | 当前版只读来源问答 | 4 | 否；独立归档 | 4 / 4 | — |
| 6 | ExactReuse 第二版，撤销后续变化 | 2 | 否；独立归档 | 4 / 2 | — |
| 7 | 从第二版改运维风险说明，权限表不复活 | 2 | 是 | 7 / 7 | 9801 |

表中为用户轮次标签，不能当成 native report ID、tree_version、draft_id 或 final version。
[机器记录](MULTITURN_REHEARSAL_20261122.json)分列实际身份、每章 draft ID/SHA、审查动作、delivery run 和恢复目录。
字数是原生 `mixed-word-estimate-v1` 估算，不是 Microsoft Word 实测。

两个场景均满足：

- 每个生成轮有当前 run 的新 review/final/delivery，五个审查动作 ID 不重复。
- 所有已接受旧版的全量文件 SHA 保持；修改轮只有选中章节的 canonical SHA 改变。
- 第 4 轮从旧工作基线恢复时，仍保留第 3 轮有效要求；第 7 轮使用 ExactReuse 后的 active 第二版。
- 第 5、6 轮只有 perception 的 GitAgent profile，没有 Researcher、Writer 或 Outline。
- 七轮各自的档案都复制到不同的恢复目录，在独立 store 中执行真实恢复和原生 final 绑定校验。
- 原生合同仍为 9000–11000，五个生成轮都在范围内。
- 取消的候选和诊断保留，head 不推进；同一来源重复请求不 fork、不派 Writer；新来源重试完成后继续后续轮次。

正常场景实际有 8 次 Writer 委派：首轮 4 次，四个修改轮各 1 次；2 次 Researcher 均在首轮。
取消场景多一次 Writer 委派尝试，但父级取消在模型启动前终止，因此实际 Writer 脚本请求数仍与正常场景相同。
角色委派、模型请求和真实 provider 调用分别记录。真实 provider 调用为 0；脚本没有提供 provider token usage，记录为 null，不伪造零 token 成本。

## 连续运行发现并修复的缺陷

第二轮副本保留了第一轮账本/会话中的固定验收动作身份，例如 `sp-fixed-c0-review-r1`。
重置验收状态后仍产生相同 ID，会触发旧动作幂等重放，本轮的新审查没有发生。
此前分段测试使用不同的手工审查 action ID，没有覆盖这个连续状态。

修复仅在宿主激活显式多轮副本时，向原生 `acceptance` 状态加入程序分配的
`turn_namespace=<native report id>-<dialogue turn id>`；固定审查、Finish、修订及问询动作使用该前缀。
原生新验收周期保留该 namespace。首轮没有该字段，继续产生原始动作 ID；实际演练核对了
`spact_sp-fixed-c0-review-r1`。

没有改变动作枚举、审查条件、重试额度、模型预算或第一轮提示/工具。
演练同时加强成功判定：本轮 `native_delivered` 从实际 delivery 的 run_id 推导，再与独立验收预期比较，
回退到旧 head 的 delivered 记录不能冒充本轮成功。

## 可复验命令与结果

最终广域回归：**1056 passed in 50.98s**，含新增的两条连续轨迹和 namespace 周期兼容测试。
[原始输出](MULTITURN_REHEARSAL_TEST_RUN_20261122.txt)。

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false \
DEER_FLOW_HOME=/tmp/sp2-rehearsal-freeze-runtime \
SP_MULTITURN_REHEARSAL_DIR=/tmp/a-new-rehearsal-directory \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$PWD/backend/packages/harness:$PWD/backend" \
timeout 150 \
/data/sp/yzb/StackPlanner2/StackPlanner2.0_Report/backend/.venv/bin/python \
  -m pytest -p no:cacheprovider backend/tests/test_sp_report_multiturn_rehearsal.py -q
```

指定的产物目录必须是新的；已有尝试不会自动清理或覆盖。
省略 `SP_MULTITURN_REHEARSAL_DIR` 时使用 pytest 独立临时目录。实际导入必须来自新 checkout。
首轮图、显式入口、旧验收、普通非报告和工具纠正等广域回归也通过。

完整最终产物位于 `/tmp/sp2-multiturn-rehearsal-freeze-20261122/`。
每个场景有逐轮 `state.json`、`execution.json`、`outcome.json`、请求轨迹、工具事件、原生报告账本、
独立轮次快照及七份恢复副本。所有调试尝试和成功产物另归档于：

[本地完整归档](../../.deer-flow/offline-rehearsals/20261122-full-dialogue/artifacts.tar.gz)。

归档 SHA 记录在机器 JSON。该大文件使用既有 `.deer-flow/` 忽略规则留在本地，不进入 Git 历史。
前两次调试修正了测试驱动的构造参数和报告工具名；第三次暴露上述真实动作重放缺陷。
后续成功尝试与最终回归均保留，没有用成功结果覆盖失败资料。

## 结论范围

这验证了连续协议、隔离、原生交付绑定、局部保留及取消/重复/重试的程序行为。
章节是预先编排的合成文字，事实质量和独立自然语言需求解析未评估。
验收 JSON 在轮次结束后读取，未放入模型输入；冻结请求和验收文件的 SHA 没有修改。
没有运行 RACE/FACT，没有调用模型 API，没有推送、合并、部署或重启旧 Demo。
