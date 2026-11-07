# CLAUSE EVAL3 文本内部审核 · 文件模式快速开始

本指南使用 DCCv2 内置插件 `clause`。SP2 把一份完整合同放在该样本专属的 `/mnt/user-data/uploads/document.txt`，由 Central 自主安排审核。文件模式无需 RAGFlow 服务或密钥。

## 任务与划分

任务是查找同一份合同中的歧义、不一致、术语错用、缺失和结构问题。当前使用 `intext-yes-whole-document-v1`，从固定上游数据筛选 5,026 份整篇文档，按基础合同分组，近似按 8∶1∶1 划分。这是本项目用于比较方法的实验划分。引用成绩时应注明划分版本。[数据协议与重建说明](clause_benchmark.md)

| 划分 | 合同数 | 标注缺陷数 |
|---|---:|---:|
| train | 4,025 | 14,604 |
| validation | 501 | 1,779 |
| test | 500 | 1,809 |

L1 为 0-shot，L2 为 1-shot。两级使用同一批 500 份测试合同和标注，L2 的固定示例来自训练组。推理清单只提供公开输入，标注和逐样本缺陷类别、数量保留给评分程序。Release 同时提供文件与 RAGFlow 两种清单，下面的命令只选择文件清单。

## 相对远程基点 `052aa77` 的变化

| 部分 | 文件模式当前行为 |
|---|---|
| 自主委派 | 文件插件保留公共 JSON schema，由 Central 选择角色、委派次数和是否补查。格式恢复将缺口交还 Central。 |
| 读取与审核 | 子代理可读取带行号的完整或指定范围原文，使用 `grep` 检索，并可用 `clause_check_passages` 核对引文。`text` 保留紧邻的原文条号；含义和效力相同的大小写或称呼差异可忽略。 |
| 文件登记与 FINISH | 插件核对本次动作的文件归属、内容摘要和 schema，把有效最终文件登记为 `generated_file`。完成原文读取且已有合法文件时，子代理可以只回报完成状态和剩余缺口。原生 FINISH 必须选中本次运行登记的文件。 |
| 监控与评分 | 推理、导出和离线评测使用相同的文件校验。超时、未完成、错误的产物引用和无效 JSON 均保留失败状态。 |
| 兼容范围 | 合并保留了远程的原生工具和恢复改动。此处的自主委派与文件回执适用于 `clause` 文件模式；`clause_ragflow` 使用自己的检索工具和 skill。 |

