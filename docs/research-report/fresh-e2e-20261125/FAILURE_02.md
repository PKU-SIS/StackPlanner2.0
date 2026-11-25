# 第二批：全新首轮与局部扩写交付，强制新检索轮未交付

首轮空会话完整生成 7 个章节草稿，原生 review ready、交付成功，一万档估算总字数 9827（正文 9167），没有导入旧报告。首轮约 479 秒。第二轮复用资料局部扩写成功约 24 秒，head 为 t0002。

第三轮真正调用 Outline、Researcher，完成 4 次搜索和 1 次抓取请求，工作副本来源由 211 增至 251，但 Fetch 收到了 HTTP 404。实验 worker 将这个有明确响应的工具错误升级为 unknown_http_outcome 并全局 abort。其后 Researcher 原始输出和单次纠正输出均是 provider unavailable 的系统兜底文本，原生 missing_citations 正确拒绝。未产生新交付，head/active 保持 t0002；t0001、t0002 原 native 文件与封存 manifest 的 SHA 逐项一致。第四至七轮未提交。

后台第三轮 run 为 error。观察器在 failed turn 上错误调用 manifest verify 导致二次异常；失败轮没有交付 manifest 是正常行为，不是快照损坏。原失败和诊断都保留。

整个第二批已观测费用小计 2.75292480 CNY，新增一笔 Fetch 0.005 CNY 未结算预留，累计已知估算 36.06422248、预留 6.493064。回调中止造成库存不完整，原始 billing_complete=false 不修改。

修复只涉及实验 worker：明确收到的 Search/Fetch HTTP 4xx 返回原生工具处理，保留最大费用预留，不再误中止无关模型调用；未知模型/网络结果仍停止。无需修改 Researcher 提示或降低引用检查。

后续验证从同一空会话生成的已接受 t0002 继续，使用新请求身份重做第三轮，保留失败 t0003，并记录为第三阶段。它不是一次七轮全通过的冻结批次。无需重跑已经成功的首轮。
