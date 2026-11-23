# 报告多轮对话增量开发计划

- 日期：2026-11-22
- 状态：P0–P4本地实现完成；P5成稿后真实多轮及ExactReuse修正复测已执行，保留中间失败和一笔未知费用预留；事实评估/全面验收及P6发布另行安排。
- 开发基线：远程 `sp2DeepResearch`，本次核对为 `f94e06612d363b6a46f3d4bf59b342f77b786729`。
- 本文是当前统一开发计划。此前 `evaluation_runs/report_multiturn_design_20261122/` 下的文稿保留为分析依据；涉及中枢职责的冲突，以本文为准。

## 1. 已确定的目标与边界

1. 中枢仍是总入口，自主判断后续query是否需要调用GitAgent；不在中枢外另设LLM总路由。
2. GitAgent负责完整当前需求、历史版本选择和通过工具准备本轮独立工作区，完成后交回中枢；不写报告、不自行调Writer。
3. 更新侧优先复用Outline的修订profile与Writer的局部改稿profile，不新增一个全能Update Agent，也不重建报告流水线。
4. 修改通过新增的独立Skills和受限Tools注入；不重写中枢基础提示、动作集合、模型工厂或全局预算。
5. **首轮保持原路径**：没有上一轮已交付报告时，不调用GitAgent，不注入后续修订Skill/工具，不增加LLM路由或新的首轮交付门槛。
6. **每一轮独立存储**：本轮输入、完整有效需求、回答、产物快照、基线、差异、运行记录均可追溯。只读问答也有本轮记录及快照。
7. **全量封存、局部修改、按需生成**：复制完整产物是为了独立保存；只有需要改写/补证的部分重新执行。不得原地改上一轮成功版本。
8. 失败/取消/重复请求不得污染旧版、冒充成功或造成无限重试；失败产物和用量保留。
9. 首版聚焦一万档、成稿后的问答/局部修改/撤销/历史复用。生成前可讨论，但原生生成途中任意并发改稿、跨报告合并、复杂版本树UI和RL训练后置。

## 2. 分支与目录：开发前必须明确

2026-11-22再次只读核对结果：

| 位置 | 分支 | HEAD/状态 |
|---|---|---|
| 当前窗口目录 `/data/sp/yzb/StackPlanner2/StackPlanner2.0_Report` | 本地 `zx_sp2research` | `f843873`；4个tracked文件有未提交改动 |
| 独立整合/测试目录 `/tmp/sp2-report-integration-20261121` | `integrate/sp2-report-threebands-20261121` | `f94e066`；tracked工作区干净 |
| GitHub远程报告主线 | `sp2DeepResearch` | `f94e066` |
| GitHub远程Qwen保留版 | `zmy/qwen38-v18-approved-20261119` | `7b45e9e` |

旧远程 `zx_sp2research`、`sp2-report-handover-20261031`、`zmy/ds-threebands-unified-20261121` 已不存在，五个 `archive/report/*` 标签仍指向原始提交。当前目录显示旧分支是因为此前没有切换该运行目录，且本地remote-tracking缓存尚未清理，不代表远程整理失败。

- 后续从远程 `sp2DeepResearch` 新建 `feature/report-multiturn` 的**独立checkout/worktree**。名字为建议，当前未创建。
- 不在旧 `zx_sp2research` 目录继续堆开发，不自动stash/reset/checkout，不覆盖或清理原4处改动，不重启Demo。
- 原空树修复已移植进新主线，不重复套一次旧补丁。
- 正式开始时再读取远程SHA；如果已前进，记录并审查差异后固定实际基线，不自动混入未知改动。
- 仓库默认分支仍是 `main`；报告主线 `sp2DeepResearch` 不等于GitHub默认分支。大小写沿用现有名称，不创建仅大小写不同的重复分支。

精确检查记录：`evaluation_runs/report_multiturn_design_20261122/BRANCH_CHECK_20261122.json`。

## 3. 目标调用流程

```text
用户query → 原中枢
  ├─ 普通任务 / 首轮报告 → 原配置、原流程
  └─ 显式绑定报告的后续query
       ├─ 只读讨论/查来源 → 读取快照后回答，本轮独立存档
       └─ 中枢选择调用GitAgent
            Inspect历史 → 整理完整需求 → 选择base
            ├─ ExactReuse：复用有效旧版，本轮独立存档
            ├─ FreshSolve：准备独立新工作区
            └─ ReuseAndPatch：从base准备本轮副本
                 ↓ 返回结构化绑定和需求Artifact
            中枢沿用Outline / Researcher / Writer / 原生验收
                 ↓
            程序核对交付，封存并激活新版本
```