这次更新复用现有 Release。正文、标注、划分、L1/L2 基准提示和 RAGFlow 映射均沿用 `dcc-clause-eval3-v1`。[共享数据包](https://github.com/PKU-SIS/StackPlanner2.0/releases/tag/dcc-clause-eval3-v1)

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

## 2. 安装现有 Release

在仓库根目录使用有仓库访问权限的 GitHub CLI 下载。安装脚本按已提交的描述文件核验附件摘要，并拒绝覆盖已有目录。已安装同版本的协作者直接设置 `CLAUSE_TEST` 即可。

```bash
python scripts/dcc_clause_release.py download \
  --descriptor configs/dcc/releases/clause-eval3-v1.json \
  --root .benchmark-data/clause-eval3-v1
export CLAUSE_TEST="$PWD/.benchmark-data/clause-eval3-v1/test"
```

文件推理清单是 `public/clause-files-l1-tasks.jsonl`、`public/clause-files-l2-tasks.jsonl`，评分使用 `private/` 下同名清单。保留安装目录的结构，以便相对路径找到合同。若要从固定上游归档再现筛选和划分，可使用重建入口。[源数据准备](clause_benchmark.md)

## 3. 配置模型

例如在推理服务器本机调用兼容 OpenAI 的 Qwen/vLLM。将 URL 和模型 ID 替换为自己的服务；需要鉴权的服务从本地凭据注入 `DCC_MODEL_API_KEY`。下面从仓库模板生成本地运行配置，使 Reporter、Perception 和 Coder 的子代理图步数上限与此次文件模式运行一致。128 表示 LangGraph 的执行步数上限，单次模型回复、工具和中间件可涉及多个图步。

```bash
export DCC_MODEL_BASE_URL='http://127.0.0.1:8075/v1'
export DCC_MODEL_NAME='qwen3.8-27b'
export DCC_MODEL_API_KEY='EMPTY'
export CLAUSE_CONFIG="$PWD/.benchmark-data/clause-qwen-files.yaml"

python - <<'PYCONFIG'
import os
from pathlib import Path
import yaml

config = yaml.safe_load(Path("configs/clause/stackplanner-qwen.example.yaml").read_text())
for role in ("sp-reporter", "sp-perception", "sp-coder"):
    config["subagents"]["agents"].setdefault(role, {})["max_turns"] = 128
Path(os.environ["CLAUSE_CONFIG"]).write_text(yaml.safe_dump(config, sort_keys=False))
PYCONFIG
python scripts/infer_sp_benchmarks.py --check-config --benchmark clause --config "$CLAUSE_CONFIG"
```

## 4. 分别推理 L1 和 L2

为每次正式实验选一个新的 `RUN_ROOT`。下面按等级顺序运行，每级读入 500 份合同。子代理并发上限是单份合同内的配置。推理清单中不含 GT。全量推理若有失败，命令会返回 2；保留已有逐条记录和失败状态，正常处理另一级。

```bash
export RUN_ROOT="$PWD/.benchmark-data/runs/clause-files-001"
mkdir -p "$RUN_ROOT"
for level in l1 l2; do
  python -B scripts/infer_sp_benchmarks.py \
    --benchmark clause --dataset-source clause-intext-yes-whole-document-v1 \
    --config "$CLAUSE_CONFIG" --tasks "$CLAUSE_TEST/public/clause-files-${level}-tasks.jsonl" \
    --run-dir "$RUN_ROOT/$level/inference" --recover-qwen-tool-calls \
    --temperature 0.2 --top-p 0.95 --seed 42 --max-tokens 8192 \
    --max-actions 20 --max-concurrent-subagents 3 --timeout 2400 \
    --central-timeout 180 --central-retry-timeout 180
done
```

最终文件是 UTF-8 JSON 数组，每项只有 `text`（含条号的逐字引文）和 `explanation`（英文解释）。审核完成且无发现时可以交付 `[]`。每级的 `inference/predictions.jsonl` 保存成功和失败记录。可用以下命令核对一个等级并导出通过校验的 JSON 文件：

```bash
python scripts/monitor_clause_run.py "$RUN_ROOT/l1/inference" --export-dir "$RUN_ROOT/l1/answers"
```

## 5. 对已有推理结果评分

评分使用固定的 NLTK 资源和 RoBERTa-large 编码器，可跨实验复用。现有 Release 提供合同和清单，评分资源单独准备。若已在 `.benchmark-data/clause/resources` 准备完毕，直接运行评分即可。在仅安装了 Release 的新环境中，下面的命令复用仓库的下载和摘要校验逻辑，单独准备评分资源。

```bash
mkdir -p .benchmark-data/clause/resources
cp configs/clause/source_rebuild/templates/resources/nltk_resources_provenance.json .benchmark-data/clause/resources/
python - <<'PYNLTK'
from pathlib import Path
from dcc_harness.datasets.clause_source import prepare_nltk

prepare_nltk(Path(".benchmark-data/clause"), Path(".benchmark-data/clause-nltk-cache"))
PYNLTK
python scripts/dcc_prepare_clause_resources.py --resource-root .benchmark-data/clause/resources --download-bert
```

```bash
for level in l1 l2; do
  OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python -B scripts/evaluate_sp_benchmarks.py \
    --benchmark clause --tasks "$CLAUSE_TEST/private/clause-files-${level}-tasks.jsonl" \
    --run-dir "$RUN_ROOT/$level/inference" --output-dir "$RUN_ROOT/$level/evaluation" \
    --options configs/clause/evaluation.example.json
done
```

默认要求完整划分和有效预测。若已有运行含交付失败，可为评分命令加 `--allow-partial`，指定独立的诊断评分目录。报告交付率时用有效交付数除以 500；报告有效样本的 Miss、Extra 时注明可评分合同数。两项均逐合同计算再等权平均，值越低越好。逐样本结果在 `scores.jsonl`，汇总在 `report.json`；同一次评分还计算 ROUGE-1/2/L、METEOR 和 BERTScore F1。推理目录、导出的答案、轨迹、评分结果、模型和密钥均留在本地。
