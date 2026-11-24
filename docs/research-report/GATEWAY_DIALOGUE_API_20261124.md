# Gateway 报告多轮接口与离线验收

开发分支 `feature/report-multiturn`，独立 checkout
`/data/sp/yzb/StackPlanner2/StackPlanner2.0_Report_multiturn`。
本次基线 `e9a7b51`，不启动服务，不调用付费模型，不推送、合并或部署。

## 正常提交入口

沿用 `POST /api/threads/{thread_id}/runs`（stream/wait 同用 `start_run`）。
客户端在**当轮用户消息**中显式绑定已交付报告，例如：

```json
{
  "input": {
    "messages": [{
      "role": "user",
      "id": "client-message-unique-id",
      "content": "只修改第二章指定的一句话，其他内容保持不变。",
      "additional_kwargs": {
        "report_dialogue": {"enabled": true, "report_id": "r1"}
      }
    }]
  }
}
```

`normalize_input` 已保留原消息 ID、正文和绑定，无需增加一个 LLM 路由。
认证用户身份仍由 Gateway 注入，线程身份来自 URL；不要传入目录、ledger 或版本索引。
开关关闭或尚无已接受基线时保留首轮路径。普通消息不自动绑定历史报告。
已有版本谱系中，可绑定任一已接受 native report ID；执行以程序记录的 active version
为默认基线，由原有中枢选择局部修订或其他操作。

## 只读状态接口

新增 `GET /api/threads/{thread_id}/report-dialogue`，沿用线程读取权限与归属校验。
返回字段：

| 字段 | 含义 |
| --- | --- |
| available | 存在有效首轮交付或已登记的报告谱系 |
| binding | 可供用户显式选择使用的 `{enabled:true, report_id}`，取当前有效版本 |
| head_version_id | 最近新接受的报告版本；整版复用不会推进它 |
| active_version_id | 当前有效版本；整版复用可指向历史版本 |
| versions | 已接受版本 ID、native report ID、基线及 head/active 标记 |
| current_turn | 最新可见用户消息已显式绑定且已有存档时，返回该轮状态；否则 null |

`current_turn` 只含 turn_id、message_id、status、mode、report_id、base_version_id、
`native_delivery_created` 和 `response_only`。

| status | 含义 |
| --- | --- |
| preparing / prepared | 正在准备／独立副本已准备，尚未交付 |
| executing | 副本正在执行，尚未交付 |
| accepted | 新版本已通过原生交付并接受 |
| failed / cancelled | 本轮失败／取消；已接受 head 保留 |
| read_only | 本轮问答已归档，未创建新报告交付 |
| exact_reuse | 已选择一个已接受版本，未创建新报告交付 |

`native_delivery_created` 只对已接受的新增报告轮为 true。
`response_only=true` 表示原有结束钩子仅存档回答，不代表用户改稿意图已完成。
归档状态不代表事实正确性或独立语义审查通过。
`current_turn=null` 也不等于运行空闲：中枢可能尚未创建本轮记录；运行状态仍读 runs API。

此接口只是读取程序已记录的状态，不是额外的发布或验收关卡。
初次读取已交付报告时验证原生绑定但**不调用 session、不懒封存、不 fork**。
已有谱系读取版本元数据，不反复全量哈希每份快照；执行与恢复仍走原有完整校验。
不返回 host 路径、用户原文、派生需求或原始错误诊断；内部状态不可读时返回通用 409。
原 `/report-view`、Writer pipeline 和首轮状态协议保持原含义。客户端需明确选择多轮模式，
不能用“存在报告”自动给所有后续消息加绑定。

## 离线验收与边界

`backend/tests/test_report_dialogue_api.py` 新增 12 项：

- 使用真实 FastAPI 路由、原生报告 ledger、独立复制及验收工具；无供应商调用。
- 首轮已交付时可获取绑定，但 GET 前后全部文件 SHA 相同且不生成版本索引。
- 无报告时 available=false，不创建版本目录。
- prepared/executing/failed/cancelled/read_only/exact_reuse 分开呈现，均不谎称新交付。
- 实际局部保存与原生 Finish 后 accepted；恢复原版后 active 回退而 head 保留；
  再做只读问答仍不推进版本，首轮文件 SHA 不变。
