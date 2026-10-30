# CLAUSE EVAL3「文本内部」任务：协作者快速开始

DCCv2 已增加内置 `clause` benchmark plugin，用 SP2 审核一篇合同内部的
歧义、不一致、术语错用、缺失和结构问题。CUAD 保留原有任务格式及评分方式；
两个 benchmark 共用推理入口和离线评分入口，各自定义提示词、技能及评测协议。
文档中的 “DCC v3” 指结果与插件协议版本，当前 Git 分支名仍为 `DCCv2`。

这里采用 `intext-yes-whole-document-v1`：从固定上游快照筛选整篇文档，按基础
合同分组划分，1-shot 示例所属合同组只在 train。它是可重建的实验版本，**不是
论文官方测试划分**。SP2 以 `sp2-files` 模式读文件，不能把其结果直接称为论文复现。

| 划分 | 文档数 | 缺陷数 |
|---|---:|---:|
| train | 4,025 | 14,604 |
| validation | 501 | 1,779 |
| test | 500 | 1,809 |

## 1. 获取代码和环境

拉取含本功能的 DCCv2 版本后，进入**仓库根目录**执行下面的命令；所有路径均相对
仓库根目录。需要联网下载数据、Python 依赖和评分编码器，以及一个支持工具调用、
可供 SP2 使用的模型服务。无需启动前端或 Gateway 即可运行 benchmark CLI。

数据下载和转换只需 Python 3.10+ 标准库。下面同时配置推理与评分，使用 Python
3.12 和 Linux/Bash，以 CPU 运行 BERTScore 为例。已有合适环境时可直接激活；不要
复制其他服务器的虚拟环境，也不要引用其绝对 site-packages 路径。

```bash
python3.12 -m venv .benchmark-env/clause
source .benchmark-env/clause/bin/activate
python -m pip install --upgrade pip

# CPU 评分环境；GPU 评分环境应自行选择与机器匹配的 PyTorch wheel。
python -m pip install 'torch==2.6.0' --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e ./backend/packages/harness \
  -r configs/dcc/requirements.txt -r configs/clause/requirements.txt
python -m pip check
```

这是独立环境的安装示例，实际依赖解析和平台适配以本机安装结果为准。保持
`configs/clause/requirements.txt` 中固定的指标库版本；不要通过替换指标版本来
绕过安装问题。SP2 的模型推理服务与本机 CPU 评分编码器是两个独立组件。

## 2. 下载并整理数据

**不需要任何协作者提供合同文件或 66 MB 的运行包。** 仓库提供重建脚本、固定
划分、提示词和校验配置，完整正文、标注和模型不存入 Git。

```bash
python -B scripts/dcc_prepare_clause.py --download \
  --output-dir .benchmark-data/clause \
  --work-dir .benchmark-data/clause-rebuild-cache
```

脚本锁定上游 commit `c81c6a5251771cab3d9f3d081f71c388957cf936`，下载约 127 MiB
原始归档和约 24 MiB NLTK 资源，扫描正文与标注，执行既定筛选/清洗/分组，并逐文件
校验。准备时建议至少预留约 2 GB 临时空间；完整评分编码器另需约 1.4 GB。

成功后得到：

| 路径 | 用途 |
|---|---|
| `.benchmark-data/clause/public/test.jsonl` | 无 GT 的推理任务清单 |
| `.benchmark-data/clause/private/test.jsonl` | 含 GT 与评分元数据的评测清单 |
| `.benchmark-data/clause/public/documents/` | 完整 UTF-8 合同正文 |
| `.benchmark-data/clause/resources/` | NLTK，及下一步准备的编码器 |
| `.benchmark-data/clause/preparation.json` | 版本、校验、划分与重建来源记录 |

下载缓存会复用；每次仍验证内容，输出目录已存在时拒绝覆盖。成功生成的数据可
反复用于不同模型实验，无需每次重新下载。默认 L1；L2 使用 `--prompt l2` 和一个
新输出目录，其中已经包含冻结的训练示例，不需要自行选择 1-shot。

