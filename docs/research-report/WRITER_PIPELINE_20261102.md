# 账本驱动的并发写作流水线

备份提交：`a5bdfd9`（修改前代码已提交，另建立本次检查点），未 push。

## 主流程

```text
Outline 提交大纲和写作任务
  → 程序按账本顺序领取，逐个提交到并发窗口
  → Native Handler 登记 in_flight 后启动 Writer
  → 草稿落盘并被原有接收逻辑接受，记录 completed
  → 哪个先返回，哪个槽位先补发下一节点
  → 全部 writable 节点有当前有效草稿
  → 原有装配、整合审查、交付
```

写作任务三主状态仍是 `pending / in_flight / completed`；技术失败保留 `technical_failed`，不算写完。Outline 节点的 `ready` 仍表示写作条件就绪，不改成执行状态。并发上限沿用 `max_concurrent_subagents`。

Central 不再逐轮选择或批量派 Writer；程序通过原生 DelegateHandler、现有工具和接收规则执行。显式依赖及 write_last 节点延后；同一节点的冲突任务不并发派发。失败请求同一轮不反复重试，取消后不再补发，已完成草稿保留。队列空但缺节点时，原有覆盖检查仍阻止验收，由 Outline 补齐规划。

## 思考兼容

保留 DeepSeek 思考，使用仓库已有专用适配器，显式配置 enabled/disabled。原配置用普通 ChatOpenAI，已改成本地正确适配器；原 YAML 单独备份于 `logs/writer-pipeline-config-backup-20261102/config.yaml`。

真实模型返回的 `reasoning_content` 原样保留并回传。旧断点已经丢失的字段不能恢复：仅在发送带工具的 thinking 请求时，将相应历史输出／工具结果标记为观察数据，不伪造思考内容，不改原检查点。宿主生成的验收动作也作相同处理。

## 验证

离线测试覆盖滚动补位、并发上限、先登记再执行、依赖等待、同节点去重、失败不误报完成、同轮不重复重试、取消停止补位、用户 ContextVars 继承和原生验收衔接。

原生 `make_sp_agent` 离线图测试证明：从已接受大纲开始，程序写完全部节点、进入审查并交付，Central 没有发生额外写作决策调用。真实 SDK 类型的响应解析和下一轮请求序列化测试证明思考字段保留。

完整后端 **7637 passed、22 skipped**。本轮未调用真实 API；下一轮从三个失败断点恢复，验证实际模型与前端效果，原始失败记录保留。

三个真实失败断点的消息已做离线请求构造核对：无缺失思考字段的 assistant 请求、无悬空工具结果，原消息身份保持不变；API 调用为 0。[检查记录](../../logs/writer-pipeline-implementation-20261102/legacy-request-audit.json)。
