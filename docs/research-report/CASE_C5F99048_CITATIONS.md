# Case c5f99048：为什么整篇报告只有 4 个来源

依据：用户提供的 [轨迹包](../../SP2_c5f99048_agent_trajectory_20261101/TRAJECTORY.md)，对照当前 `zx_sp2research` 代码。本次只读分析，未重跑模型。

## 1. 来源漏斗：收缩主要发生在研究阶段

| 阶段 | 实际结果 |
| --- | --- |
| 预检索 | 3 次成功搜索，返回 15 个不同 URL |
| 预检索抓取 | 尝试 2 次：Google 博客超时，A2A 首页保存成功；只有 **S1** |
| 定向研究 g1 | 新保存 MCP 入门页 **S2**、规范介绍页 **S3** |
| 定向研究 g2 | 新保存 A2A/MCP 对比页 **S4**；Google 英/日博客抓取失败，搜索报不可用；该子代理最后 `token_capped` |
| 最终 Outline | 14 个节点，其中 9 个写正文；使用全部 4 个来源 |
| 最终报告 | 正文 52 次引用：S1=37、S2=2、S3=1、S4=12；来源表仍只有 4 项，来自 2 个文档站点 |

9 个正文节点中：4 个仅绑定 1 个来源，4 个绑定 2 个，只有 1 个绑定 3 个。

## 2. 对前面猜测的修正

- **本例没有“保存了很多文档、summary 压缩后只剩 4 篇”的证据。**全过程只成功保存 4 篇，而且全部进入终稿。
- **Outline 实际读了原文。**初始规划读取 S1，收尾读取 S2、S4；没有观察到只凭 summary 规划的行为。S3 的行定位仍主要沿用研究结果。
- `research/.../summary.md` 在代码里保存的是 Researcher 的 `artifact_content`，并非仅保存给 Central 的短 `summary`。本例四份材料均标记 `upstream_truncation=false`。
- 15 个搜索候选没有整体保留到预检索材料交接中，summary 主要保留 S1 和少数官方候选。候选相关性与质量尚未逐篇核实，不能把 15 个 URL 等同于 15 篇有效证据。

## 3. 原因：事实与解释

**① 预检索契约偏“快速找到够用信息”，与我们想要的广覆盖不一致。**

代码明确称其为 `small orientation pass`、收集 `bounded set`。本例 Researcher 在预算告警后明确说“已有足够 orientation 材料”，只带 1 篇保存文档结束。3 次搜索、目录检查、失败抓取和原文回读共同消耗了这次委派的工具预算。若目标是 10–20 篇有效文档，需要调整任务契约及预算/批次安排。

**② 搜索发现没有完整转成后续可用的材料资产。**

当前 Skill 要求保存“依赖的来源”，没有要求把所有相关候选连同未抓取原因交给 Outline。Researcher 先选少量“足够回答”的文档，其他候选就从后续视野消失。我们的“广泛发现、保留相关材料，再让 Outline 做内容判断”方向有价值，但本例的信息流失更早发生在候选筛选与抓取阶段。

**③ 抓取失败与来源选择收窄进一步限制了供给。**

7 次抓取中 4 次成功、3 次失败；失败集中在 Google 博客，另有一次搜索不可用。Central 委派偏向官方资料，Outline 为两个 gap 都设置 `must_be_primary=true`。这是比原始用户请求更窄的执行范围；它有利于权威性，也减少了可用于分析和交叉验证的候选空间。当前 case 的提供方与配置不能直接用本地 Jina 配置代替判断。

**④ 补充研究只修补了两个缺口，没有扩展报告的证据地图。**

g1 聚焦 MCP 定义/范围，g2 聚焦 A2A 创新/问题；分别增加 2 篇和 1 篇。收尾委派明确要求“不新增自动研究”，Outline 随后将两项判为 resolved，全部正文节点置 ready。固定一批研究、窄缺口与偏低的充分性判断叠加，锁定了后续写作的来源上限。

**⑤ Writer 和拼接沿用了这个窄证据集合。**

Writer 只能使用节点给定证据；拼接负责识别正文已有的来源标记，不会主动拓展证据。终审也没有将来源覆盖作为阻塞问题。因此已有四份来源被充分重复使用，更多来源没有进入报告。

## 4. 优先讨论的调整

1. **预检索交付改成材料地图**：保留相关候选目录、筛选理由、抓取状态和原文入口；以 10–20 篇有效相关文档为实验目标，按主题维度覆盖，不因找到单篇概览就收尾。
2. **预算按材料采集设计**：减少无效重复与诊断消耗，允许有界分批抓取；只改 summary 文案不足以解决材料少的问题。
3. **Outline 收尾检查节点证据覆盖**：主要节点约 3–5 个有效来源可作为实验目标，包含已有来源；检查关键论点的证据互补性，允许明确、有界的补充研究。

本例最终成功交付，耗时约 11 分 41 秒；它能证明来源覆盖窄，不能作为“无法停机”的失败样例。

## 证据入口

- [完整子代理步骤](../../SP2_c5f99048_agent_trajectory_20261101/subagent_steps.jsonl)：预检索第 3–14 行；研究第 32–57 行；Outline 原文读取第 16、59 行。
- [预检索产出](../../SP2_c5f99048_agent_trajectory_20261101/artifacts/report/r1/research/spact_call_7eaed66837394b78a57947a7/summary.md)、[最终树](../../SP2_c5f99048_agent_trajectory_20261101/artifacts/report/r1/tree/2.json)、[终稿](../../SP2_c5f99048_agent_trajectory_20261101/artifacts/report/r1/final/report-1.md)。
- 代码：[研究提示词](../../backend/packages/harness/deerflow/sp/report/prompts.py)、[研究 Skill](../../skills/public/report-research/SKILL.md)、[研究正文保存](../../backend/packages/harness/deerflow/sp/report/hooks.py)、[节点输入包](../../backend/packages/harness/deerflow/sp/report/packets.py)、[来源表装配](../../backend/packages/harness/deerflow/sp/report/assembly.py)。