GitHarness交接应保留完整 `resolved_query`、base/target身份和非权威change_hint。模型不生成真实路径/版本ID，不把摘要当确定性补丁，不把首轮偶然章节安排提升为用户硬要求。

## 4. Skills封装（拟议名称）

| Skill | 对象 | 职责 | 默认隔离条件 |
|---|---|---|---|
| `report-dialogue-routing` | 中枢 | 何时只读回答、调用GitAgent、继续执行或开新任务；区分准备与已交付 | 只对显式报告后续轮加载 |
| `report-version-management` | GitAgent profile | 需求保留/撤销、历史检查、选base、准备后返回中枢，不写报告 | 只提供版本工具 |
| `report-revision-plan` | Outline修订profile | 影响范围、keep/rewrite/drop、补研和write_requests，沿用原树协议 | 只在本轮副本修订时加载 |
| `report-node-revision` | Writer修订profile | 读取原稿与修改要求，仅改被派发范围，保存到本轮目录 | 只给选中节点的修订任务 |

新增独立Skill文件，不直接给所有首轮都会读的通用Skill追加新内容。首版GitAgent可由既有 `perception` 执行器的专用profile承载，中枢仍用 `sp_delegate`；不为了名字先扩展 `ALLOWED_DELEGATE_AGENTS`。

## 5. Tools与程序适配器

以下是待实现接口，不是当前已有功能。

### GitAgent的三个版本工具

- `report_inspect_version(version_id?, view?)`：列轻量历史索引，或读指定版本完整需求/manifest。正文由执行侧按需读，不把所有历史报告一次注入。
- `report_prepare_turn(base_version_id, resolved_query, change_hint?)`：来源消息身份从可信运行上下文绑定；验证基线、权限及状态，程序分配turn/transition/工作区，保存派生需求Artifact并返回绑定。只准备，不更新已接受head。
- `report_reuse_version(version_id)`：验证有效快照及产物绑定，复用旧报告，保存本轮独立记录/快照，不触发研究、写作或无谓resume。

### 修订侧

优先复用现有 `read_file`、`report_read_material`、`report_submit_outline`、Writer保存与原生验收。可补一个只读 `report_check_local_diff`，检查实际改变范围、未选章节SHA及引用身份；不替模型判断事实是否正确。

### ReportTurnAdapter

新增独立模块（建议 `sp/report/turns/`），内部承担：

- store：封存、索引、独立副本、相对路径映射、校验和；
- tools：上述受限接口与幂等控制；
- adapter：把准备结果绑定原生报告上下文/resume，以及交付后的发布。

Adapter不是另一个中枢，不做LLM路由，不直接派Writer。发布只能依据真实原生交付/验收记录，不能由GitAgent自由调用“宣称成功”来推进版本。

优先用现有扩展点；必要时仅在报告上下文条件注入、profile工具绑定、工作区/有效需求投影、交付回调处加opt-in小接点。不要重构Central循环、Writer pipeline或另建业务账本。

## 6. 首轮保护与入口判定

进入新修订profile至少要求：功能开启、绑定报告会话、上一轮报告已接受、当前是后续请求。是否启用由宿主验证，不信任模型自报 `turn_index=2` 或任意路径。

**不能只用 `report_task=revise` 判定多轮。首轮补研后的大纲整合也使用revise。**

- 开关关闭：原路径。
- 开关开启但无基线：仍是原首轮路径，无GitAgent/新增Skill/新增LLM调用。
- 有报告但当前是普通非报告任务：不劫持原任务。
- 最新消息带report_length也不能抢在多轮决策前强行开新报告；保留无多轮上下文时的原生入口行为。
- 首轮版本可在首次后续请求时按原生交付/SHA懒登记，不修改r1、不改变首轮交付门槛。

## 7. 每轮产物与工作记忆隔离

沿用原生 `outputs/report/r1`、`r2` 等路径，避免迁移首轮文件及旧下载链接。新增旁路 `outputs/report_dialogue/index.json` 和 `turns/tNNNN/` 存轮次、完整快照、回答、差异与运行记录。

