# FinAuditing 与 RuleArena NBA 评测器

两者均有官方实现，已通过现有 `BenchmarkPlugin` 接到 `evaluate_sp_benchmarks.py`。
评测只读取已保存的 SP 最终交付和宿主侧标签，不重新运行 SP，也不访问 RAGFlow。
交付可来自通过校验的 FINISH，或原生图正常返回的可见文本答案；采集不改变 SP 的决策流程。
现有数据 Release、输入 prompt 和任务文件可直接复用。

## 官方来源与口径

| 数据集 | 接入实现 | 指标与解析 |
| --- | --- | --- |
| FinRE | [官方 Starter Kit](https://github.com/The-FinAI/FinAuditing/blob/ccf99d9b0a1610576f8997fb18b35076c9bbb838/StartKit/evaluateFinRE.ipynb) | 原始标签归一化；Accuracy、Macro Precision／Recall／F1、无效预测数及分类报告 |
| FinSM | [官方 Starter Kit](https://github.com/The-FinAI/FinAuditing/blob/ccf99d9b0a1610576f8997fb18b35076c9bbb838/StartKit/evaluateFinSM.ipynb) | HR、Recall、Macro-F1，分别报告 @1／5／10／20 及均值；保留预测顺序与 top-k 前的重复项 |
| FinMR | [官方 Starter Kit](https://github.com/The-FinAI/FinAuditing/blob/ccf99d9b0a1610576f8997fb18b35076c9bbb838/StartKit/evaluateFinMR.ipynb) | 原始裁判提示词与 A／S／E／C 标签，分别代表正确、结构错误、提取错误、计算错误 |
| NBA | [官方代码](https://github.com/SkyRiver-2000/RuleArena/blob/59f60f6b4eb93a9b6d187ec781349d4f15618903/nba/auto_test.py)与[规则提取器](https://github.com/SkyRiver-2000/RuleArena/blob/59f60f6b4eb93a9b6d187ec781349d4f15618903/nba/micro_evaluation.py) | 原始目标字符串命中 Accuracy；模型提取规则后计算 Problem-Wise／Rule-Wise Precision、Recall，并按难度分别汇总 |

FinAuditing 选用官方面向原始回答 JSONL 的 Starter Kit；另一路 FinBen notebook 面向
lm-evaluation-harness 的结果布局。FinSM 原始标签中的 Python 列表字符串仅在评测加载时用
`ast.literal_eval` 解码成标签数组，保持标签和顺序，原始数据包不变。

FinMR 的官方默认裁判为 `gpt-5-mini`，NBA 提取器为 `gpt-4o-2024-08-06`。本项目示例使用团队
部署的 **`deepseek-v4.1-flash`**：保留官方提示词、schema 和计分公式，报告明确记录为
`official_prompt_custom_judge`／`official_prompt_custom_extractor`，并记录请求模型和服务返回的模型名。
这组结果使用的是自部署裁判，引用论文结果时应同时注明这一设置。

官方细节也原样保留：NBA 先去掉回答中的 `**` 再做大小写敏感的目标子串匹配；不重新选择“最后一个答案”。
规则 ID 沿用原标注与 55 字段提取 schema，不修正其不一致。未定义的规则 Precision／Recall
以 JSON `null` 表示源代码的 NaN，并给出分母。FinMR 裁判无法解析的输出不进入 ACC 分母，
单独报告 Parsing success rate 和失败计数。连接错误、超时或不完整 schema 则记作评测错误。

## 运行

在 SP Python 环境中安装评测依赖，并配置私有 Key 文件：

```bash
python -m pip install -r configs/dcc/evaluation-requirements.txt
export DCC_JUDGE_API_KEY_FILE=/path/to/private/judge.key
```

FinAuditing：

```bash
python scripts/evaluate_sp_benchmarks.py \
  --tasks .benchmark-data/finauditing-v1/bundle/public/tasks.jsonl \
  --run-dir /path/to/finauditing-inference \
  --output-dir /path/to/new-finauditing-evaluation \
  --dataset-plugin dcc_harness.preprocessing.native:make_plugin \
  --options configs/dcc/evaluation/finauditing.example.json
```

NBA（含规则指标）：

```bash
python scripts/evaluate_sp_benchmarks.py \
  --tasks .benchmark-data/rulearena-nba-v1/bundle/public/tasks.jsonl \
  --run-dir /path/to/nba-inference \
  --output-dir /path/to/new-nba-evaluation \
  --dataset-plugin dcc_harness.preprocessing.native:make_inline_plugin \
  --options configs/dcc/evaluation/rulearena-nba.example.json
```

`--options` 中的 `bundle` 指向对应数据包；使用其他安装位置时修改该路径。
`datasets.<名称>.judge`／`rule_extractor` 控制各自的模型、接口和凭据来源。
NBA 仅算 Accuracy 时，删除 `rule_extractor` 配置；无需模型调用，规则指标标为 `not_requested`。
FinMR 必须配置裁判，不会自动替换成自定义数值比较器。
NBA 的 `extraction_attempts` 可设为 1–5，默认 5，与官方驱动的尝试上限一致；离线接入只重试
相同交付上的规则提取，不重新运行 SP 或改写答案。每次失败及最终尝试次数会写入结果和 provenance；
全部失败时保留评测错误。示例为自部署模型预留 8,192 个输出 token，包含可能使用的推理 token。

默认要求任务、交付、公开输入哈希和推理清单完全匹配。推理失败或交付缺失时不输出完整汇总；
诊断部分结果才使用 `--allow-partial`。结果按数据集／子任务分别汇总，标注百分比或 0–1 单位，
同时记录评测样本数、数据包可用样本数、标签哈希、官方源版本、评测代码及裁判配置。
无效但已完成的回答由官方解析器计分，保留无效预测／空预测诊断计数，不悄悄从样本中删除。

## 扩展接口

`configs/dcc/evaluation/adapters.json` 映射数据集名称到 `module:factory`。也可仅在本次
`--options` 中通过 `evaluators` 覆盖或增加注册，无需修改通用 runner：

```json
{
  "dcc_inline": {
    "bundle": "/path/to/bundle",
    "evaluators": {"my_dataset": "my_package.metrics:make_evaluator"},
    "datasets": {"my_dataset": {}}
  }
}
```

工厂返回 `dcc_harness.evaluation.dcc.Evaluator(dataset, version, score, aggregate)`。
`score(sample, private_label, delivery, options, context)` 返回单样本结果；
`aggregate(metrics, options, context)` 汇总该数据集。标签只在评测阶段加载，并与数据包和
公开输入指纹绑定；`PublicTask` 和策略输入保持无标签。

裁判也可配置 `use: "module:factory"`：工厂接收模型配置，返回同步可调用对象；该对象接收
`{"api": "chat|responses", "payload": {...}}`，返回文本或结构化对象。官方提示词和指标可以保留，
仅替换服务接入方式。评测报告会记录自定义实现及来源哈希。

官方函数节选、MIT 许可和来源校验值位于 `dcc_harness/evaluation/dcc_official/`。
只保留经核对的函数、常量或纯计分片段；不执行上游 notebook 的实验单元或完整 API 驱动脚本。
