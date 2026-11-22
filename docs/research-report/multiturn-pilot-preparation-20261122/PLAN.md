# 单题七轮 API 前准备（未启动）

本轮完成配置/代码/Skill/依赖快照、数值审计适配及验收预期复核。模型服务 API 未调用。
预检命令只有构建与核验，没有 `--start` 或执行入口；`api_experiments_authorized=false`。
真实 worker 的 HTTP 预留、请求清单对账及回调安装仍是独立启动门，当前不能宣称 API 已就绪。

## 冻结对象与配置

选 `library-rag` 一题、七轮、一万档。模型可见的 `agent-input.json` 只含冻结用户请求，不含验收预期。
原生合同仍为全文估算9000–11000。新 checkout 的 runtime、报告、模型、执行器、配置与工具源码、
八份报告相关 Skill，以及实际 Python/SDK 版本/关键 SDK 源码 SHA 都进入预检包校验。
模型配置和九项工具绑定对白名单历史批次逐项核对；API key 仅记录变量名 `LLM_API_KEY`，不读取其值、不复制旧 `.env`。
历史 HTTP 记录用于核对原端点 `/v1/chat/completions`，不请求服务。SDK 只以无效占位凭据做请求形态序列化，预检本身阻断 socket 连接。

| 角色 | 请求模型名 | max_tokens | 思考模式 |
|---|---|---:|---|
| Central | deepseek-flash | 8192 | 原生开启 |
| GitAgent / perception profile | deepseek-flash | 8192 | 原生继承开启 |
| Outline | deepseek-flash | 32768 | 原生继承开启 |
| Researcher | deepseek-flash | 8192 | 原生继承开启 |
| Writer | deepseek-flash | 8192 | 原生强制关闭 |
| memory_recaller | deepseek-flash | 8192 | 原生继承开启 |
| 后台 memory_updater | deepseek-flash | 8192 | 原生关闭 |

模型 SDK max_retries=2、request_timeout=180；原生其他重试/预算实现不改。
历史配置保留 memory 默认开启及 char 计数，因此费用统计必须包含可能的后台 memory_updater，不能只算五个报告角色。
全局并发6、子代理超时1470秒、Central初始/恢复超时90/60秒、recursion_limit1600与参考配置一致。
只给独立 worker 追加 NO_PROXY，不改机器全局代理或旧 Demo。

官方目前把 `deepseek-flash` 标为 DeepSeek-V4.1-Flash-1017；旧批次响应只记录了别名，不能据此补造其历史底层版本。
请求名、响应报告名、可用指纹及文档日期应分列。权重版本不能通过这个可变别名做不可变固定。
[官方模型与价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)、[思考参数](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/)。

## 预算和停止规则草案

费用选择未收到，当前 `max_cost_cny=null`，预留器拒绝发放调用额度；100元只是待审阅草案，不是授权。
金额和账户账单币种确认后，应生成新版本包，保留旧包。

| 外层边界 | 草案 |
|---|---:|
| 全例输入 token | 20,000,000 |
| 全例输出 token（含计费 reasoning 子集） | 1,000,000 |
| 模型 HTTP 实际尝试数 | 600 |
| 直接 Search/Fetch HTTP 实际尝试数 | 2,000 |
| 每用户轮时限 | 1,500秒 |
| 全例时限 | 7,200秒 |
| 无进度时限 | 300秒 |
| 外层自动重启 | 0 |

这些是实验外层停止边界，不改变角色内部生成预算、调度或 native acceptance。
金额预留必须在实际 HTTP 尝试前完成，并同时计算所有在途请求的 token/费用上界。
未知传输结果保留预留、停止新调用，不把未知当免费；实际超预留也保留已知费用并停止，不能回写成较小数。
Native SDK/中间件的内置重试实现不修改，但每次实际尝试均要获得额度，未知结果会触发外层停止。
时限由 worker 外的单调时钟/进程监督执行，不能只依赖模型或同一阻塞事件循环中的 timeout。

按2026-11-22核对的 CNY 峰值费率：输入未缓存2元/百万、缓存0.04元/百万、输出8元/百万；
直接 Search/Fetch 单次有结果成功请求0.005元。20M输入全未缓存、1M输出、2000次有结果工具请求的费率曝光合计58元，
仅用于审阅上界，不是本例费用预测或账单保证。币种不符时不能默默换算。
价格和模型路由在启动前需再核对。[价格来源](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)

直接工具只有 HTTP200 且返回结果/正文才收费；Search空结果不收费，`count=10`不乘十计费。
按原始 HTTP 收据判断，不能用材料 saved/reused 或工具语义成功代替收费状态。
服务端内置搜索的免费政策不适用于代码中直接访问 `/v1/tools/search`、`fetch` 的客户端调用。
[官方 Search/Fetch 计费规则](https://api-docs.deepseek.com/zh-cn/guides/search/)。

## 用量审计接线与验收

`scripts/report_pilot_audit.py` 提供：

1. `PilotCallback`：Central或后台模型的数值回调；只保存身份与用量，不保存凭据、提示或私有 reasoning 文本。
2. `observe_native_subagents`：仅在实验进程的 context manager 中桥接真实子代理 Collector，保留原有 observer 和 source_run_id；GitAgent由宿主配置/三个版本工具识别，不把所有 perception 都当 GitAgent。
3. `PilotUsageAudit`：按 source_run_id 去重，冲突停机；按角色/轮次列明用量。缓存与 reasoning 都是已有输入/输出的子集，不能再加一遍。
4. `BudgetGuard`：并发安全的发起前预留和按实际结果结算；未知结果不释放额度，不能在事后清空费用。

真实 worker 仍必须在网络层逐次记录脱敏 HTTP 身份/状态、对应 logical source ID，并安装发起前预留。
不能直接拿测试中的模拟 HTTP 清单当真实收据。`reconcile_model_requests` 未核对独立清单时，billing_complete为false、总费用为null。
后台记忆回调/HTTP也需安装并等待结束/对账；未知或未归属收费请求停止该例，不隐藏在总数中。
当前只有适配接口及真实执行器的离线 wiring 测试，没有在任何真实 API worker 上安装或运行。

## 本轮接口补齐

版本检查新增 `tree`、`sources`、`material:<id>` 行读取，全部来自已校验快照；不新增工具名或读任意路径。
来源问答可检查材料原文和原始 source/material/line身份，仍不派 Researcher/Writer。
中枢直接自然语言回答且没有 GitAgent准备/改稿时，宿主也独立存档为 `response_only`；
明确 `native_delivery_created=false`、意图满足未经程序验证，不把文本中的“已改好”当成交付。

## 复验与状态

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$PWD/backend/packages/harness:$PWD/backend" \
/path/to/python scripts/report_multiturn_preflight.py \
  --output .deer-flow/pilot-preflight/a-new-version
# 同一环境只读核验；改变源码/依赖/包内容会拒绝：
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$PWD/backend/packages/harness:$PWD/backend" \
/path/to/python scripts/report_multiturn_preflight.py \
  --output .deer-flow/pilot-preflight/20261122-v4 --verify
```

最终包在新 checkout 的 `.deer-flow/pilot-preflight/20261122-v4`，中间包保留；大块文件使用既有忽略规则，不提交凭据或运行数据。
冻结请求/验收材料字节未变。[逐轮语义复核](SEMANTIC_REVIEW.md)仅验收侧使用。
离线广域回归1073项通过；最终预检17项通过。真实模型理解、事实质量、API worker接线及账单未进行实测。
没有推送、合并、部署或重启旧 Demo。