完整快照至少涵盖：原始反馈、当前有效需求及其派生来源、基线、ledger、树、材料/来源/locator、草稿、候选/终稿、引用映射、原生合同和配置签名、文件SHA。对话轮ID、tree_version、draft_id和Artifact.version不是同一个编号。

- 新轮从选中base复制，不隐式用最新head的文件。第一版完整复制，不共享可写ledger/draft或用硬链接节省空间。
- 在新副本上执行原生resume，旧版本交付/review/final不被改为stale。控制JSON重基记录新SHA/derived-from，原材料和保留草稿字节核对，不放宽校验。
- UI可保留完整聊天，执行侧只收到完整当前需求与选中基线。原话不删除/篡改，派生resolved_query不冒充用户原话；撤销内容不能从历史输入或任务记忆重新成为待办。
- 候选只有执行中/失败/取消等状态，验收成功才通过预期head校验发布。重复消息、工具重试幂等，不重复fork或派Writer。
- 只读问答/ExactReuse同样有本轮独立档案和快照，但可绑定原报告版本，不伪造新生成或新正确性证明。
- 所谓局部更新优先按章节草稿划分；必要摘要/结论/目录/引用编号联动要列明。要求严格段落外字节不变时需差异校验，不能把整章重写冒充一句修改。
- 一万字合同跨轮继承；删章后与9000下限或“其他不动”冲突时明确处理，不偷偷扩写。不能为了复用保留过时口径或失效来源。

## 8. 开发任务与顺序

### P0：固定开发环境与原路径基线

- [x] 阅读论文、现有接口，按用户选择统一中枢委派方案。
- [x] 只读核对本地/独立checkout/远程主线与归档标签。
- [x] 从已核对主线创建独立 `feature/report-multiturn` checkout；备份旧目录状态但不迁移运行服务。
- [x] 固定代码/配置/协议版本与现有首轮离线轨迹、测试命令。
- [x] 明确GitHarness对应源码/权重版本；不能把本地某个尚未支持Research的旧C2副本直接接进来替换SP2。

### P1：无模型的版本/隔离底座

- [x] 实现manifest/index、freeze、fork/read/reuse、恢复映射与逐文件SHA。
- [x] 用已有报告离线验证新副本可恢复、旧文件不变、只读路径不写基线。
- [x] 验证失败/取消head不变、幂等、身份/会话隔离、损坏快照拒绝复用。

### P2：GitAgent封装与中枢交接

- [x] 实现三个版本工具及结构化handoff；真实用户输入来源明确。
- [x] 新建GitAgent Skill和perception专用profile，限制工具与步数，terminal准备/reuse后结束；不增加全局重试预算。
- [x] 中枢仅在报告后续轮加载路由Skill，正常调用现有sp_delegate并接收handoff。
- [x] 绑定本轮独立工作区和完整有效需求，不能只传delta或污染旧记忆。

### P3：原生局部修订

- [x] Outline修订Skill接现有keep/rewrite/drop、补证及write_requests。
- [x] Writer修订Skill得到原稿和范围，仅写本轮路径；未受影响章节复用。
- [x] 复用原生装配/审查/Finish；封存与head发布绑定真实交付。
- [x] 增加修改清单/允许的引用显示变化，不绕开证据、篇幅或哈希验收。

### P4：离线回归和失败注入

- [x] 首轮开关关闭/开启无基线两种调用路径与原路径一致；无新增模型步骤、Skill/工具/预算变化。
- [x] 非报告首轮及后续普通任务不受影响。
- [x] 第二轮修改指定章节，第一轮全部SHA和本轮未改canonical草稿SHA保持。
- [x] 第三轮正确选历史base；撤销一项时保留其他仍有效修改。
- [x] 只读问答和ExactReuse独立存档但无Researcher/Writer调用。
- [x] 失败、取消、重复调用、无基线/损坏快照、跨会话ID均可诊断且不污染成功版本。
- [x] 验证一万档篇幅冲突、旧review不能冒充新验收、来源/locator和版本绑定。

### P5：小量真实多轮验证

