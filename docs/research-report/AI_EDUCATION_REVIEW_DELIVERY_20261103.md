# AI 教育旧草稿的审查与交付验证

复用原失败 thread `a0b09622-4d12-4f4d-a1de-4ab351bb720f` 的 20 份草稿，在隔离副本运行原生固定验收 → Outline integrate_review → FinishHandler。没有重新检索、没有 Writer 委派、没有连续重试，也没有启动服务。

**交付链路通过。** 一次审查委派、一次模型调用，约 20 秒，311244 tokens；审查给出 advisory 并通过，原生 Finish 成功。生成 61470 字符终稿、连续编号来源表 50 项，文件 SHA 正确。原账本/材料/草稿未修改，副本中的 20 份草稿按 `(node_id, draft_id)` 核对亦全部未变。

用实际完成 Handler 结果及原致命错误回放真实 run_agent/RunManager：完成为 success；`error_fatal` 为 error，错误原因保留。该项是进程内终态回放，不等同 Gateway/前端完整回归；原运行数据库的旧 success 不追溯改写。

## 本次额外发现

- **转义引用漏识别。**n4 使用 `\\[S11\\]` 等 Markdown 转义引用，现有提取/编号规则没有覆盖。终稿残留 14 处，涉及 S11/S13/S16/S19/S20/S29；其中 S11/S19/S20 未进入最终来源表。因此不能把技术交付成功称为引用完全正确。
- **草稿 ID 重复。**原始并发账本中 d1、d2、d5、d6、d7 各重复三次，d3、d4 各两次。文件路径含 node_id，实际正文没有覆盖丢失，但 `writing.draft` 仅按 draft_id 查找时会含糊；`next_draft_id` 只看已接受 drafts，没有计入在写任务预留编号。这是原现场既有问题，本次未修改。

初始验证脚本误按 draft_id 单独比较 SHA，导致一项指标误报 false；改为 `(node_id,draft_id)` 后 20 份均一致。原指标保留为 `metrics.raw.json`，修正说明在 `metrics.json`，独立结果在 `audit.json`。未修改真实产物来迎合检查。

产物：`logs/ai-education-review-delivery-20261103/final-report.md`、`metrics.json`、`audit.json`、`runtime-status-audit.json`。终稿是隔离副本的阶段测试产物；原页面现场保持原状。自动技术重试策略和本轮额外发现均未修改。

后续已完成这两项代码修复及离线预览验证，未改写本次历史交付产物，见 [修复记录](CITATION_AND_DRAFT_ID_FIX_20261103.md)。