有现成资源时也可使用 `--archive`（旧运行包）、`--ready-dir`（已验证运行目录）
或 `--source-archive`（原始上游归档）。参数和离线资源复用详见
[详细协议与数据准备](clause_benchmark.md#三种已有资源入口)。

## 3. 准备评分资源

```bash
python -B scripts/dcc_prepare_clause_resources.py \
  --resource-root .benchmark-data/clause/resources --download-bert
```

该命令下载并校验固定版本 RoBERTa-large，供 BERTScore 使用。不加 `--download-bert`
时仅检查已有资源；评分过程本身不会偷偷联网补模型。已经准备好的资源可以复用。

## 4. 配置模型并检查

下面的地址、模型名、密钥都是示例，需要换成你自己的服务配置：

```bash
export DCC_MODEL_BASE_URL='http://127.0.0.1:8000/v1'
export DCC_MODEL_NAME='YOUR_MODEL_ID'
export DCC_MODEL_API_KEY='YOUR_API_KEY'

python -B scripts/infer_sp_benchmarks.py --check-config --benchmark clause
```

无需鉴权的自托管服务可把 key 设为非空占位值 `EMPTY`。不要把实际密钥写入 Git。
`--check-config` 只检查配置，不调用模型，不能证明网络连通或模型工具调用能力。

## 5. 运行 test 推理

以下命令会真正调用配置的模型服务并运行 500 个任务；模型费用、耗时和动作预算
取决于服务与参数，执行前应完成服务侧配置。

```bash
python -B scripts/infer_sp_benchmarks.py \
  --tasks .benchmark-data/clause/public/test.jsonl \
  --dataset-source clause-intext-yes-whole-document-v1 \
  --run-dir .benchmark-data/runs/clause-test-l1-run001 \
  --temperature 0 --top-p 1 --max-tokens 8192 --max-actions 20 --timeout 900
```

完整合同以 `document.txt` 放入原生 uploads，agent 使用文件工具读取。公开输入
不注入 GT、单篇缺陷类别、数量或来源。模型最终应交付严格 JSON 数组，每项只有
`text`（逐字引用完整句子）与 `explanation`（英文解释），无发现时为 `[]`。

框架保存原生 FINISH 接受的完整交付，输出 `predictions.jsonl`、推理 manifest、
报告及轨迹；此阶段不计算 benchmark 分数。不要将运行错误改成空答案。

运行目录必须新建；`--resume` 仅恢复未记录任务，已记录失败不会自动重试。
如要重新运行某个实验，使用新的目录。

## 6. 对已有推理结果评分

```bash
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python -B scripts/evaluate_sp_benchmarks.py \
  --tasks .benchmark-data/clause/private/test.jsonl \
  --run-dir .benchmark-data/runs/clause-test-l1-run001 \
  --output-dir .benchmark-data/evaluations/clause-test-l1-eval001 \
  --benchmark clause --options configs/clause/evaluation.example.json
```

该步骤不运行 SP2 或 LLM Judge，计算 Miss、Extra、ROUGE-1/2/L、METEOR 和
BERTScore F1。结果写入评分目录的 `scores.jsonl`（逐文档）和 `report.json`
（汇总、分母、完成状态、实际资源）。原推理结果不会被改写。

默认要求完整固定 split 和有效预测。小规模诊断需要配套子集清单，并显式使用
`--allow-partial`；不能称为完整 test 成绩。显式选择
`configs/clause/evaluation.lexical.example.json` 可省略 BERTScore 做六项诊断，
报告会标记 `metrics_complete=false`。换数据目录时相应调整 tasks 路径及 options
中的 `resource_root`，或复用已有评分资源目录。

## 7. 提交范围与结果解释

可以提交：插件/重建代码、冻结模板、prompt、技能、依赖清单、测试和文档。
不提交：`.benchmark-data/`、`.benchmark-env/`、本机配置、密钥和完整运行轨迹。
本指南把结果也放入已忽略的 `.benchmark-data/`；需要分享实验报告时单独整理。

不要格式化或修改冻结模板；其字节由校验值约束。与模板相关的历史上游脚本仅在
忽略目录中恢复作来源校验，不执行；不要将整个下载缓存或 ready 目录加入 Git。

当前审核证明了数据重建和推理/评分接口的正确衔接，**尚未给出真实 SP2 在该任务上
的正式成绩**。公开输入/标签分离属于数据流约束；默认 host bash 环境不是文件系统
隔离，严格隔离实验应只给推理进程挂载公开数据。

进一步阅读：[详细协议](clause_benchmark.md)、[通用插件接口](dcc_harness.md)、
[数据来源说明](../configs/clause/NOTICE.md)。
