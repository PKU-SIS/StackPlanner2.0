# 宿主 Writer 流水线前端展示

备份 `c40eeaa`，未 push。Writer 仍由原生 DelegateHandler 执行；宿主调度不会生成 Central 的 sp_delegate 消息。前端此前只按消息中的工具调用挂载卡片，所以事件已收到也没有显示入口。

新增章节写作面板：显示章节标题、待写/在写/完成/失败/停止、进度及实际并发上限。直接使用已有任务上下文的实时步骤；报告视图新增只读 writer_pipeline 快照，提供当前章节状态、任务/运行 ID 和已接受的虚拟草稿路径。未改调度规则、工具或 Agent 提示词。

已有 sp_delegate 卡片按 ID（含 spact_ 别名）去重；仅展示当前报告/会话范围。快照在 effect 中幂等合并，保留实时步骤，不在 render 中写状态。较长的待写/完成队列可展开，正在写或失败的章节直接显示。刷新后根据账本恢复，不依赖仍在内存中的旧事件；历史工具步骤能否展开仍取决于原有事件存储是否保留。

验证：后端 7675 passed、22 skipped；前端 626 单元测试、35 浏览器回归通过，ESLint/TypeScript/Prettier/diff 检查通过。新增浏览器用例覆盖无 sp_delegate 消息时两个 Writer 同时显示、待写章节、完成后刷新恢复、切换会话无串入，并回归之前的无限更新问题。

实际旧会话只读验证：20 个章节全部恢复为 completed，可展开队列和任务详情；刷新仍为 20，pageerror/console error 均为空。没有提交新 case 或调用模型。

确认无活动任务后已重启 Gateway 加载只读字段，保留账号/数据；Next.js 已热更新。入口仍为 http://123.59.6.244:10081，用户强制刷新即可。证据在 `logs/frontend-react-loop-20261103/pipeline-*`，重启前事件保存在 `logs/frontend-live-20261102/run-events-before-pipeline-ui-restart.json`。

展示时机：预检索、大纲和补研期间不展示全待写队列；首个 Writer 获得实际任务 ID 后展示流水线，同时显示其余待写节点。写作结束后保留历史状态，刷新仍可恢复。该调整仅修改前端筛选，无需重启 Gateway。

展示时机调整验证：627 个前端单元测试、35 个浏览器回归及 pnpm check 通过；浏览器覆盖未派发时隐藏、开始写作后显示、交付后刷新保留。修改前文件备份于 `/tmp/sp2-pipeline-visibility-before-20261103.tar.gz`。未启动真实模型实验。
