# CLAUSE EVAL3 In-Text：数据格式与评测协议

首次运行请先阅读[协作者快速开始](clause_quickstart.md)。本文解释数据格式、评分协议与高级准备选项；所有命令均从仓库根目录执行，使用已配置好的 Python 环境。

CLAUSE 现为内置 `clause` plugin，与 `cuad` 共用推理、交付收集和离线评分入口，各自拥有输入 contract、技能、答案格式、评分与汇总。它不改动 SP2 的 Central、角色权限、委派或 FINISH。当前接入的是固定版本的 `intext-yes-whole-document-v1` 协议：整篇文档、全部缺陷均为 YES、按基础合同分组划分，**不是论文的官方测试划分**。

## 默认资源布局

以下是运行准备脚本后生成的默认路径；这些目录不随 Git 克隆提供。

| 资源 | 相对路径/数量 |
|---|---|
| 无标签推理清单 | `.benchmark-data/clause/public/{train,validation,test}.jsonl` |
| 仅供评分的清单 | `.benchmark-data/clause/private/{train,validation,test}.jsonl` |
| 中性文件名的公开正文 | `.benchmark-data/clause/public/documents/` |
| NLTK、固定版本 RoBERTa-large | `.benchmark-data/clause/resources/` |
| 独立 CPU 评分环境 | `.benchmark-env/clause/bin/python` |
| train | 4,025 篇、14,604 处缺陷 |
| validation | 501 篇、1,779 处缺陷 |
| test | 500 篇、1,809 处缺陷；CUAD 来源 240 篇，NLI 来源 260 篇 |

数据、模型和 Python 环境均在 Git 忽略目录；小型 prompt、技能、协议、评分依赖清单和准备脚本纳入代码改动。`configs/clause/NOTICE.md` 保留来源、归属和协议差异。

