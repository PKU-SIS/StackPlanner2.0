# 单题七轮真实试验（2026-11-22）

用户最新明确要求尽早启动实验，先前“仅离线”的范围已据此更新。本次已启动真实DeepSeek模型与直接Search/Fetch调用；不是合成演练。
独立目录 `.deer-flow/pilot-live/20261122-library-rag-01/`；代码提交 `4bacf44`；冻结包 `.deer-flow/pilot-preflight/20261122-v7-live-entry/`。
原始七轮题面保持 `library-rag`，模型使用已审查的 `deepseek-flash`。未推送、合并、部署或重启服务。

## 启动与账户

- 用户允许从旧目录 `.env` 只读取得 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL_BASIC`。没有复制整个文件，没有打印密钥。
- 官方 `GET /user/balance` 返回200、账户可用、币种CNY；只记录这些结果，不公开余额金额。
- `.env` 默认模型与冻结Flash不同，本例显式延续Flash；没有修改 `.env` 或其他任务默认模型。
- 总预算仍按50元、程序调用额度48元执行。用户愿意增加费用不作为无限额或自动重启授权。
- 模板包保持 `api_experiments_authorized=false`，因为构建包不能授予执行权限；此次真实启动权限单独记录于 `launch.json`，CLI显式 `--start --billing-currency CNY`。

## 入口与传输

`scripts/report_pilot_entry.py` 的默认行为只核验包和脱敏凭据状态，不产生模型调用。
真实启动使用独立spawn进程中的原 `make_sp_agent`、SQLite checkpoint和原生报告工具；每个后续消息显式绑定已交付首版。
没有外置LLM路由或第二个报告业务账本。prepare、原生交付、实验程序验收和事实正确性分别记录。

官方传输和离线MockTransport共用请求前预留、收据、回调、记忆排空和停止代码。仅显式执行许可的实验子类开放真实传输：

- 只允许官方三个HTTPS POST端点；校验该次Authorization绑定，强制不跟随重定向。
- 只接受直接HTTPX transport、TLS证书/主机名校验开启、底层连接重试0；拒绝代理transport和隐藏重试。
- socket仅在已审计send/读流/关闭的短作用域内放行，且只允许本次官方DNS解析所得的公网IP及443端口。其他连接仍被拒绝。
- DNS变化或坏收据按未知停止；不自动重发未知响应，不将未知费用算作0。

冻结包的role_matrix是模型工厂序列化探针，并未穷尽原生角色覆盖。实测Researcher请求含原生 `research_continuation.RESEARCH_OUTPUT_LIMIT=65536`，高于探针的8192。
没有将它改小；该覆盖已存在于冻结的原生代码，真实guard按实际HTTP请求中的max_tokens逐次预留。
整例输出总量和48元费用guard仍共同生效。此前“11路均按Outline最大值”的数值只是特定压力夹具，不应称为所有角色的绝对最大值。

## 验证与结果边界

入口新增10项离线测试通过；入口/预算/审计专用组合66项通过；加入入口后的广域回归1122项通过。
真实运行文件包括：launch、runtime-binding、SQLite checkpoint、数值事件、各轮原生验收结果、usage和supervisor。
运行时原始记录在忽略目录中完整保留；失败也不覆盖、不自动付费重启。

本次已经停止于首轮原生 `blocked_user / ASK_HUMAN`：全文估算11,423超过11,000上限（正文9,926）。
8份章节草稿和候选c1均保留，未创建native delivery；后六轮没有执行。没有自动压缩、放宽篇幅或付费重跑。
83次模型请求、23次直接工具请求均完成对账；输入3,643,657、输出113,646 token，缓存读2,825,728已包含在输入内。
按核验的峰值费率保守上估2.77305512元，未知费用和在途预留均为0。这不是提供方发票实付金额。
监督程序只确认callable返回，其billing_complete保守标志仍为false；实际worker独立HTTP/回调对账为true，两者不可混为一谈。
完整结构化结果和产物manifest SHA见 [试验汇总](MULTITURN_LIVE_PILOT_20261122.json)。
本次不是成功的七轮多轮验收成绩。
事实质量和来源语义必须另审，不能将原生交付或进程结束直接当成多轮验收成绩。

参考：[官方余额接口](https://api-docs.deepseek.com/api/get-user-balance/)、[启动时模型与价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)。
