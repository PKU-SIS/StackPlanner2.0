# Writer-first 局部改稿

2026-11-23，独立目录 `StackPlanner2.0_Report_multiturn`，分支
`feature/report-multiturn`，上游 `origin/sp2DeepResearch`，基线 `f94e066`。
用户最新要求：优先改得准、减少输出失败，简单修改不强制 GitAgent/Outline。
这取代早期固定串行角色流程的要求。首轮报告路径不变。

## 2026-11-24 基线与结构修订更新

普通 local_edit 默认当前 active_version，不再信任中枢委派中残留的旧 base_version_id。
本轮用户的 report_dialogue.base_version_id 或开头的 base_version_id=... 指令可明确选旧版；
自然语言旧版需求的复杂协调仍可走原版本管理流程。历史 resolved_query 的 mode/base 是溯源信息。
独立章节的增删可直接派 Outline report_task=revise，由宿主 fork/resume，无需先派 GitAgent；
章内段落／小标题调整仍由 Writer 处理，首轮及既有已准备修订路径不变。

## 实际流程

1. 原中枢接收最新原始消息；仍须显式 opt-in 并有已接受报告。
2. 小修改直接 `sp_delegate(target_agent="reporter", metadata={"report_task":"local_edit"})`。
   默认使用当前有效版本；用户显式选版方式见上方更新，不从历史委派参数继承基线。
3. 程序复用版本 store 完整复制、路径/哈希恢复和原生 resume，随后启动一次 Writer。
4. Writer 用 `report_find_sections` 定位，`report_read_section` 读章节，
   `report_replace_text(node_id, old_text, new_text)` 精确替换唯一片段。
   不受影响的正文逐字保留；不是重新生成整篇。
5. 工具通过原生 write_requests/accept_draft 保存新 canonical 草稿；没有第二本章节账本。
   Writer 只需普通完成说明，不需要输出 artifact JSON 或交接 envelope。
6. 程序执行原生装配、已保存来源/材料与引用绑定、篇幅及文件哈希检查。
   通过后返回 ready，沿用原生 Finish 写 delivered，再封存并发布；prepare/save 不推进 head。

结构调整、新材料研究和复杂需求协调仍可由中枢选择 Outline/Researcher，
必要时调用旧 perception GitAgent profile。没有额外 LLM 分类器，不改公共执行器。直改沿用原生报告 Writer 的 350 graph-step / 50 tool-call 执行容量，
不继承普通 reporter 的小循环容量；全局预算不变。

只读检查用既有 `sp_replan`：

```json
{"metadata":{"report_action":"inspect_version","version_id":"t0001","view":"evidence:n2"}}
```

view 复用现有 report/tree/draft/sources/evidence 快照读取。只读工具不调用网络。
整版复用或只读回答归档：

```json
{"metadata":{"report_action":"reuse_version","version_id":"t0001","mode":"exact_reuse"}}
{"metadata":{"report_action":"reuse_version","mode":"read_only","response":"根据已保存材料给出的回答"}}
```

归档后正常 Finish。ExactReuse 只切换 active_version，不 fork、不派 Writer、不推进 head；
只读不切换版本。上述程序动作无 GitAgent 循环，不增加中枢动作枚举。

## 去掉的负担与保留的含义

- 简单改稿不再要求 GitAgent 检查多次、生成完整自然语言需求，再经 Outline 全节点处分。
- 不要求 Writer 重新提交整章文本或遵守复杂 JSON 输出协议。
- 文本未匹配/多处匹配、未知引用等返回 `needs_adjustment` 与修正提示，文件不动；
  Writer 可读取后修正参数，不把这类情况直接变成 agent 输出失败。
- 成功保存的相同替换调用幂等，不产生重复草稿。模型无修改 ledger、版本 ID 或任意文件路径的工具。
- 零修改只回传说明并恢复旧有效版本；失败/取消/截断保留诊断与副本，不发布半成品。
- 每轮来源是“选定基线内容 + 最新原始修改 + 保留其他内容/继承整体约束”的有来源编辑合同，
  另存 resolved_query；不会把旧聊天当待办重放。继承树是基线结构，不是一次新需求分析结果。
  旧轮 keep/rewrite 决定留存为历史，不能错误约束新轮编辑。

**程序检查不能替代语义审查。** 简单路径不再强制 Outline 独立语义审查；原生 review 记录标明
`review_origin=programmatic_local_edit`、`semantic_review_performed=false`、
`factual_correctness_verified=false`，covered_requirement_ids 为空。
原生 candidate/final/delivery 的文件、引用和篇幅绑定仍执行，未降低阈值。
这仅证明工具实际保存和程序合同通过；任意自然语言改稿是否准确仍要实测。
歧义、缺证据或广泛结构影响由 Writer 说明并交回中枢澄清/升级，不能承诺自动分类永远正确。

## 离线回归

新增 `backend/tests/test_sp_report_writer_first.py` 覆盖真实中枢图直派 Writer、真实
SubagentExecutor/ToolNode（固定模拟模型）、单处修改、其他章节 SHA 保留、首轮旧文件全量 SHA
不变、重复工具保存、历史读取与部分撤销、独立恢复、失败/取消/截断不发布、篇幅不放宽、
普通说明无 JSON 协议、无 opt-in 禁止进入、只读/ExactReuse 无 agent/fork。

验证命令使用旧虚拟环境的依赖，但显式 PYTHONPATH 指向新 checkout；关闭追踪、禁写 pyc，
DEER_FLOW_HOME 位于独立 `/tmp`。真实异步执行器测试需放行本机事件循环通信；受限沙箱
运行曾停住，模拟用例在放行本机通信后通过。没有为此调用真实模型或更改 agent 预算。

本轮没有启动付费模型实验；新增调用 token/费用为 0。既有费用账本和未知预留未改动。
旧固定首遍实验的冻结代码/用例/结果未重写，不拿本次离线结果替换旧失败记录。
未推送、合并、部署或重启旧服务。

验证记录：

- 报告回归 `backend/tests/test_sp_report*.py`：658 passed（新增 Writer-first 15 项）。
- Writer-first 与受影响动作/协议/artifact/普通任务回归：252 passed，其中包含上述 15 项，不重复相加。
- 最后补齐完成事件后再次运行 Writer-first 15 项；lint、format、diff 检查通过。
- 显式检查 hooks、local_edit、turns.adapter、dr2_adapter 的 `__file__`，均来自新 checkout。
- 受限沙箱中一次异步测试挂起、一次 45 秒限时诊断退出；放行本机通信后正常通过。
  中枢图测试最初用错 checkpoint 消息规范化顺序，已按原生入口修正；未放宽预算或校验。


后续实际 API 测试见 [首批结果](writer-first-api-20261123/RESULT.md)。
首遍发现中枢猜错章节ID、Writer未识别冲突；补丁为 Writer 增加真实章节目录，
读取返回真实标题，原始用户要求优先于委派中的猜测。没有增加角色、拒绝门槛或自动重试。
