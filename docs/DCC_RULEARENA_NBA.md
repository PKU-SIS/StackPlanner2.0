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
[通用推理入口](dcc_harness.md)。离线评测已接入官方 Accuracy 和模型提取的规则 Precision／Recall，
配置方式见 [DCC 评测器说明](DCC_EVALUATION.md)。

## 内部训练/测试划分（可选）

项目内部划分固定在 [`rulearena-nba-80-20-v1.json`](../configs/dcc/splits/rulearena-nba-80-20-v1.json)，
采用约 **80% 训练 / 20% 测试**，seed 为 **42**。这是后续训练评测使用的自定义划分；
原始 Release、默认 loader 和本次 216 题全量 baseline 继续使用原有协议。

| 难度 | 训练集 | 测试集 |
| --- | ---: | ---: |
| comp_0 | 65 | 16 |
| comp_1 | 71 | 18 |
| comp_2 | 37 | 9 |
| 合计 | **173** | **43** |

相同球队、球员背景的题目整组分配，即使操作不同也不跨集合。216 题形成 188 个背景组，
训练 151 组、测试 37 组；完整重复场景自然也在同一侧。组内保留所有原始样本，
再按难度及违规/合规标签尽量保持比例；分配不使用任何 SP 实验输出或成绩。
难度 2 只有一条合规样本，本版本将它保留在训练集，测试集该难度的 9 题均为违规题。

下载原始 Release 后，用下面的命令生成独立数据包；不需要模型或 RAGFlow：

```bash
python scripts/dcc_split.py export \
  --bundle .benchmark-data/rulearena-nba-v1/bundle \
  --definition configs/dcc/splits/rulearena-nba-80-20-v1.json \
  --output .benchmark-data/rulearena-nba-80-20-v1
```

生成的 `train/` 与 `test/` 都是完整、独立的 bundle，各有 `public/tasks.jsonl` 和
`private/gold.jsonl`。例如测试时把 `DCC_BUNDLE` 和评测 options 中的 `bundle` 指向
`.benchmark-data/rulearena-nba-80-20-v1/test`，任务路径使用该目录的 `public/tasks.jsonl`。
训练侧只包含训练样本的文档和标签；规则、问题、prompt、答案不改写，公开输入指纹与原包一致。
只有宿主 `split` 和 `group_id` 随划分设置，分组信息不进入 agent prompt。

导出会校验源数据指纹及固定分配，拒绝覆盖已有目录或写入源 bundle。
后续需要新版本时可用 `dcc_split.py create`，通过 `--seed`、`--train-fraction` 或
可信的 `--group-key module:function`／`--stratum-key module:function` 扩展，保留独立版本。
当前导出入口支持 inline 文档模式。

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
