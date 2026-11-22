# 报告成稿后多轮与局部改稿：本地离线实现

日期：2026-11-22。开发计划：[../../MDPLAN.md](../../MDPLAN.md)。
基线为远程 `sp2DeepResearch` 的 `f94e06612d363b6a46f3d4bf59b342f77b786729`。
本次仅本地开发，不部署、不推送、不调用付费模型。首轮稳定性历史结果没有重跑或覆盖。

## 入口与边界

原用户消息先进入原 Central。显式后续轮在最新 `HumanMessage.additional_kwargs` 中提供：

```json
{"report_dialogue": {"enabled": true, "report_id": "r1"}}
```

这是当前内部 runtime 的 opt-in 接口，尚未新增网页开关或版本树 UI。仅当该线程 outputs
中确有原生 delivered 报告，且原生 final/review/文件绑定有效时，宿主才启用路由 Skill。
来源身份来自真实消息，用户身份来自现有用户上下文，线程身份来自运行上下文；模型不能传任意路径。
没有版本索引的首轮中，开关关闭或没有已接受基线均保持原路径；普通未绑定任务保持原路径。已经封存的版本不会因为后续消息漏带开关而解除修改保护。`report_task=revise` 不作为多轮判据。
最新消息的 `report_length` 在已绑定后续轮中不会抢先启动另一份报告。

没有外置 LLM 总路由。Central 用原有 `sp_delegate(target_agent="perception",
metadata={"report_profile":"git_agent"})` 选择 GitAgent profile。profile 仅提供：

- `report_inspect_version(version_id?, view="requirements")`：轻量索引，或完整需求、manifest、终稿、指定章节。
- `report_prepare_turn(base_version_id, resolved_query, change_hint?)`：完整当前需求、独立副本；不发布。
- `report_reuse_version(version_id, mode="exact_reuse"|"read_only", response?)`：独立快照/回答归档，不派 Researcher/Writer。

成功 prepare/reuse 是 profile 的 terminal tool。宿主只接受实际版本工具记录的结构化 handoff，
不把模型自由文字当准备成功。一次消息仅派发一次 GitAgent；工具重放不会重复 fork。
profile 工具调用上限为 8；图内部步骤沿用现有配置、最多 50，不增加全局重试预算。

GitAgent 不写报告、不调 Writer。prepare 后宿主先绑定副本，再调用原生 resume；随后 Central
沿用 Outline `revise`、现有 research gaps、write_requests、Writer、装配、审查与 Finish。
Outline 显式覆盖所有 current drafts 的 keep/rewrite/drop，仅派发需要改写的节点。
宿主对后续轮的原生写文件工具加路径限制：Outline 只能写其 delivery，Writer 只能写其指定草稿。
首轮的默认角色 Skill、提示、工具配置、篇幅合同、全局预算和动作枚举未修改。

## 文件和状态

新增 `backend/packages/harness/deerflow/sp/report/turns/`，只有版本元数据，不另建章节业务账本。
原生 ledger 仍是章节、证据、写作请求和验收的唯一业务状态。

```text
outputs/report/r1/                         原版；后续不原地 resume
outputs/report/r2/                         独立副本上的原生执行
outputs/report_dialogue/index.json         owner、accepted head、active version、幂等来源索引
outputs/report_dialogue/turns/tNNNN/
  turn.json                               原始 query、完整 resolved_query、来源、基线、状态
  resolved_query.json                     有来源标记的派生需求（修订）
  artifacts/ + manifest.json               全量独立快照、逐文件 SHA
  response.md                             本轮回答
  changes.json                            canonical 节点保留/修改/删除/新增
  runs/<action hash>.json                  实际执行状态、stop reason、原始用量记录
```

每份快照用真实复制，不使用硬链接。拒绝符号链接、越界路径、跨 owner 身份和损坏 SHA。
恢复程序分配新 `rN`，重绑 host/virtual 路径；控制 JSON 保存 derived-from 和新 SHA，
同步其文件哈希引用。材料和保留 canonical 草稿按字节核对。材料 catalog 是路径投影，可以重建。
每轮快照包含原生 ledger、树、材料、原始输入、草稿、候选、review、final、delivery 和长度合同。
执行配置沿用原 runtime；本轮没有加载 GitHarness 权重或替换模型工厂。论文是方法依据，
不是已接入的训练模型/独立 Research executor。真实实验的完整 provider 配置冻结仍属于后续实验工作。

