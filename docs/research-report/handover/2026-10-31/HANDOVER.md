# 1031-任务交接

截至 **2026-10-31**。**正式归档：本分支保存当前源码 `109f6859c8a8b76a9b8f9e23a8f2366dbf65f948` 的完整快照；报告审查与 case 基线固定为 `d05b3845053bd21ce53f6b69796a1b5f58fe3785`。ef51f38、109f685 仅作为当前实际状态附带归档，不代表其前端改动已获报告验收认可；本轮不处理其保留或回退。**

仓库：[PKU-SIS/StackPlanner2.0](https://github.com/PKU-SIS/StackPlanner2.0)。归档分支：`sp2-report-handover-20261031`，保存当前完整源码快照和三份交接文档，不改变生产或原分支。

## 目标与当前进展

1. 第一阶段：满足 MemoryFlow 数据集产出要求。
   - 稳定产出满足用户要求的正文，并实际完成最终交付。
   - 正文尽量实际引用至少 **6 个不同有效来源**。同文转载、镜像、重复保存和仅列参考文献不能凑数；同机构的不同实质文档可以分别计数。
   - 修复可避免的子任务故障；真实失败、截断和停止原因必须保留，不能隐藏或改成成功。
2. 第二阶段：满足 Githarness 后续对接要求。
   - 无论模型规模，都能稳定生成 `.md` 报告，作为后续目标，尚未验收。
   - 训练前 Deep Research Bench 目标：`Qwen3.8-27B ≥45`、`DeepSeekV4 flash ≥48`。模型名称及分数按用户要求记录，尚未跑分验收；评测集版本、评分口径和运行配置需明确后再安排。
3. 第一阶段尚未完成；已改善 Outline 交付与恢复，最新 case 主要卡在 Researcher 取证质量和完整返回。第二阶段不是已取得的结果，也不代表本次授权启动 benchmark。

## 版本说明

- **报告优化审查与 case 基线：`d05b3845053bd21ce53f6b69796a1b5f58fe3785`。** 本文代码链接固定在该版本，避免后续分支移动改变交接含义。
- 本次同步前的核对结果：本地及生产 HEAD 已是 `109f6859c8a8b76a9b8f9e23a8f2366dbf65f948`，原分支 `zx_sp2research`；GitHub 原分支仍为 `b7988146d1a2f1fe2a5b83d396b45b1e1b8dd4b9`，当时报告优化代码尚未同步。完成本次归档后，以独立交接分支的代码和文档为准。
- `d05b384` 后另有 [ef51f38](https://github.com/PKU-SIS/StackPlanner2.0/commit/ef51f38)、[109f685](https://github.com/PKU-SIS/StackPlanner2.0/commit/109f685) 两项前端改动，其中包含过滤失败子任务卡片的行为，与“真实失败必须显示”的要求存在冲突，未纳入报告优化验收。**交接分支保留当前实际源码供接手人核查；Git 归档不表示认可这两项改动，不部署或回退，保留与否需另行决定。**

## 当前主流程与近期改动

```text
可选预研 → 初始 Outline → 固定一轮补检索 → 全部返回后 Outline 集中裁定
→ 顺序 reporter 分段写作 → 宿主组装/验收 → Central 最终交付
```

Central 调度，Outline 决定结构与证据充分性，Researcher 搜抓存，Writer 根据节点证据写作。研究可并发，Outline/Writer 目前顺序执行。gap 是研究问题；额外研究须有用户明确授权。

| 提交 | 已完成的事 | 验证边界 |
|---|---|---|
| `a5ef0cd`、`6a6f4b8` | Outline 分块指引；Researcher“先保存候选再搜索”提示 | 已上线，提示未保证实际遵守 |
| `fffb250` | `report_update_outline_tree` 分批更新已有树，程序生成完整候选 | 仅用于已有树的 `revise`；初始规划和 `integrate_review` 沿用原路径 |
| `43f3a36`、`d05b384` | 暂存恢复、旧候选兼容、旧 delivery 失效、失败/超时/异常恢复记录 | 执行侧报告 92 项测试通过；规划侧独立复跑新增 17 项并验证恢复流程 |

暂存、finalize、正式接受是三个边界：**finalize 只检查树结构并写候选，最终业务验收仍走原 handler**。报告优化补丁未加入 Researcher 中间件、公共执行器注入口，也未改预算、搜索供应商或前端。[工具审查摘要](https://github.com/PKU-SIS/StackPlanner2.0/blob/sp2-report-handover-20261031/docs/research-report/handover/2026-10-31/OUTLINE_STAGING_REVIEW.md)

## 最新真实 case：工具有效，报告未交付

题目：“中外博物馆教育的现状与未来趋势”，要求国外按代表性国家分别归纳、分类自成体系、科技与教育理念结合且中国未来部分详写。脱敏 case 编号：`04f86258`。

**tree v2 / 9 节点 / 5 条材料记录 / 0 草稿 / 无 final_report / 未 delivered；正文实际来源数为 0。** run 的 `success` 仅表示正常停在人工澄清处。

两次最终 Outline 均使用新工具：第一次漏 `gap_outcomes`，被业务验收拒绝；第二次恢复前次 stage、补齐裁定后接受 v2，节点完整保留。

主阻塞：

1. 研究覆盖不足：国外仅有美国 AAM 材料；未来趋势和中国详写无可用证据。g1/g3 为 `awaiting_user`，Writer 未启动。
2. Researcher 返回不完整：g1 有 `loop_capped`；g1/g3 缺 `gap_attempts`，宿主如实记 `technical_error`，不补 `searched`/round。`rounds=0` 不代表自动尝试没执行，不能据此重派。g3 未见明确 stop_reason，不猜补。
3. 材料可用性不足：m3 是 987 字符反爬 JS；m1/m4 为转引或二手名单，不能冒充官方原始统计。保存成功不等于有效证据。

[最新 case 脱敏证据摘要](https://github.com/PKU-SIS/StackPlanner2.0/blob/sp2-report-handover-20261031/docs/research-report/handover/2026-10-31/CASE_04f86258.md)

## 留给接手人的问题

1. 在现有保护内完成必要搜抓存、正文核读和完整 `gap_attempts`；区分零结果、技术失败、证据不足。“先存再搜”提示已试过，不能只验证字符串存在。
2. 处理反爬/空壳内容、正确归因二手资料，围绕真实缺口获得不同实质来源，再进入节点 `evidence` 与 Writer 正文。来源支持关系由 Agent 判断，不做主题关键词或域名白名单式语义验收。
3. 减少 Outline 协议遗漏重试和重复输入。历史 case 最终裁定约 94.5% token 为输入；360k 是累计输入+输出预算，不是树大小上限。新工具尚未证明解决全部输入开销。

生产搜索仍用 DDGS，direct_fetch 默认 20 秒、1 MiB；历史 403、超时、DNS、PDF 超限需分别定位。LangSearch 仅有限对照，未证实改善、未切生产。最新版本无完整端到端交付证据，Writer 和最终交付仍需验证。

## 仓库入口与开发边界

| 位置 | 职责 |
|---|---|
| [sp/report/](https://github.com/PKU-SIS/StackPlanner2.0/tree/d05b3845053bd21ce53f6b69796a1b5f58fe3785/backend/packages/harness/deerflow/sp/report) | hooks：装配/接受/恢复；outline_staging：暂存工具；packets：上下文；materials：材料；ledger/tree/writing：业务契约 |
| [report skills](https://github.com/PKU-SIS/StackPlanner2.0/tree/d05b3845053bd21ce53f6b69796a1b5f58fe3785/skills/public)、[prompts.py](https://github.com/PKU-SIS/StackPlanner2.0/blob/d05b3845053bd21ce53f6b69796a1b5f58fe3785/backend/packages/harness/deerflow/sp/report/prompts.py) | 角色行为和输出协议 |
| [DDGS](https://github.com/PKU-SIS/StackPlanner2.0/blob/d05b3845053bd21ce53f6b69796a1b5f58fe3785/backend/packages/harness/deerflow/community/ddg_search/tools.py)、[direct_fetch](https://github.com/PKU-SIS/StackPlanner2.0/blob/d05b3845053bd21ce53f6b69796a1b5f58fe3785/backend/packages/harness/deerflow/community/direct_fetch/tools.py) | 搜索与抓取 |
| [sp/runtime.py](https://github.com/PKU-SIS/StackPlanner2.0/blob/d05b3845053bd21ce53f6b69796a1b5f58fe3785/backend/packages/harness/deerflow/sp/runtime.py)、[公共执行器](https://github.com/PKU-SIS/StackPlanner2.0/blob/d05b3845053bd21ce53f6b69796a1b5f58fe3785/backend/packages/harness/deerflow/subagents/executor.py)、[中间件](https://github.com/PKU-SIS/StackPlanner2.0/tree/d05b3845053bd21ce53f6b69796a1b5f58fe3785/backend/packages/harness/deerflow/agents/middlewares) | 执行与公共保护；不要默认修改 |
| [暂存工具测试](https://github.com/PKU-SIS/StackPlanner2.0/blob/d05b3845053bd21ce53f6b69796a1b5f58fe3785/backend/tests/test_sp_report_outline_staging.py) | 暂存、恢复、真实接受路径回归 |

环境接手从 [README](https://github.com/PKU-SIS/StackPlanner2.0/blob/d05b3845053bd21ce53f6b69796a1b5f58fe3785/README.md)、[backend/AGENTS.md](https://github.com/PKU-SIS/StackPlanner2.0/blob/d05b3845053bd21ce53f6b69796a1b5f58fe3785/backend/AGENTS.md)、[配置模板](https://github.com/PKU-SIS/StackPlanner2.0/blob/d05b3845053bd21ce53f6b69796a1b5f58fe3785/config.example.yaml) 和 [启动脚本](https://github.com/PKU-SIS/StackPlanner2.0/blob/d05b3845053bd21ce53f6b69796a1b5f58fe3785/scripts/serve.sh) 开始。它们是仓库说明，不等于当前生产实例的实际配置。

生产绝对路径、SSH 设置、运行配置、数据库及完整运行定位信息由负责人另行提供；不伪造其 GitHub 路径，不上传密钥、数据库或原始运行转储。

复用现有 main 能力，skill + tools 优先，最少增量，保护普通功能。公共机制、预算/保护、供应商和费用变化先讨论；不增加自动研究轮次、全局六来源硬门禁或独立 Reviewer。不恢复已撤回的 `83be3df`、`dc567b6`。本次仅交接同步，不改产品代码、部署、续跑 case、调用付费模型或启动 benchmark。
