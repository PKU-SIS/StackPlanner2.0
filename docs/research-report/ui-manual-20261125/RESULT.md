# 最新版独立 UI 试用准备（2026-11-25）

运行源码基线 8448a80，包含 07d9efe 修复。后端模块 __file__ 已核对来自独立 checkout。前端是该 checkout 的独立源文件副本，单独编译缓存，复用已有 node_modules，不影响旧 UI。

- URL：http://123.59.6.244:3325/workspace/chats/ui-report-latest-20261125
- 独立运行目录：.deer-flow/ui-live/20261125-ui-10/
- 独立 tmux socket：sp2-multiturn-ui10（gateway、frontend）；独立 nginx pid 文件位于运行目录。
- 网关仅监听 127.0.0.1:8425；前端仅监听 127.0.0.1:3425；公网 3325 代理采用 HTTP Basic 和当前 SSH 来源 IP 白名单，未认证请求返回 401。访问密码仅保存权限 600 的运行文件，不进入文档或 Git。
- 已通过服务器 IP 实际浏览器发出一次只读单元格问题；原文回答正确，报告 head 和 active 均保持 t0007，全部初始 native 文件 SHA 不变，五个已接受快照 verify 通过。
- 浏览器 pageerror 为空。测试用例及合成上传材料下载均为 200，字节与源文件一致。
- 本次含后台记忆总用量 101700 tokens：输入 99625，输出 2075。估算 0.06933608 CNY，非供应商账单。UI 主会话计数可能不含后台记忆。
- 已知累计费用 30.52851416 CNY，历史未知预留 6.488064 CNY，最大已占风险 37.01657816 CNY；未知请求未重放。后续用户试用费用继续记入本实例 usage.json 与 numeric-events.jsonl。
- 原 Demo、旧会话、旧工作区均未重启或修改；未推送、合并或部署。

[测试用例](TEST_CASES.md) 包括八个连续步骤和两个可选步骤。[上传材料](pilot-material.md) 为明确标注的合成内部建议。

认证页面及下载已从服务器本机用公网 IP 验证；用户笔记本的外部网络可达性仍需实际打开确认。若 403，核对来源 IP；若连接超时，核对网络及云安全组，不据此判报告功能失败。

自动审批拒绝了仅靠 IP 白名单的无密码公网方案，已改为密码加 IP 限制并获准启动。另一个可选的完整初始报告公网下载被拒绝，已取消，未复制到 public 目录。

这次只读烟测用于验证 UI 提交和归档链路，不替代用户后续自由表达测试，不扩充改稿成功率。严格删段附带改过渡语、事实引用核对等已知限制仍保留。

详细静态结果见 [RESULT.json](RESULT.json)。运行目录还保存 source-binding.json、http-smoke.json、readonly-browser-smoke.json、readonly-ready.png、acceptance.json。
