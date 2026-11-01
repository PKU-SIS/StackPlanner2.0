# NBA baseline 问题与修复

原实验为 `nba-sp-baseline-deepseek-v3-noninteractive`：216 题，193 题执行完成，
23 题未完成，70 题通过官方完整答案匹配。193 题准确率 36.27%；全部题目中未完成计错为
32.41%。保持该实验及原 Release 不变，修复后的诊断必须使用新目录。

## 原因

- 未完成题中，15 题最后一次 Coder 观察明确缺少未掩盖退出码的执行证据。
  多次使用 `grep ... | head`、`; echo EXIT=...`，会被现有检查拒绝；部分任务还被当成
  软件开发，要求软件测试。另有真实缺失证据、文件缺失、截断等问题，不能强行标为成功。
- 审计计算输出的 `CHECK ... FAIL` 可能表示业务违规，却被通用 Coder 检查当作程序回归。
- 5 题虽然被标记完成，但交付的是规则摘录或脚本。SP 原先在一个专家产生完成的文件后，
  强制下一步 FINISH，混淆了子任务完成与整题完成。
- 58 题的布尔判断一致且正确，但完整答案未匹配。其中 11 题球队错、18 题操作错、
  28 题两者都错、1 题缺少字段。这部分主要是推理／定位问题，不能通过修改评分掩盖。

## 修复

通用 SP 修复不含 NBA 数据或标签：

1. 普通文字分析不再因一个中间文件完成而强制 FINISH。明确请求文件、报告等交付时保留
   原文件收尾；源码修改仍保留独立验证。
2. 显式 `coder_task_kind="data_processing"` 的业务负例不再仅因 CHECK/FAIL 被当成程序失败。
   真实异常、断言、非零退出、掩盖退出码、未执行和未解决 evidence gaps 继续阻止通过。

新的可选执行配置为 `configs/dcc/preprocessing.rulearena-nba.audit-v1.json`，通过既有
prompt 片段注入机制提示：数据分析任务、正确命令调用、读取完整相关规则、逐操作定位，
以及完整的最终答案交付。原官方问题、规则、标签及原 zero-shot 模板不变；不增加 NBA
领域规则或参考答案，不放宽官方 scorer。

```bash
python scripts/dcc_preprocess.py prepare \
  --config configs/dcc/preprocessing.rulearena-nba.audit-v1.json \
  --output .benchmark-data/rulearena-nba-audit-v1
python scripts/dcc_preprocess.py render \
  --config configs/dcc/preprocessing.rulearena-nba.audit-v1.json \
  --bundle .benchmark-data/rulearena-nba-audit-v1
```

独立新实验使用该 bundle 和当前代码，不续写历史推理目录。小样本诊断用于检查执行和
交付，不能代表全量准确率提升；模型的错误判断与定位仍需后续实验验证。

## 验证

新增回归覆盖文字分析的中间文件、业务违规、真实非零退出与异常；现有软件验证、
退出码与安全路径检查继续通过。相关后端测试 587 项、根目录离线测试 291 项通过。
完整后端套件尝试在收集阶段因本地缺少 Blockbuster、AIO Sandbox 等可选依赖中止，
没有将此记作全套通过。新配置已核对全部 216 题的公共样本、规则与答案原值一致。