- 普通下一条消息不继承上一轮状态；线程归属拒绝和跨会话索引拒绝；响应不泄露诊断或 host 路径。
- 真实 POST → start_run → 后台 worker 参数保留消息 ID、中文原文和报告绑定；
  伪造 user_id/thread_id 不能替换认证身份和 URL 线程。

POST 测试在 worker 执行边界使用离线桩；编辑/撤销/只读组合测试由程序调用真实工具。
它们验证 HTTP 协议、状态映射和原生数据隔离，不等同于一条真实模型驱动的线上会话。
此前付费实验与生产图模拟的证据仍分别保留，不重跑旧批次，不改旧成绩。

## 最小网页入口

普通聊天页在确有已交付报告时显示“继续修改这份报告”，默认不勾选。
勾选后，该页正常提交会在消息 additional_kwargs 附加已接受报告绑定，保留文件等原始字段。
选择以 threadId 隔离，切到另一会话不会继承绑定；刷新默认关闭。
组件显示 active version 的顺序号及本轮状态，失败不显示成已交付，执行中不显示旧完成状态。
仅在运行期间轮询，结束或消息变化后重新读取；Mock、新会话、静态站点不请求这个接口。
第一版没有增加复杂版本树、网页切版按钮或独立 Agent；用户仍可在消息中提出恢复/撤销要求。

前端新增 4 项测试覆盖默认关闭、附件保留、跨线程隔离、只读/失败与交付区分、
无报告不展示、恢复后的 active 序号和执行中旧状态隐藏。
测试使用真实组件静态渲染和纯函数；未启动浏览器或服务，不能称为浏览器端到端验收。

## 本轮结果

- 后端：944 passed（报告、多轮、task memory、动作系统/循环、Gateway services、threads router 和新 HTTP 用例）。
- 前端：8 passed（新增 4 项和原 report-status 4 项）；`pnpm check` 全量 ESLint 与 TypeScript 检查通过。
- 新后端文件及 router 的 Ruff lint/format、改动 diff 空白检查通过。
- 实际 Python 导入路径均在本独立 checkout：`backend/app/gateway/routers/threads.py` 与
  `backend/packages/harness/deerflow/sp/report/turns/presentation.py`。
- Python 仅借用旧虚拟环境依赖，PYTHONPATH 显式设为新 checkout 的 `backend/packages/harness:backend`；
  测试状态在 `/tmp/sp2-api-entry-test` 与 pytest 临时目录。
- 前端 node_modules 为旧 checkout 依赖目录的只读使用链接；Rstest 的源码别名及工作目录指向新 checkout。
- 本轮未调用模型，新增模型 token/费用为 0；没有重跑历史付费实验。
- 旧工作区仍仅原有 README、backend/AGENTS、invalid_tool_call_repair_middleware 及其测试四处改动。

后端回归命令（先在 repo root 设置上述 PYTHONPATH 与隔离 DEER_FLOW_HOME）：

```bash
python -m pytest backend/tests/test_sp_report*.py \
  backend/tests/test_sp_task_memory_middleware.py \
  backend/tests/test_sp_action_system.py backend/tests/test_sp_action_loop.py \
  backend/tests/test_report_dialogue_api.py \
  backend/tests/test_gateway_services.py backend/tests/test_threads_router.py -q --disable-warnings
```

前端命令（新 checkout 的 frontend 目录）：

```bash
pnpm exec rstest run tests/unit/core/threads/report-dialogue.test.ts \
  tests/unit/components/workspace/report-dialogue.test.ts \
  tests/unit/components/workspace/report-status.test.ts
pnpm check
```


## 后续真实 UI 验收

[真实浏览器、模型实验与手测入口](ui-acceptance-20261124/RESULT.md) 已补齐。
显式绑定消息现在在客户端分配一次 UUID，网络重发保留同一来源；默认首轮/普通消息结构不变。
完整报告文件列表按需展开。该记录包含中间失败及最终连续三轮结果，不能只引用完成率而省略工具误派工。
