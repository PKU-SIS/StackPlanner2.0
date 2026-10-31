# RuleArena NBA 数据加载与 SP 接入

NBA Transaction 使用完整场景直接输入模式，不连接 RAGFlow。球队情况、球员情况和操作列表
按官方顺序进入 prompt；完整 CBA 规则保留为文件，由 agent 用原生文件工具读取。

## 数据与任务

固定[官方快照 `59f60f6`](https://github.com/SkyRiver-2000/RuleArena/tree/59f60f6b4eb93a9b6d187ec781349d4f15618903/nba)，
包含 `comp_0/1/2.json`，分别为 **81、89、46 条**，共 **216 条**。三者对应论文的三个难度等级，
不代表训练、验证、测试划分；适配器统一按 benchmark 的 `test` 使用，不额外切分。

场景沿用官方 `build_query_prompt` 的三段格式。历史工资帽、税线、apron 和 2024–25 赛季假设
及任务输出要求来自原始 `prompt_template`，默认 zero-shot；不加入原脚本的一次示例。
货币符号在模板文件中写成 `$$`，渲染后的场景和金额仍是原来的 `$`。

**`answer=True` 表示存在违规。** 官方要求分析后，以 `Answer: False.` 或
`Answer: True. Illegal Operation: X. Problematic Team: Y.` 收尾。原始答案、违规操作／球队和
`relevant_rules` 只进入 `private/gold.jsonl`；计数字段及难度保留在宿主元数据，不注入 prompt。

不总结或改写规则，不归一化金额，不修正规则 ID、标签或缺失工资表。两个相同场景仍保留为
独立样本：216 条记录本地保存为 215 份唯一场景和 1 份完整规则。源文件和 MIT 许可随包保留，
发布前逐条检查场景、规则和标签一致性。

## 下载使用

NBA 数据准备只使用 Python 标准库。在仓库根目录运行；GitHub CLI 已登录时无需重复登录。

```bash
gh auth login
python scripts/dcc_preprocess.py download \
  --release rulearena-nba-v1 --output .benchmark-data/rulearena-nba-v1
export DCC_BUNDLE="$PWD/.benchmark-data/rulearena-nba-v1/bundle"
export DCC_CONFIG="$PWD/.benchmark-data/rulearena-nba-v1/config.json"
python scripts/dcc_preprocess.py status --bundle "$DCC_BUNDLE"
```

[数据 Release](https://github.com/PKU-SIS/StackPlanner2.0/releases/tag/dcc-rulearena-nba-v1)
包含原始 JSON、规则、规范化场景、单独保存的标签、渲染后的任务及相对路径配置。脚本校验附件与
全量文件后安装；也可手动下载后给命令增加 `--archive /path/to/rulearena-nba-v1.tar.gz`。

SP 推理使用 `bundle/public/tasks.jsonl`，注册：

```text
--dataset-plugin dcc_harness.preprocessing.native:make_inline_plugin
```

任务 benchmark 为 `dcc_inline`。插件将完整场景直接保留在原生输入中，只上传规则与中性命名的
公共 context，不注册检索工具，不读取 RAGFlow 配置或 Key。模型与 SP 参数沿用
[通用推理入口](dcc_harness.md)；本次只做数据准备与输入接入，不增加子任务评分器。

## 重建与扩展

下载包中的 `raw/RuleArenaNBA` 就是可重建的数据源。新建输出目录运行：

```bash
python scripts/dcc_preprocess.py prepare --config "$DCC_CONFIG" \
  --output .benchmark-data/rulearena-nba-rebuilt
python scripts/dcc_preprocess.py render --config "$DCC_CONFIG" \
  --bundle .benchmark-data/rulearena-nba-rebuilt
```

示例配置为 `configs/dcc/preprocessing.rulearena-nba.example.json`。`document_mode: "inline"`
是通用能力；加载器和 Release 构建器均通过 `module:function` 注入，数据集 prompt 独立维护。
没有 `index` 步骤。`configs/dcc/sources/rulearena-nba.json` 固定原始文件版本和哈希；上游推理脚本
仅作为来源保存，提取其字符串常量时使用 AST，不执行脚本或调用模型。
