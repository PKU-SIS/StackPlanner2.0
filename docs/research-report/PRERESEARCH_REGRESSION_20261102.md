# 预检索真实回归：3 条 smoke

**3/3 通过，一次执行即正常落盘，无工具错误、预算中断或格式修复。**本轮没有修改代码，复测的是补研交付、Outline 收尾修改后的当前工作区。

| ID／主题 | 正文独立引用来源 | 保存材料 | 综述字符数 | 工具调用 |
| --- | --- | --- | --- | --- |
| 31 博物馆教育 | 106 | 169 | 21,227 | 31 |
| 11 缓蚀剂光谱 | 54 | 128 | 17,551 | 30 |
| 97 支线机场经济影响 | 49 | 93 | 25,469 | 30 |

三份均为正常 Markdown 综述，全部引用均有已保存材料，材料哈希与账本一致；原生 summary 与导出的 summary 全文一致。机场样本超过旧 24k 字符恢复阈值，仍完整保存，没有截断标记。运行期间代码哈希未变。

沿用既有独立阶段入口与 DeepSeek API，3 并发、每条 50 次工具／350 图步。没有复用旧综述，没有启动 Outline、Writer 或终稿；旧 20 条产物保持不动。引用数统计来源身份，不代替逐条事实核验，也不代表全链路已经验证。

[逐项结果](../../logs/preresearch-regression-smoke3-20261102/REPORT.md) · [完整性检查](../../logs/preresearch-regression-smoke3-20261102/integrity-audit.json)

综述：[博物馆](../../logs/preresearch-regression-smoke3-20261102/31/summary.md) · [缓蚀剂](../../logs/preresearch-regression-smoke3-20261102/11/summary.md) · [支线机场](../../logs/preresearch-regression-smoke3-20261102/97/summary.md)