- [ ] 冻结2–3道题的5–7轮反馈，先固定逐轮预期保留/新增/撤销项，评测答案不进入Agent上下文。
- [ ] 先测一万档，保留全部失败/恢复，不按前题调参或重跑替换失败。
- [ ] 统计GitAgent和原生执行器全部用量、阶段/角色、API/工具/委派次数；缓存包含于输入，不双计。
- [ ] 检查逐轮要求、局部保持、历史基线、发布/恢复及时间成本；不是只检查最后一轮有文件。
- [ ] 使用同配置的原生resume和每轮重生成作对照；缓存条件披露，不预先承诺提速或省Token比例。

### P6：评审后再决定发布

- [ ] 汇总首轮非回归、各轮独立性与真实多轮结果；查看实际diff与剩余问题。
- [ ] 再讨论合回报告主线、网页入口/版本展示与部署。当前计划不执行这些操作。

## 9. 验收硬条件

- 首轮路径、提示/工具/预算和模型调用数不增加；用确定性离线轨迹核对，不要求随机真实生成逐字同文。
- 每轮独立存储，旧版SHA保持，本轮能从自己的快照恢复，不依赖另一本可变账本。
- 当前需求完整，未撤销要求保留，撤销内容不再以当前要求出现在正文/表格/摘要中。
- GitAgent只负责需求/选基/准备，中枢保持调度，原Outline/Writer协议与验收保留。
- 修改范围可解释，未改草稿有hash证据，必要联动单列；程序校验不冒充事实正确性保证。
- 失败不推进已接受head、不丢诊断，不无限重试或混淆准备成功与交付成功。

已有一万档3次单轮验收通过仅是当前基线，不是本计划的多轮验收成绩。RACE/FACT另行评估；中间轮需求改变后不能盲套原单轮criteria/reference。未实施和回归前不承诺零影响。

## 10. 计划编写时的交付记录（实施前历史）

本轮只新增MD计划和只读分支检查记录。未创建开发分支、未切换旧目录、未清理缓存、未修改生产代码、未启动测试/API实验、未推送或部署。
MDPLAN与检查记录便于后续开发窗口直接接手；正式实施前先复核基线和本地改动，不把此文中的拟议工具/字段当作已有接口。

## 11. 2026-11-22 本地实施记录

- 独立目录：`/data/sp/yzb/StackPlanner2/StackPlanner2.0_Report_multiturn`。
- 分支 `feature/report-multiturn`，上游 `origin/sp2DeepResearch`；基线 HEAD 为 `f94e06612d363b6a46f3d4bf59b342f77b786729`。实现和离线审查冻结为本地 feature 分支提交；未推送。
- 先添加失败测试，再实现 `sp/report/turns/`、受限版本工具、perception GitAgent profile、条件中枢/Outline/Writer Skill 与原生交付发布。
- 原生 ledger 仍是唯一章节业务账本；新增 index 只管理快照、身份、head/active_version 和幂等元数据。
- GitHarness 仅按论文方法适配，没有接入旧 executor 或任何训练权重；未修改模型工厂或中枢动作枚举。
- Skills 放在 `skills/report-multiturn/`，不进入默认 public Skill 发现。
- 真实历史一万档产物只读复验：230 个源文件 SHA 保持，215 个 canonical 文件字节保持，独立恢复、原生 resume、合同继承及取消不推进 head 通过，模型调用为 0。原批次未重跑。
- P0–P4 的完成指程序实现和固定输入/fake LLM 离线验证；不是任意自然语言需求解析、事实正确性或真实多轮质量验收。
- 网页开关、跨报告合并、并发在途修改、复杂版本树 UI、RL、付费实验与部署均未实施。
- 可复验命令、最终测试数量、已发现并修正的问题、边界及精确工作区记录见：
  [MULTITURN_IMPLEMENTATION.md](docs/research-report/MULTITURN_IMPLEMENTATION.md)、
  [MULTITURN_OFFLINE_RESULTS.md](docs/research-report/MULTITURN_OFFLINE_RESULTS.md)、
  [MULTITURN_WORKSPACE_20261122.json](docs/research-report/MULTITURN_WORKSPACE_20261122.json)。

## 12. 离线审查与实验材料冻结

在新 checkout 补充失败回归并修复封存版本漏开关保护、激活失败卡轮次、快照 manifest 断点、
首轮 HITL 需求来源、恢复身份复用、未受限角色、指针漂移和原生 close 清理。
真实 Central 图验证首轮开关/无基线提示与工具一致，以及 read-only/ExactReuse 无研究写作。
完整离线组 1053 项通过；新冻结两题各七轮请求与独立验收预期。
见 [MULTITURN_REVIEW_20261122.md](docs/research-report/MULTITURN_REVIEW_20261122.md)。
P5 的“准备题面/预期”子项已离线完成，但模型配置、预算与真实实验未启动；P5/P6 仍不标为完成。

