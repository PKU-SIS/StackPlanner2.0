# 哈希读取与 SP 致命状态修复

备份 `b7506fc`，未 push。本次仅按要求修复哈希读取及 error_fatal 状态映射；自动整合的技术失败重试策略未修改，`sp/report/acceptance.py` 与备份逐字节相同。

- 报告模块统一使用 `store.read_text`，直接解码原始 UTF-8 字节，不转换 CRLF/CR/LF。材料、草稿、summary、大纲、候选和终稿的内容身份/回读校验使用同一约定；不重写材料或历史哈希。真实修改，包括把 CRLF 改成 LF，仍会被拒收。
- Gateway 读取本轮最后的根图 SP 结果；非 values 流也可从最终根检查点取状态。error_fatal 记录为 RunStatus.error，保留原错误并发送 SSE error。可恢复结果、正常等待用户、历史轮次错误及子图错误不被扩大为本轮致命失败。取消优先级保持原逻辑。旧数据库状态不追溯改写。

验证：修改前 7656 passed；修改后 **7668 passed、22 skipped**。相关 124 项通过，覆盖 CRLF/mixed-newline、真实篡改、拼接链路、本轮/旧轮次、根图/子图、检查点及 Gateway 生命周期；Ruff 和 diff 检查通过。

原失败现场离线复测：m158（1 处 CRLF）、m251（313 处 CRLF）均按原哈希通过；复用原 20 份草稿，在独立账本副本成功拼接 63068 字符候选，原文件及账本未修改。原致命检查点正确识别为 `SP action budget exhausted: 60/60`。没有模型/API 调用，也未进行审查或宣称已交付最终报告。

证据：`logs/hash-status-fix-20261103/audit.json`；候选：`logs/hash-status-fix-20261103/cloned-outputs/report/r1/assembly/c1.md`。服务保持关闭，没有启动新 case。
