# DeepSeek 批量检索与 20 次工具阈值：实现及实测

实现前备份：`8e1cbdf`，未 push。当前版本已实现并运行真实 A2A/MCP case；**检索和研究综述改善，但本轮没有交付终稿**。

## 已实现

- DeepSeek Search 默认 `count=10, need_content=true`；报告工具在返回前保存有效正文、登记 source/material ID。Fetch 用于正文缺失或确需进一步核对的页面。
- 普通工具名与材料账本沿用现有协议。普通角色不启用报告材料保存封装；提供方通过配置显式选择。
- 仅报告 Researcher/Outline 的 `max_turns` 下限提高到 150，使现有保守公式达到 20 次工具阈值；普通角色、Writer、token/时间保护保持原设置。
- 工具返回去除重复的大段正文/摘要，正常 10 结果的展示控制在约 10k 字符以内，低于默认 12k 外置阈值；所有来源身份和完整原文仍保存。
- 需要外部证据且无充分用户材料时，报告提示词明确要求先预检索。

核心代码：[DeepSeek 适配器](../../backend/packages/harness/deerflow/community/deepseek_tools/tools.py)、[批量材料保存](../../backend/packages/harness/deerflow/sp/report/search_materials.py)、[报告角色装配](../../backend/packages/harness/deerflow/sp/report/hooks.py)。主运行配置未自动切换；真实实验使用隔离的 DeepSeek Search/Fetch 配置。

## 实测结果

实验：`live-report-deepseek-20261101T090404Z`，同一原始请求，模型 `deepseek-v4-flash`。

| 指标 | 原导出 case | 上一轮实测 | 本轮 |
| --- | ---: | ---: | ---: |
| 预检索保存材料 | 1 | 4 | 60 |
| 全程保存材料 | 4 | 7 | 83 |
| 预检索综述 | 正常 | 被硬截断替换 | 正常，约 3.7 万字符，引用 19 个来源 |
| 补充研究综述 | 有一项 token_capped | 被硬截断替换 | 正常，约 1.5 万字符，引用 21 个来源 |
| 已接受正文节点 | 9 | 12 | 17/18 |
| 终稿引用来源 | 4 | 7 | 未交付；17 份已接受草稿引用 14 个来源 |

本轮预检索：6 次 Search、6 次回读、0 次 Fetch。补充研究：2 次 Search、5 次 Fetch、10 次回读、1 次 grep。报告 Researcher 和已完成的 Outline 均未触发 `loop_capped`；记忆召回仍触发其既有保护，不属于本次预算修改。

## 未交付原因

1. 最后一个版本/治理节点 n25 添加了一个日期冲突的细节要求，Writer 写出正文后声明缺少其中一项分配证据，返回 partial 却没有 node_feedback，被拒绝接受。
2. Outline 修订首次重复提交已关闭的 g1，被拒绝。
3. 再次修订改变了其他已写节点的规格/引用上下文，却没有提供必要的 draft_dispositions，再次被拒绝。
4. 实验到 20 分钟上限后停止，保留 17 份已接受草稿。**这次收尾失败是内容要求和修订契约问题，不是 20 次工具阈值，也不是 API 余额。**

研究阶段已验证；最终引用增量和完整交付不能算通过。60/83 的采集量也超出原定参考范围，后续应控制过度采集，不能只追求来源数量。

## 测试与实验记录

- 报告、适配器、材料保存及普通子代理定向回归：372 项通过；另验证了长摘要下仍保留所有原文/身份并缩减展示。
- 完整离线套件：7,533 passed、22 skipped、1 deselected。排除项 `test_task_tool_rejects_bash_subagent_when_host_bash_disabled` 在未修改的 `8e1cbdf` 上同样失败：预期“Bash 已禁用”，实际返回“未知子代理”。未修改该基线问题。
- 保留两次中止试验：`085339Z` 模型跳过预检索；`085555Z` 批量采集成功但重复展示约 90–100k 字符导致外置回读和综述截断。修正后进行本轮；没有把中止试验算作成功。

本地产物：[预检索综述](../../logs/live-report-deepseek-20261101T090404Z/pre-research-summary.md)、[研究及草稿指标](../../logs/live-report-deepseek-20261101T090404Z/evaluation.json)、[停止结果](../../logs/live-report-deepseek-20261101T090404Z/result.json)。它们位于 gitignored 的 logs，尚未作为本轮归档提交。
