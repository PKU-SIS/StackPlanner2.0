# Central 驱动的完整报告流程 smoke

北京时间 12:00 后启动，三并发：31 博物馆、11 缓蚀剂、97 支线机场。

从原始 query 出发，原生 Central 自行调度预检索、大纲、补研、写作与交付，不复用已生成报告，也不由测试程序手动指定阶段。保留隔离目录、事件、SQLite 检查点与结果；检查交付、章节覆盖、引用编号、错误及是否终止。每例总时限 1500 秒。

准备时发现：此前阶段测试显式使用 350 图步／50 工具，但报告 Researcher／Writer 原生配置尚未统一。已在报告专属 hooks 对齐，并保留普通任务默认行为；Researcher 在 40 工具时预留综述收尾，纯格式修复分支不增加预算。相关预检查 56 passed；完整后端 **7624 passed、22 skipped**。

本轮直接测试原生 SP 图，未覆盖 Gateway 生命周期与前端。前端依赖和构建目录已在，前端联调放在本轮后。

产物：`logs/full-central-smoke3-20261102T0400Z/`。启动门槛前不发起模型请求。

## 首轮实际结果

**报告完整链路尚未通过。**博物馆、缓蚀剂、机场明确报告对照均完成预检索、补研与大纲，各保存一个草稿后中断；分别还有 18/19/15 个节点未写。阶段单测通过不能替代 Central 调度验证。

三个样本都出现同一顺序：Central 同批请求 4 个 Writer，原有串行规则只接收首个、拒绝其余 3 个；下一轮 DeepSeek 返回 `reasoning_content` 缺失 HTTP 400。故障发生在终稿渲染之前。

当前配置 `supports_thinking=false`、运行参数 `thinking_enabled=false`，但没有 `when_thinking_disabled` 或显式 `thinking.type=disabled` 请求参数。官方说明 thinking 默认开启，带工具的后续请求需要保留完整 `reasoning_content`；读取失败检查点时仅检查了字段是否存在，确认历史 AI 消息缺该字段，没有读取或输出私有推理内容。[DeepSeek 官方说明](https://api-docs.deepseek.com/guides/thinking_mode/)

原始机场问题只要求找证据，没有要求报告交付，Central 走普通研究答复并结束；这条不按报告链路故障处理。明确追加“完整报告并交付 Markdown”后才进入报告模式，并复现上述兼容错误。原始输入与失败记录均保留，没有改问题的研究范围。

测试图的 LLM 错误兜底以终止消息返回，未抛 Python 异常，因而原 metrics 的 error=null；delivered/overall_pass 均为 false，未判为成功。独立诊断文件记录真实错误。已停止新增重复 API 实验；保留 SQLite 断点、材料、大纲和已完成草稿。

下一步：先显式配置非 thinking 请求，再让 Central 遵守现有 Writer 顺序规则，从断点恢复；是否开放报告 Writer 并发另行验证。尚未修改上述生产规则或开启前端测试。

[首轮结果与诊断](../../logs/full-central-results-20261102/REPORT.md) · [结构化诊断](../../logs/full-central-results-20261102/diagnosis.json)