环境安装参见[快速开始](clause_quickstart.md#1-获取代码和环境)。使用独立 Python 3.12 环境安装 harness、通用 benchmark 依赖及 CLAUSE 指标依赖；无需任何其他服务器的 Python 路径。

## 推理和评分

以下是运行模板；交付审核没有执行这些 SP2 推理命令。先配置实际 SP 模型地址和名称：`DCC_MODEL_BASE_URL`、`DCC_MODEL_NAME`、`DCC_MODEL_API_KEY`。在仓库根目录执行：

```bash
# 配置检查，不构建 SP graph、不调用模型。
python -B \
  scripts/infer_sp_benchmarks.py --check-config --benchmark clause

# 第一阶段：向 SP2 提供文件与公开任务说明，收集原生最终交付。
python -B \
  scripts/infer_sp_benchmarks.py \
  --tasks .benchmark-data/clause/public/test.jsonl \
  --dataset-source clause-intext-yes-whole-document-v1 \
  --run-dir .benchmark-data/runs/clause-test-l1-run001 \
  --temperature 0 --top-p 1 --max-tokens 8192 --max-actions 20 --timeout 900

# 第二阶段：对已保存结果计算七项指标，不调用 SP 或 LLM Judge。
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python -B \
  scripts/evaluate_sp_benchmarks.py \
  --tasks .benchmark-data/clause/private/test.jsonl \
  --run-dir .benchmark-data/runs/clause-test-l1-run001 \
  --output-dir .benchmark-data/evaluations/clause-test-l1-eval001 \
  --benchmark clause --options configs/clause/evaluation.example.json
```

模型预算、超时应按实际模型能力设定；参数进入推理 manifest。每次评估使用新输出目录；需要重新推理则使用新运行目录，已有失败不会被自动改成空答案。`scores.jsonl` 包含逐文档指标和匹配组，`report.json` 包含完成状态、按来源/类别汇总、分母和资源 provenance。

CUAD 继续使用原有清单与相同两个入口，答案和评测仍采用 CUAD 协议（包括 AUPR）；不需要 CLAUSE 的 NLTK/编码器。混合清单可同时包含 `benchmark=cuad` 与 `benchmark=clause`，两阶段按每行 benchmark 路由。混合清单中的相对文件路径须相对新的清单位置有效；CLAUSE 同一实验仅允许一个 split、一个 L1/L2 版本。评分用 `--benchmark` 可仅选择一种数据集。

## 模型可见范围与答案

默认 L1；原归档的 L1/L2 prompt 字节原样保留。完整合同进入原生 uploads，模型看到 `/mnt/user-data/uploads/document.txt`，通过原生文件工具按需读取；任务说明要求覆盖整篇合同及远距离定义/引用。不会把全文塞进任务 contract，也不建 RAG。

模型收到固定任务说明、文件列表和公开的 prompt 版本；收不到 GT、来源 CUAD/NLI、单篇缺陷类别/数量、split、基础合同组或含类别的 record ID。公开/私有清单的模型可见输入指纹逐条一致。私有清单保留评分所需的 gold、source、category 和关联身份。

预期最终交付是严格 JSON 数组：

```json
[{"text": "从输入逐字引用的完整英文句子。", "explanation": "English explanation of the internal discrepancy."}]
```

每项只允许 `text`、`explanation`，必须是字符串且 text 非空；`[]` 是合法无发现答案。禁止 Markdown 代码围栏和额外正文，不自动修复格式错误。建议最终 artifact 为 `/mnt/user-data/outputs/clause_answer.json`，通过原生 FINISH 接受后收集完整文件；中央摘要或中间笔记不能替代答案。五类内部缺陷都要检查，不能从文件名推断类别。explanation 保留但不评分。

输入隔离是框架数据流边界；默认允许 host bash 的 LocalSandbox 不是操作系统安全边界。若实验需要严格的标签不可访问性，应将推理运行在只能挂载 public 数据的独立权限/容器环境中，private 数据留在评分端。

## 评测协议与可比性

- GT 的 changed_text / contradicted_text 与预测 text 用固定英文 NLTK Punkt 分句，`strip().lower()` 后比较完整句子。它不是语义匹配。文字被摘要、改写或截掉半句，可能无法对齐。
- 保留归档 GPT 版多对多图对齐、DFS 顺序及拼接规则；命中任一侧即可命中原始缺陷，两侧不会被无条件拼接。`miss=FN/原始缺陷数`，`extra=FP/预测项数`，无预测时 extra=0。
- 对匹配组计算 ROUGE-1/2/L F1、METEOR、BERTScore F1；每层按参考实现保留四位。组均值 → 文档均值 → 来源×类别文档均值 → 每来源五类别等权均值，另给全部文档宏平均。
- 无匹配组的文档 `text_scores=null`，不以零分填充文本相似度，也不从 miss/extra 分母删除。报告同时记录实际分母。
- BERTScore 使用固定 RoBERTa-large revision、17 层、slow tokenizer、idf=false、rescale=false。长匹配组仍受编码器长度限制，报告记录截断组数。完整七项评分需要本地模型；缺资源时失败，不暗中下载或改用其他模型。
- 只想检查六项非 BERT 指标时，显式选择 `configs/clause/evaluation.lexical.example.json`。报告 `metrics_complete=false`，不能作为完整七项成绩。它不需要 `--allow-partial`，因为指标完整性和样本覆盖是两件事。
- 默认要求整个固定 split 的样本 ID/数量。即使推理 manifest 只有若干样本，也不能冒充完整 CLAUSE 测试；必须显式 `--allow-partial` 才能输出子集诊断。错误预测保持错误，不替换成 `[]`。

实验标记为 `sp2-files`：沿用归档 prompt 的任务要求和评分协议，但通过 SP2 contract/文件/多步骤推理运行，不等同于归档 singlecall 的 system/user 消息布局，更不能直接宣称复现论文成绩。

## 协作者从公开上游准备数据

Git 仓库只需要代码和 `configs/clause/source_rebuild/` 下的冻结模板，**不需要**
合同、标注、完整运行归档或其他机器上的中间索引。首次准备时，在仓库根目录执行：

```bash
python -B scripts/dcc_prepare_clause.py --download \
  --output-dir .benchmark-data/clause \
  --work-dir .benchmark-data/clause-rebuild-cache
```

该命令使用 Python 3.10+ 标准库，下载固定 commit 的公开上游归档（约 127 MiB）
及固定 NLTK 压缩资源（约 24 MiB），验证整个上游 Git tree，自行扫描正文/标注，
执行整文档 YES 筛选、清洗和确定性分组划分，然后校验全部 10,092 项运行文件。
确认正文、GT、划分和提示词均与既定版本一致后，才调用现有转换器生成
`public/`、`private/` 和 `preparation.json`。1-shot 来源合同组固定在 train。

下载缓存可复用，每次仍重新验证；准备时建议预留约 2 GB 空间。下载/重建失败时
不会发布半成品输出；已有输出目录不会被覆盖。已有 `.benchmark-data/clause`
时直接复用；如需重新准备或比较结果，请指定另一个新目录。

准备结果仍为 train 4,025、validation 501、test 500 篇。该步骤不会下载被测 LLM
或 BERTScore 权重，不执行 SP2，不调用模型重新生成任何正文或标签。

完整七项评分还需要安装评测依赖，并显式准备编码器：

```bash
python -B scripts/dcc_prepare_clause_resources.py \
  --resource-root .benchmark-data/clause/resources --download-bert
```

不加 `--download-bert` 时仅校验现有资源。BERTScore、NLTK、本机环境和所有生成
数据都保留在 `.benchmark-data/`、`.benchmark-env/` 等 Git 忽略目录中。
代码仓库的 34 份冻结模板约 6.5 MiB；不要将其格式化或手动修改。

### 三种已有资源入口

`--download`、`--archive`、`--ready-dir`、`--source-archive` 四种来源互斥。

```bash
# 兼容原有的筛选后运行归档；保持完整归档 SHA256 校验。
python -B scripts/dcc_prepare_clause.py \
  --archive /path/to/CLAUSE_Eval3_InText_ready.tar.gz \
  --output-dir .benchmark-data/clause-from-release

# 已经重建/解压的标准 ready 目录；严格逐文件验证，不能使用 public/private 目录。
python -B scripts/dcc_prepare_clause.py \
  --ready-dir /path/to/CLAUSE_Eval3_InText_ready \
  --output-dir .benchmark-data/clause-from-ready

# 原始上游归档：仍需扫描、筛选和划分。可复用已验证的 NLTK 资源以完全离线准备。
python -B scripts/dcc_prepare_clause.py \
  --source-archive /path/to/Better-Call-CLAUSE_c81c6a5.tar.gz \
  --resources-from .benchmark-data/clause/resources \
  --output-dir .benchmark-data/clause-from-source
```

`--source-archive` 指原始上游归档，不能填筛选后的 66 MB 运行归档。
`--resources-from` 仅适用于下载/上游重建模式，指向含 `nltk_data/` 的 resources
目录；复用文件也会校验。省略该参数时 NLTK 从缓存复用或联网下载。

目录入口校验固定的清单哈希及每份文件的字节哈希，拒绝遗漏、多余文件和符号
链接；它不依赖不同 Python/zlib 可能产生差异的压缩字节，也不以数量一致代替
内容一致。若需单独保留标准 ready 目录用于核对，可使用：

```bash
python -B -m dcc_harness.datasets.clause_source \
  --work-dir .benchmark-data/clause-rebuild-cache \
  --output .benchmark-data/clause-source-ready
```

L2 使用新的输出目录和 `--prompt l2`，不能与 L1 混跑；公开/私有清单同步切换
到已固定的 L2 提示。评测 options 中的 `resource_root` 相对当前工作目录解析，
可以指向已有验证资源以复用编码器。

标准 ready 目录为了与旧归档逐字节一致，会从上游恢复历史评测源码，其中有
硬编码凭据；这些源码未纳入仓库模板，也不会执行。不要将 ready、下载缓存、
生成的 public/private 或实验输出提交 Git；来源及模板范围说明见
`configs/clause/source_rebuild/README.md`。

## 不运行 SP2 的审核

此部分面向开发者，不是正常跑数据集的必需步骤。测试环境还需安装 `pytest`、`pytest-asyncio`；原运行归档只用于可选的参考对照测试，日常下载、推理和评分不依赖它。

```bash
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 \
DCC_CLAUSE_TEST_ARCHIVE=/path/to/CLAUSE_Eval3_InText_ready.tar.gz \
DCC_CLAUSE_TEST_BERT=1 python -B -m pytest \
  tests/test_clause_plugin.py tests/test_clause_source_rebuild.py \
  tests/test_benchmark_lifecycle.py \
  tests/test_sp_benchmarks.py tests/test_sp_dataset_plugins.py \
  --no-sp-runtime -q -p no:cacheprovider
```

真实 encoder 检查只运行 CPU 文本相似度，不调用生成模型或 SP2；混合数据集交付链使用显式模拟 graph。参考代码对照测试从已校验归档读取原评分代码。测试产生的空预测/固定预测都属于夹具，不是系统在 CLAUSE 上的实测成绩。

重建专项集成检查可设置 `DCC_CLAUSE_REBUILT_READY=/path/to/verified-ready`，
并用 `DCC_CLAUSE_TEST_ARCHIVE` 指向原运行归档以进行逐文件对照。未提供资源时
相关集成用例明确跳过。测试会暂存数据，建议将 pytest 的 `--basetemp` 指向数据盘
上的一个专用于该次测试的新目录。开发审核记录及原始日志保留在维护者本地，不随代码分发。

离线重建专项用例还可设置 `DCC_CLAUSE_SOURCE_ARCHIVE=/path/to/original-upstream.tar.gz`；
它会禁止下载函数，并复用现有 NLTK 验证离线路径。