## 13. 连续七轮离线演练

完成 library-rag 的正常七轮，以及独立存储中的第 3 轮取消、重复来源抑制、新来源重试后继续七轮。
使用真实中枢图、子代理执行器、ToolNode、原生提交/保存/验收；只有模型和检索内容为合成夹具。
修复继承固定验收动作 ID 导致跨轮重放的问题，宿主只为多轮副本添加 turn namespace。
1056 项广域回归通过；两条轨迹各七轮可独立恢复，旧版/未改草稿 SHA 保持。
全文估算 9764–9801，原生一万档门槛保持；provider 调用和网络连接尝试均为 0，事实质量未评估。
证据见 [MULTITURN_REHEARSAL_20261122.md](docs/research-report/MULTITURN_REHEARSAL_20261122.md)。
P5 真实模型实验及 P6 合并部署仍未执行。

## 14. 单题七轮 API 前离线预检

补充公开配置/九项工具/角色请求形态、226个源码与Skill文件、实际SDK环境的冻结与核验工具。
费用、token、时限和停止规则为外层草案；预算未选择时为null，API授权始终关闭。
独立数值审计覆盖Central、GitAgent和原生角色，保留缺失/冲突用量及未知费用，要求HTTP清单独立对账。
来源问答增加封存树、目录、材料原文行视图；中枢直接回答也独立response_only存档，不强制新增GitAgent步骤。
广域1073项和预检17项通过；没有运行API实验、RACE/FACT、合并或部署。
见 [单题预检与语义复核](docs/research-report/multiturn-pilot-preparation-20261122/PLAN.md)。

## 15. 实验 worker 数值接线（仅离线）

补齐HTTPX逐次发起前预留、同步/异步/SSE收据、callback source对账、原生角色与后台记忆Timer归属。
run_turn包装原图，轮间排空记忆，整例限额/计数不重置；不增加模型路由、不接管报告发布。
父进程监督单调时钟时限和取消，终止后排空并保留预留/结算日志；不自动重启。
只允许MockTransport，监督子进程到退出均禁用socket，不加载凭据或启动真实API。
输入上下文预留上界、账户币种与费用授权仍待真实启动前确认；没有把离线成功当账单/事实成绩。
证据见 [worker离线接线](docs/research-report/multiturn-pilot-preparation-20261122/WORKER_OFFLINE.md)。
P5真实模型实验、P6合并部署保持未执行。

## 16. 50元预算最后离线审查

用户认可整例50元以内DeepSeek预算。本轮落实48元调用额度和2元余量，核验官方1M上下文，
按2^20输入上界保守预留，15M输入/1M输出/2000工具尝试联合费率上估48元。
预检新增可选公开审查，离线worker直接绑定冻结限额；并发在途额度统一计算。
修复审查发现的gzip SSE收据解码问题；不修改默认模型工厂、提示或首轮调度。
完成单题七轮实验单；预算认可不等于API启动，真实账户币种/实时模型费率/真实传输准入仍待后续。
见 [50元实验单](docs/research-report/multiturn-pilot-preparation-20261122/EXPERIMENT_SHEET_50CNY.md)。
P5付费实验、P6合并部署仍未执行。

## 17. 用户明确授权真实启动

用户最新要求实验尽早启动，已只读核验官方账户可用且币种CNY，未打印密钥或复制旧.env。
新增显式启动入口与官方受限TLS传输，共用既有预留/收据/回调核心，使用原生图和SQLite检查点。
入口默认仅预检，实际CLI必须显式--start。真实library-rag单题七轮已启动；不自动付费重跑。
新入口专用10项、费用/入口组合66项、广域1122项离线回归通过。
P5已完成首次真实首轮尝试：全文估算11423超出11000，原生转blocked_user/ASK_HUMAN，后六轮未执行。
83次模型、23次直接工具请求对账完整，峰值费率上估2.77305512元，预留残留0；未自动付费重跑。
多轮验收仍未完成；P6合并部署仍未执行。
见 [真实试验记录](docs/research-report/MULTITURN_LIVE_PILOT_20261122.md)。

