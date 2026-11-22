# 实验 worker 接线离线验证

本次补齐请求前预留、HTTPX 收据/回调对账、后台记忆与进程外停止；未调用模型或检索服务。
新增 `scripts/report_pilot_worker.py`，仅用于独立实验进程，不被 Gateway、模型工厂或首轮报告默认路径导入。
`OfflinePilotWorker` 只允许有效挂载为 `httpx.MockTransport` 的白名单 POST 请求；真实传输被拒绝。
监督子进程直到退出都阻断 socket 连接，没有启动真实 API 的选项，也不读取或复制 `.env`。

## 已接通的链路

- 在同步/异步 `httpx.Client.send` 前校验端点、模型、输出上限、回调 source ID 和当前轮次，再预留输入/输出/费用。SDK 每次重试会重新经过此门。
- 输入预留使用显式的提供方上下文上界，不用字符数冒充 token 上界。当前预检包将该值和验证状态保留为 null/false；真实调用前必须核实。合成测试使用的1000仅是夹具参数。
- 接收非流式 JSON 或 SSE 最终 usage。正常流式 SDK 可在 DONE 后立即关闭；此时仍结算完整收据。提前关闭、缺失用量、坏格式、未知模型或 HTTP 错误收费状态不明，保留预留并停止。
- 原生 SDK 重试参数不变；未知结果后的重试被门禁挡在传输前。直接 Search/Fetch 的原生重试也受相同限制。尝试数覆盖模型和直接工具，整个案例不按轮清零。
- `PilotCallback` 的 source_run_id 进入独立 HTTPX 收据，核对输入、输出、总量和响应模型；已知缓存量也必须一致。真实原生 GitAgent 子代理在 isolated loop 中通过此链，原 Collector/observer 继续执行。
- 冻结 SDK 丢失 DeepSeek 顶层缓存字段时，保留回调缓存未知和 HTTP 缓存数值两份记录；回调估算按未缓存计费，不修改 SDK 或默认模型行为。HTTP 结算费用与回调保守上估可不同，不伪造一致。
- `run_turn` 包装调用者的原图，提供 Central 数值回调，安装原生角色观察器及 `MemoryUpdater._get_model` 回调。实际 `MemoryUpdateQueue` 的 Timer 调用纳入账目；本轮排空后才允许下一轮，重复轮号被拒绝。它不路由任务、不调用 Writer、不判定报告交付。
- 原生 delivered 发布仍由报告系统验收控制。worker callable 返回、HTTP 收据完整、报告交付和事实质量为四件不同的事。

## 外层停止与日志

`supervise` 使用 spawn 子进程和父进程单调时钟，独立执行整例、每轮、无进度与取消停止。
子进程阻塞不会阻塞父监督；到期先 terminate，必要时 kill，不自动重启。
父进程逐条 fsync 白名单数值事件，终止生产者后排空管道，保留刚发出的预留、结算和未知记录。
已结算超预留仍记录实际费用；未知不释放额度。日志不保存请求正文、响应正文、Authorization 或原始异常文本。
目标函数正常返回也只记录 `worker_callable_completed`，不自动把 `native_delivery_verified` 或 `billing_complete` 改为 true。

代码使用仅限实验进程的可恢复 hooks；进程中只允许一个安装实例。退出恢复正常方法，独立监督子进程的 socket 禁用保持到退出。
真实启动时还需要对提供方非成功状态、真实流式响应、账户币种、上下文上界和收费规则做准入核验；当前代码明确拒绝真实传输。

## 复验

在新 checkout 使用借用的依赖解释器，PYTHONPATH 必须指向本 checkout：

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false \
DEER_FLOW_HOME=/tmp/sp2-pilot-worker-verification \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$PWD/backend/packages/harness:$PWD/backend" \
/path/to/python -m pytest -p no:cacheprovider \
  backend/tests/test_sp_report_pilot_worker.py \
  backend/tests/test_sp_report_pilot_preflight.py -q
```

新 worker 测试覆盖真实 SDK 同步/异步/流式、流中断、并发 source 隔离、真实原生子代理、真实后台记忆 Timer、连续两轮计数与归属、额度拒绝、实际超预留、缺失/冲突用量、直接工具重试抑制、端点/真实传输拒绝、父进程三类时限和取消、数值日志白名单。
其中“真实 SDK/执行器”表示使用实际 Python 代码，HTTP 收据由 MockTransport 合成，不是提供方实测。
这组数值接线测试不替代已存档的完整原生七轮合成报告演练，也不构成真实自然语言或事实质量成绩。

最终测试数量、代码提交、预检包 SHA 和旧工作区保护核验记录见 `WORKER_RESULT.json`、`WORKER_TEST_SUMMARY.txt`。
预算仍未确认，预检包 API 授权为 false。未推送、合并、部署或重启服务。
