# ExactReuse修复与独立复测

保留2026-11-22整版复用失败的原始记录，不回写旧head、不重标旧结果。
修复仅涉及已绑定已接受报告的后续轮：中枢在原sp_delegate metadata中声明report_operation（patch/exact_reuse/read_only）。
宿主将声明保存到对应消息的GitAgent attempt，按模式提供终止工具，并校验reuse模式与handoff。
缺省auto保留旧接口兼容性；没有外置LLM路由或新动作枚举，首轮提示/工具/预算不变。

独立Skill明确区分：
- 完整回到旧版并丢弃其后全部变化：ExactReuse；目标旧版本已不含后续变化，不需要prepare。
- 选旧基线但保留部分后来要求：patch，需要完整resolved_query。
- 问答不改报告/active：read_only。

复测从此前已成功的第1–5轮前缀建立全新owner/thread副本；源实验仍保留其失败head=t0006。
真实文件copy和路径/哈希重绑定沿用现有restore，四个已接受版本及只读档案重新封存；导入不调用模型。
继续使用原始requests.json的第6–7轮，不泄漏验收预期。检查第6轮没有Outline/Researcher/Writer和新native delivery，head仍4、active为2；第7轮只改n11、不恢复第三轮清单。
费用从此前累计6.21026880元继续记账，仍设有调用前预留和停止边界。

离线模式契约、隔离导入及现有报告广域回归1130项通过。两份独立Skill验证通过。
2026-11-23复核官方Flash模型与费率未变，继续按原峰值费率保守计价；真实发票金额另计。
来源：https://api-docs.deepseek.com/zh-cn/quick_start/pricing/

严格验收文件只在模型执行后由程序读取。旧试验结果与新复测结果分开保存；不部署、不推送。
