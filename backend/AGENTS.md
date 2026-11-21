# DeepSeek unified three-band runtime

The current user authorized consolidating the original approved DS bands. Use `length.contract_for_band` and `evaluate_report_full.py --length-band` for one runtime; band-specific role prompt helpers live in `sp/report/prompts.py` and are selected in `hooks.py`. Keep the native report entry, fixed supplement lifecycle, evidence checks and acceptance. See `../docs/research-report/DS_THREEBANDS.md`. Frozen historical copies under `deliveries/` must not change. Do not add post-generation length processing or conflate old scores with unified-code results.

Historical experiment notes:

用户明确授权四道全新题各一次1万档，case3/17/27/36；沿用上一轮RACE评分。原问题保持不变。最终9000/10000/11000，Outline自选内部正文预算3000–4000，默认3500。节点/论点仅作经验参考，非配额。保留入口修复、一次缺章兜底、原生需求/证据校验与必要内容修复；Writer模型不变，恢复归档1万档compact Writer提示，移除3万专用展开句。不得套用旧固定4000运行器overlay，不新增模型规划、事后压缩、summary、硬截断、长度返修或自动付费重启。逐题生成并评分，评分失败不重发未知响应、不阻断其他题；不按前题结果调参。全部日志、临时、缓存、检查点、结果在本数据盘独立目录local-storage/runs/race；不改只读原仓库或历史结果。Token含缓存输入按万展示，评分另计。不用子代理。
