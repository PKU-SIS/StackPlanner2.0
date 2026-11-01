# 预检索 20 条实测结论

**严格三项全通过 13/20，尚未稳定。引用数量已有改善，主要问题转为综述交付格式。**

备份：`6112b60`，未 push。先跑 3 条 smoke，再独立跑固定 20 条（中英文各 10 条，并发 3）。使用 DeepSeek 模型和 Search/Fetch，复用原 Researcher/Skills/Handler；不启动 Outline、Writer 或前端。

| 目标 | 结果 |
| --- | --- |
| 无执行/工具错误 | 18/20；没有执行中断，2 条出现可恢复的 grep 参数错误 |
| 正常 Markdown summary | 15/20；排除原始 JSON、恢复记录与截断替代物 |
| 多来源且编号有效 | 14/20；正常综述自动统计为 23–138 个不同来源，1 条编号越界 |
| 三项同时满足 | 13/20 |

## 暴露的问题

| 样本 | 原因 |
| --- | --- |
| 35、60、77、80 | 长 JSON 中有未转义引号；解析失败后，原始 JSON 被当作研究产物留下 |
| 55 | 返回未按协议包装的正文，进入原始结果恢复；超过 24,000 字符后被截断，不能算完整综述 |
| 1、73 | grep 缺少 description，或将文件路径当作目录；恢复后仍生成了综述 |
| 73 | 出现不存在的 S120（位于弃用材料说明）；m120 实际属于 S119，source/material 仍会混淆 |

**不是没搜到资料。**本轮每条保存 84–215 个来源，实际使用 23–36 次工具，没有触发硬上限。测试显式配置单体 50 次工具、350 图步；1 条在 token 警戒后关闭工具并正常收尾。普通运行默认未改，跨阶段总 200 次预算尚未实现。

下一步优先把综述正文保存与 JSON 元数据交付分开，避免长篇引文都塞进 JSON；再处理编号与工具参数问题。先复测预检索，暂不推进 Outline。另有明显过量采集，后续再优化。

[20 条逐项结果及 summary](../../logs/preresearch-20-v1/REPORT.md) · [博物馆 summary](../../logs/preresearch-20-v1/31/summary.md) · [smoke 记录](../../logs/preresearch-smoke3-v1/REVIEW.md)

指标在整批结束后离线复核：补上文件工具错误、原始 JSON 与截断文本识别；保留 metrics.raw.json 和原始结果，未重跑模型或修改 summary。来源按账本归一化 URL 计，不等于互不重复的文献；未逐条核验事实支撑。

离线相关回归 **435 passed**；改动文件 Ruff 检查与格式检查通过。失败样本的生产交付逻辑尚未修改。

```bash
cd backend
# 不调用 API，重新核查已保存的真实产物
PYTHONPATH=.:packages/harness .venv/bin/python scripts/evaluate_report_preresearch.py \
  --output ../logs/preresearch-20-v1 --audit
```