## 18. 跳过新首轮，直接测试既有成稿的真实多轮

按用户新要求，选取历史已验收的低代码/无代码报告（全文估算9234）为只读来源，验证230文件与原生交付后独立恢复。
在新线程仅暴露一个已接受origin，真实运行第2–7轮：单章改稿、另一章补充、选旧基线撤销部分要求、只读问答、ExactReuse、从恢复版继续修改。
旧来源SHA和canonical字节保持；题面按实际章节设计，验收预期仅在模型执行后读取，不进入模型输入。
新费用guard承接上次已结算费用，不重置总额度。原library-rag题面/记录均保留，未重跑首轮或旧稳定性批次。
见 [成稿后多轮试验](docs/research-report/seeded-lowcode-20261122/PLAN.md)。

成稿后真实结果：2/3/4/5轮通过；6轮内容完全恢复但误走prepare/review，新建重复版本并错误推进head，ExactReuse判失败；7轮停止未执行。
每轮独立恢复通过；源230文件与未改canonical SHA保持。新增峰值费率上估3.43721368元，连同首轮尝试累计6.21026880元，对账完整、预留残留0。
修复入口真实消息ID和逐运行建图适配，未重放已成功的付费轮次。最终1125项离线回归通过，不能替代真实ExactReuse失败。
见 [成稿后真实验收结果](docs/research-report/seeded-lowcode-20261122/RESULT.md)。P5仍未全部通过，P6未执行。

## 19. ExactReuse操作契约与独立复测（2026-11-23）

用户授权继续修复并扩大真实实验。中枢沿用sp_delegate，在同一决策中声明patch/exact_reuse/read_only；宿主按声明限制GitAgent工具和handoff，legacy auto兼容保留。
修正两份独立多轮Skill对整版恢复与部分恢复的歧义，不改默认Skill或首轮路径。
从已成功1–5轮前缀创建全新独立会话重测6–7，原失败记录保留。1130项离线回归通过，独立Skill验证通过。
详见 [ExactReuse复测](docs/research-report/exactreuse-retest-20261123/PLAN.md)。

扩展对照继续暴露只读分散读取和静态DNS快照误拦风险。增加受限 evidence:<node_id> 读取，官方公网DNS刷新仍受host/443、TLS和审计作用域限制。
一个未知后台请求保留原始2.162688元预留，不重放、不按0费用结算；新独立前缀试次若显式携带预留，仍计入总上界，禁止对旧失败试次resume。
费用对账按当前试次与全局分别标识，全局存在旧预留时不声称全部结清。

2026-11-23最终：原题与改写6–7、部分保留4及续测5–7的8个目标功能步骤已覆盖通过，21份档案独立恢复有效。
最终1139项离线回归通过。累计已知部分峰值上估10.68349592元，另有2.162688元旧未知预留未解除，不宣称全局费用已结清。
原失败实验、3次中间停止均保留，未重放未知请求；未部署、推送、合并或重启服务。
见 [ExactReuse修正后真实结果](docs/research-report/exactreuse-retest-20261123/RESULT.md)。

## 20. 冻结实现的首次完成率与Token记录

按用户同意，暂不增加护栏，两个已验收成稿主题各两种表述，计划四会话24条后续请求。
一次启动、不人工修复/重跑，原生有限修正保留且计入消耗；批次中不改后端/Skill/模型/题面。
仅扩展实验层被动观察与整批token汇总容量，按source ID对账并导出会话/轮次/角色token与缓存子集。
旧未知费用预留仍计入同一总额度，不宣称全局费用结清。详见 [首次完成率协议](docs/research-report/firstpass-frozen-20261123/PLAN.md)。

冻结批次已完成：4会话完整完成1/4、无可见错误完成0/4；24条计划中尝试15、通过12、依赖失败未执行9。
三次GitAgent在7次不同参数版本读取后loop_capped；不能自动认定所有拒绝是误拦，也不能仅凭名称认定重复循环。
本批输入6998720、输出345988、总7344708 token，缓存读4791808已包含输入，按角色/轮次CSV保留。
已知费用本批峰值上估7.37340032元、累计18.05689624元；旧未知预留2.162688元仍占额度，无新未知请求。
266冻结SHA一致，1143项离线回归通过，未中途调参或重跑。详见 [固定批次结果](docs/research-report/firstpass-frozen-20261123/RESULT.md)。
