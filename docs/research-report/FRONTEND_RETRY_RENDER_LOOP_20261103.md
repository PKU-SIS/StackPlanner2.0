# 前端重试任务历史导致的 React 更新循环

备份 `2f6f4e9`，未 push。用户提供 Maximum update depth exceeded，Radix compose-refs/button 栈；此前仅检查后端交付状态不足以定位该页面故障。

真实已完成会话 `075a7188-cf3f-4a0a-bcca-d30c258629cf` 的浏览器只读加载稳定复现相同错误。另用模拟后端构造同一 sp_delegate ID 先 failed 后 completed 的历史，新增浏览器回归修改前失败。

原因：MessageList 每次渲染重放两次终态，useUpdateSubtask 每次都设置“渲染后通知”标志；即使最终状态与已提交状态一致，effect 仍 setTasks → 下一次渲染 → 重放历史，造成无限更新。重复 ID 还可能挂载重复 key 的任务卡片，成功重试残留旧错误。

修复只涉及前端：按本轮首次观察到的状态记录通知基线，最终状态无净变化时不通知；完成状态清除旧失败文本；同组任务计数和卡片按 ID 去重。保留实时 steps/backfill 合并及中间任务状态处理，未修改后端调度、模型配置或报告产物。

验证：624 单元测试、34 浏览器回归通过；ESLint/TypeScript/Prettier/diff 检查通过。原真实会话再次只读打开，pageerror 与 console error 均为空，交付内容可见。新增回归覆盖加载及刷新重试历史，原停止任务卡片用例继续通过。没有重跑报告或提交新模型任务。

浏览器运行库仅下载解包在日志测试目录，未修改系统包。服务未重启，Next.js 热更新已加载代码；用户可强制刷新原页面，无需重跑。

证据：`logs/frontend-react-loop-20261103/before-errors.json`、`errors.json`、`e2e-before.log`、`e2e-after.log`、`chat-regression.log`、`unit-full.log`、`check.log`。Reporter 宿主流水线任务卡片缺入口是另一项展示问题，本次未补齐。
