# 批量补研 → Outline 收尾：3 条 smoke

**1/3 完整通过；2 条补研交付不合格，未运行其下游 Outline。**三条均未触发预算中断，没有工具错误。此次只增加阶段测试入口，未修改生产逻辑。

| 样本 | 补研结果 | Outline 收尾 |
| --- | --- | --- |
| 11 缓蚀剂 | 正常综述，26 个规范引用来源，新增 49 篇材料 | 一次通过，27 个正文节点均有写作任务，采用 59 个来源，3 个 gap 全部关闭 |
| 25 卒中中性粒细胞 | 正文约 1.6 万字符，新增 94 篇材料；来源写成 `S133/m139`，没有规范的 `[S133]` 标记 | 被评测挡住，未运行 |
| 97 支线机场经济影响 | 新增 83 篇材料；最终 JSON 转义失败，summary 保存成约 2.4 万字符的截断原始 JSON | 被评测挡住，未运行 |

## 两个问题

**25 是引用格式未遵循协议，不是没有检索到材料。**当前 Skill 已要求 `[S<n>]`；生产验收仍接收了这份综述，规范引用检查由本次测试发现。

**97 是旧交付路径的问题。**最终模型输出约 11,719 tokens，低于本轮 32,768 上限；一处正文 `"airport presence"` 未正确转义，外层 JSON 解析失败。Adapter 回退到保留原始输出并截断，补研验收仍保存了它。预检索的直接 Markdown 交付与修复机制没有覆盖补研。

因此，本轮不应归因于工具额度不足。后续优先考虑：让报告补研也直接交付完整 Markdown；gap 执行元数据仍由宿主与实际工具记录维护；保存时检查规范引用和正文格式，原始 JSON／截断恢复文本只能作诊断，不能作合格 summary。先讨论该范围，再改生产代码和复测，不扩大跑 20 条。

## 记录

入口：`backend/scripts/evaluate_report_supplement.py`。克隆已验收的初始树并恢复原 Handler 状态／任务记忆，一次 Researcher 处理全部 3 个 gap；只在综述检查通过后运行原生 Outline revise。上游 422 个文件哈希未变。没有运行 Central、Writer 或交付。

[逐项结果](../../logs/supplement-smoke3-v1/REPORT.md) · [样本 11 综述](../../logs/supplement-smoke3-v1/11/summary.md) · [样本 11 收尾大纲](../../logs/supplement-smoke3-v1/11/outline.md) · [上游完整性检查](../../logs/supplement-smoke3-v1/audit.json)

新增指标及相关原生执行回归：32 passed；Ruff 检查与格式通过。完整后端：7610 passed、22 skipped、5 failed；其中 4 条是真实 DeerFlowClient 测试连接当前默认模型失败（Connection refused），另 1 条是既有 Bash 禁用提示断言。该次全量未设置 CI，包含真实模型测试，不能和此前排除 live 测试的计数直接比较。DeepSeek smoke 的三条 API 调用正常。旧生产改动仍未提交，本轮没有 push。
