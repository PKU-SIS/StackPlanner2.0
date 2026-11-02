# 报告 Writer 软警告收尾修复

备份 `d489e50`，未 push。只为报告 Writer 配置软警告：停止继续探索，根据已有证据调用 write_file 保存指定节点，再返回完成声明。普通 Agent 保持原提示；循环判定、调用计数、硬停止和时间/token/工具预算均不增加，没有新增重试。

验收没有改：`_accept_writer`、`_accept_writer_locked`、`_writer_repeats_node_title` 与备份版本逐函数源码相同，证明见 `logs/writer-soft-warning-nodes-20261102/acceptance-unchanged.json`。新增回归确认有效指定文件即使不声明 created_paths 或声明空数组仍正常通过；既有缺文件、空文件、错误路径及技术中断测试继续通过。软警告本身不设置 loop_capped；硬停止仍保持原行为。

| 节点 | 委派次数 | 警告后工具调用 | 正文字符 | 原生验收 |
|---|---:|---|---:|---|
| 缓蚀剂 n8 | 1 | write_file ×1 | 1602 | 通过，SHA 一致 |
| 脑缺血 n37 | 1 | write_file ×1 | 2072 | 通过，SHA 一致 |

真实 API 节点测试复用失败时的原始已接受树版本（1、2）与已读取的材料，排除旧的错误完成声明和后续修复草稿。在隔离副本的原生模型请求边界注入一次软警告，让现有中间件生成新提示；配置、写文件工具和 Handler 文件验收走原生路径。每节点一次 Writer 委派、两次模型请求（写入与完成确认），无新读取、无补派、无预算中断。合计 67433 tokens。该测试验证警告边界续写，不等同重新检索或完整报告回归。

离线回归：修改前 7641 passed；修改后 **7647 passed、22 skipped**。相关 214 项通过，覆盖默认/报告提示、硬上限、配置接线、写作验收和并发流水线；Ruff 与 diff 检查通过。两个实验进程均已退出。

产物：`logs/writer-soft-warning-nodes-20261102/{11,25}/node-draft.md`、`metrics.json`、`handler.json`；原真实失败记录保留。提示修正降低了本次触发点的风险，不代表 Writer 在所有输入下都不会漏写；文件门禁和原失败恢复继续保留。

后续两条原始 query 的完整回归通过，Writer 窗口提至每报告 4，47 个写作任务全部首次完成；观察限制和残留工具错误见 [完整回归记录](WRITER_C4_FULL_REGRESSION_20261102.md)。