原始聊天留在原 state/UI。懒登记的首轮标为 `requirements_status=native_accepted`：初始 request 只是来源，GitAgent 同时获得已接受需求、HITL 来源和已应用决定。后续解析的完整需求标为 `resolved`，不能让历史来源重新覆盖它。

`resolved_query` 明确标为派生需求，只在新副本投影为原生 request/input。
历史输入文件保留作溯源，但执行 packet 和 Central 请求视图使用完整当前需求，剔除旧聊天与 task memory。
旧树/旧草稿是基线材料，不自动变成当前用户要求。原生 Outline 必须将当前需求绑定到新的派生 input ID。

`prepared`、`executing`、`accepted` 分开；executor completed 也不是 native delivery。
只有新 resume 的原生 delivered、final/review/hash/长度绑定有效、run/turn 匹配，并通过预期 head
校验后才发布。原 delivery 不能充当本轮交付。发布索引写入中断可由同一 Finish 恢复，避免重新写作。
发布的 `head` 指最近新接受版本；`active_version` 表示当前有效版本。ExactReuse 可以选择一个已接受旧版
作为有效基线，不伪造新生成版本；只读问答不切换有效基线。

失败/取消保留独立候选、诊断和用量，不推进 head；运行结束清理接在已有 TaskMemory `after_agent`
钩子中，没有给首轮图增加一个新生命周期节点。解析失败也有独立基线快照，`resolved_query=null`、
`requirements_status=unresolved`，不会伪造需求解析成功。同一会话未完成轮次阻止另一条消息并发修改。

## 离线验证

新增 `test_sp_report_turns.py` 与 `test_sp_report_turns_executor.py` 覆盖：

- opt-in 无基线保持首轮动作一致；有基线时 GitAgent 工具范围与 terminal 返回。
- 首轮全部文件 SHA 保持、独立恢复、控制 JSON 重基 SHA、材料/草稿字节保留。
- 原生 Outline 提交 keep/rewrite → Writer 单章保存 → 新 review/final/Finish → 发布；其他章节 canonical SHA 保持。
- 第三轮选旧基线，完整需求独立于所选工作基线；撤销相关旧聊天/记忆不进入执行需求。
- 第二版后仍不能原地 resume；解析失败、取消、并发轮次、来源身份、跨会话、坏快照、越界写入。
- 重复来源不重复派 GitAgent/复制/写作；原生交付后的索引写故障和 Finish 恢复。
- read-only/ExactReuse 独立归档与 Finish，无 Researcher/Writer；ExactReuse 后使用正确的有效需求起点。
- 真实 SubagentExecutor + ToolNode 的 fake LLM inspect/prepare/handoff，没有 provider 调用。

最新审查、命令与数量见 [MULTITURN_REVIEW_20261122.md](MULTITURN_REVIEW_20261122.md)；首次实现和历史产物复验保留于 [MULTITURN_OFFLINE_RESULTS.md](MULTITURN_OFFLINE_RESULTS.md)。
旧 Python 环境仅提供依赖；所有命令显式设置新 checkout 的 PYTHONPATH，并记录实际模块 `__file__`。

历史产物可用新增脚本只读复验：

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$PWD/backend/packages/harness:$PWD/backend" \
/path/to/python scripts/validate_report_multiturn_snapshot.py \
  --source-outputs /path/to/historical/user-data/outputs \
  --work-dir /tmp/a-new-independent-validation-directory
```

脚本要求新的独立 work-dir；验证源 SHA、原生 final 绑定、独立恢复、合同继承、原生 resume 和取消不推进 head。
它不调用模型、不请求网络、不写源报告目录。

## 验证边界和暂缓项

离线脚本验证程序协议和隔离，不验证模型是否能准确理解任意撤销、选择最优基线、判断事实/引用语义。
测试中的完整需求和模型决策使用固定输入；不能把它们表述为真实多轮模型质量成绩。
没有运行 RACE/FACT 或重新运行历史一万档批次，也不承诺 token/时间节省比例。

首版使用本地文件锁及单 Gateway 既有原生 ledger 锁；不宣称分布式、多 Gateway 并发写入安全。
只支持成稿后串行轮次和一个线程内的一条报告版本谱系，不支持在写作途中并发改需求、跨报告合并或复杂 UI。
报告数据的分叉不会建立代码 Git 分支。对整篇独立新任务/FreshSolve，Central 仍走原生未绑定新报告入口；
`report_prepare_turn` 目前用于从已接受基线 ReuseAndPatch，不提供清空快照后跳过首轮协议的捷径。

付费多轮实验、语义质量验收、合回主线、推送与部署均未执行。
