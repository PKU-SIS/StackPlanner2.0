# 多轮报告 UI 信息折叠修复

修复 current.json、report_dialogue/index.json 和历史报告路径导致文件预览漏折叠；报告专用文件变化统一收进 View changes。混合代码任务仍展示普通文件预览。

多轮写作流水线按最新 HumanMessage 绑定和当前 run_id 过滤，继承的旧 Writer 不再显示为本轮工作；原始首轮队列不变。子任务折叠时去除已经由 present_files 展示的同路径最终报告卡片，展开任务仍可看证据。已完成的后续轮控制步骤默认折叠，失败卡片仍保留。

验证：15 项相关单元测试通过；4 项 Playwright 浏览器测试通过；pnpm check 通过。真实旧长会话刷新前页面可见文本 103347 字符，修复后 6376 字符，无 pageerror。新版前端已同步到独立 ui-live/20261125-ui-10 镜像并热更新，后端未重启、报告未修改。

运行截图、browser review JSON 保存在独立运行目录，不含密码的结果文档才进入 Git。此次无额外模型调用。
