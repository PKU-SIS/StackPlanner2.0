# 单题七轮 DeepSeek 试验单：总上限50元（未启动）

用户已认可50元以内的DeepSeek预算。本次只完成最后离线审查、修复和冻结，不把预算认可当成付费启动指令。
选 `library-rag` 一题、七轮、原生一万档；不扩大为多题或新批次，不重跑历史单轮稳定性试验。

## 费用与模型

| 项目 | 本次冻结值 |
|---|---|
| 用户认可的整例总费用上限 | 50元人民币，包含首轮及全部后续轮 |
| 程序调用额度 | 48元；额外2元留作余量，不发放给模型或工具 |
| 模型 | 延续 `deepseek-flash`，不切换 Pro/Qwen；实际响应名另行记录 |
| 单次输入预留上界 | 1,048,576 token；按文档1M向上取整的保守程序值 |
| 整例输入 / 输出上限 | 15,000,000 / 1,000,000 token；缓存、reasoning子集不重复相加 |
| 直接Search/Fetch尝试 | 最多2000次，全部按可能成功收费预留 |
| 模型HTTP尝试 | 最多600次，每次SDK重试也受预留约束 |
| 时限 | 单轮1500秒、整例7200秒、无进度300秒；外层自动重启0次 |
| 账单币种 | 尚未核对真实账户；人民币费率仅适用于对应账户口径 |

2026-11-22重新核对官方文档：Flash上下文1M；CNY高峰价输入未缓存2元/百万、缓存0.04元/百万、输出8元/百万。
保守全未缓存曝光为15M输入30元 + 1M输出8元 + 2000次工具10元 = 48元。
这是一组同时生效的上界，不是预期消费或保证七轮必能完成。费用或token额度不足，停止本例并保留已完成版本。
单次输入按整个1M级上界预留，再加当前角色输出上界；不依赖字符估算。11个同时在途、均按Outline最大输出32768预留的压力场景为25.952256元；任意更多并发仍由同一guard逐次核验，不把11当原生调度配额。
[官方模型和价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)、[直接工具计费](https://api-docs.deepseek.com/zh-cn/guides/search/)。

包含Central、GitAgent、Outline、Researcher、Writer、memory_recaller、后台memory_updater和直接检索。
不另加付费RACE/FACT评分。本例不新增服务端搜索、OCR、图像或语音收费入口。
原生角色输出、思考模式、SDK重试和调度保持原配置；只有独立实验外层额度改变。
计费政策、模型别名、未知收据或实际超预留发生变化时停止，保留实际数值；2元余量不是提供方硬限额保证。

## 七轮与验收

原始请求仍取冻结套件 `multiturn-evaluation-20261122/requests.json` 的 `library-rag`；不改题面。
`evaluator_checks.json` 和 `SEMANTIC_REVIEW.md` 仅给验收侧，不进入模型输入、工具任务或记忆。

| 轮 | 用户目标 | 必须区分的结果 |
|---|---|---|
| 1 | 新建高校图书馆RAG中文报告 | 原首轮路径，无GitAgent；原生9000–11000及证据/引用/哈希验收 |
| 2 | 局部增加更新频率比较 | 首轮全部文件SHA不变；完整需求继承；未受影响canonical草稿SHA保持 |
| 3 | 增加权限表，保留频率比较 | 基线为第2版；保留前轮有效要求，允许实际大纲决定节点 |
| 4 | 从第1版修改：撤销频率比较、保留权限表 | 工作基线和当前有效需求分别检查；对比基线和旧head两份差异 |
| 5 | 只读解释当前权限表来源 | 独立归档；Researcher/Writer均0调用，不新建native delivery |
| 6 | ExactReuse第2版 | 独立快照；不研究、不写作；active指向第2版，accepted head不伪造推进 |
| 7 | 从恢复后的第2版澄清运维风险 | 不复活第三轮表；继承正确有效需求；其他canonical草稿保持 |

每个改稿轮必须经过独立副本上的原生resume和交付验收；prepare、executor完成和文本“已改好”都不算交付。
每轮记录原始query、派生完整需求及来源、基线/head/active、全部快照SHA、canonical差异、原生交付状态、HTTP尝试和费用数值。
无法交付、需要澄清、取消或失败后停止后续依赖轮次，不能把缺失版本当已存在继续跑。
冻结问题若与实际首稿冲突，记录并停止；不悄改题面、不降低篇幅门槛、不强制重写已满足的内容。
原生验收通过不等于报告事实正确。事实与来源语义另外审阅，不冒充RACE/FACT成绩。

## 真实传输接入审查

目前 `OfflinePilotWorker.from_preflight` 已能从校验过的包读取48元guard、上下文上界和整例计数；仍只允许MockTransport。
真实SDK同步/异步、原生isolated executor、后台记忆Timer和外层监督使用同一数值链。gzip流式审查发现压缩原始字节与SDK解码字节不一致，已补独立解码；不共享或消耗SDK decoder状态。

| 接入点 | 审查结论 |
|---|---|
| 凭据与环境 | 将来只允许独立worker从已授权的环境变量读取；不复制旧.env，不输出Authorization。当前未加载凭据。 |
| HTTP边界 | 仅官方HTTPS的三个POST端点；拒绝任意URL/查询/重定向。预算在SDK实际send前核验。当前真实transport被拒绝。 |
| 流式与重试 | 断流/缺失usage/坏格式/未知模型保留预留并阻止新HTTP；完整SSE含DONE才结算。SDK重试数不增加。 |
| 压缩响应 | 使用独立HTTPX解码器读取收据；同步/异步gzip夹具通过，模型收到的流不改。 |
| 后台线程 | 每轮安装记忆回调并排空，之后才能换轮；整例guard和计数不重置。 |
| 监督与故障 | 父进程单调时钟监督并落盘；杀停后排空管道，已知费用/未知预留均保留，不自动重启。 |
| 首轮与服务 | 实验脚本不进入默认runtime，不改旧Demo、服务、默认Skill或首轮参数。 |

真实运行入口尚未开放。下一阶段若明确批准启动，应单独实现并审查官方端点的受限真实传输，不可简单删除socket禁用或放开任意transport。
新入口必须复用同一预留/收据/回调代码，并把内层代理/重定向/底层transport重试一并约束；原生guard无法证明账单口径时不得自称50元硬封顶。
真实账户币种和启动时的模型/费率仍需确认。预算认可本身不修改 `api_experiments_authorized=false`。

## 冻结、复验与交付

本次代码、Skills、SDK、公开审查记录及原始七轮请求进入新预检包；旧包保留，不覆盖。
包路径、提交、SHA、测试结果及旧目录保护核验见 `ADMISSION_RESULT.json`。
复验命令只构建或核验，不请求提供方：

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$PWD/backend/packages/harness:$PWD/backend" \
/path/to/python scripts/report_multiturn_preflight.py \
  --budget-cny 50 --admission-review \
  --output .deer-flow/pilot-preflight/a-new-reviewed-version
```

实际运行数据拟单独保存为 `.deer-flow/pilot-live/<fresh-run-id>/`；当前没有创建或启动真实运行。
不推送、合并、部署或重启服务。
