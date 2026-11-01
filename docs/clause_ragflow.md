# CLAUSE EVAL3：可选 RAGFlow 输入

本适配针对 EVAL3 文本内部任务，保留冻结的 L1/L2 prompt、原测试划分、严格 JSON 解析和七项指标。它增加独立的 `clause_ragflow` 插件；现有 `clause` 文件插件及其命令不变，不需要 RAGFlow。新模式复用 DCC 的逐样本建库、解析等待、可恢复索引和作用域检索，不修改 SP 核心运行流程。

| 模式 | SP 可见输入 | 读取方式 | 评分报告 input_mode |
| --- | --- | --- | --- |
| `clause` | document.txt、原 prompt | 原生文件工具 | sp2-files |
| `clause_ragflow` | dcc-context.json、原 prompt 和 RAG 执行约束 | ragflow_retrieve + 文件工具读上下文/写答案 | sp2-ragflow |

RAG 清单仍引用原合同，供宿主端校验输入哈希和离线评分绑定；输入适配器不会将它加入 SP 上传目录或消息。标注、来源、问题类别、记录 ID 留在宿主清单；只有合同正文上传到 RAGFlow，不能上传 private/。每个样本一个独立知识库，工具固定 dataset_ids/document_ids。Agent 只提供查询和分页参数，服务地址/密钥由运行环境配置，不能借空检索扩大范围。返回内容包括片段文本、文档/片段标识、相似度及位置（取决于服务响应），不是完整合同的保证。

## 准备与建库

在仓库根目录执行。先按 [CLAUSE 快速开始](clause_quickstart.md) 恢复原始 public/private 清单；已有数据可以直接使用，无需重下载。安装项目运行依赖及 `configs/clause/requirements-ragflow.txt`，离线评分另需原 CLAUSE 评分依赖和资源。

复制 `configs/dcc/preprocessing.clause.example.json` 到忽略目录，如 `.benchmark-data/clause-ragflow.json`，设置：

- `loader_options.source`：原 CLAUSE **private/test.jsonl**，相对配置文件解析；若配置在 `.benchmark-data/`，可填 `clause/private/test.jsonl`。这保证后续离线评分清单保留标注。也能用 public 清单做仅推理准备，但其结果不能用于正式评分。
- `ragflow.base_url`：实际服务根地址；不是网页设置页。
- embedding、parser、chunk 配置：按服务部署选择并保留在实验记录中。
- 通过 `RAGFLOW_API_KEY` 或 `RAGFLOW_API_KEY_FILE` 提供凭据，勿提交密钥。

```bash
python scripts/dcc_preprocess.py prepare \
  --config .benchmark-data/clause-ragflow.json \
  --output .benchmark-data/clause-ragflow-l1
python scripts/dcc_preprocess.py index \
  --config .benchmark-data/clause-ragflow.json \
  --bundle .benchmark-data/clause-ragflow-l1
python scripts/dcc_render_clause_ragflow.py \
  --config .benchmark-data/clause-ragflow.json \
  --bundle .benchmark-data/clause-ragflow-l1
```

`prepare` 不访问服务；`index` 才创建知识库、上传正文并等待解析完成，可用同配置重跑恢复。最后的 CLAUSE 专用 render 生成 `public/clause-tasks.jsonl` 和 `private/clause-tasks.jsonl`，不要将通用 `public/tasks.jsonl` 用作 CLAUSE 评分输入。不要并发修改同一 bundle。首次诊断可在 prepare 使用 `--limit 2`；这不是完整测试集。

同一 bundle 可通过 `dcc_render_clause_ragflow.py --variant l1` 和 `--variant l2` 输出两套清单；两套 prompt 共用原样本索引，不重新上传或解析。全量发布入口会自动生成两套清单及文件读取模式清单。不要手动复制或篡改索引账本。

## 推理和独立评分

配置 SP 模型和文件工具与原文件实验一致。`DCC_BUNDLE`、`DCC_CONFIG` 使用绝对路径，密钥仅供工具后端读取。

```bash
export DCC_BUNDLE="$PWD/.benchmark-data/clause-ragflow-l1"
export DCC_CONFIG="$PWD/.benchmark-data/clause-ragflow.json"
python scripts/infer_sp_benchmarks.py \
  --dataset-plugin dcc_harness.preprocessing.clause:make_plugin \
  --benchmark clause_ragflow --dataset-source clause-intext-yes-whole-document-v1 --config config.yaml \
  --tasks "$DCC_BUNDLE/public/clause-tasks.jsonl" \
  --run-dir .benchmark-data/runs/clause-ragflow-l1
python scripts/evaluate_sp_benchmarks.py \
  --dataset-plugin dcc_harness.preprocessing.clause:make_plugin \
  --benchmark clause_ragflow \
  --tasks "$DCC_BUNDLE/private/clause-tasks.jsonl" \
  --run-dir .benchmark-data/runs/clause-ragflow-l1 \
  --output-dir .benchmark-data/scores/clause-ragflow-l1 \
  --options .benchmark-data/clause-ragflow-evaluation.json
```

评分 options 与原 CLAUSE 相同，但顶层键必须为 `clause_ragflow`：

```json
{"clause_ragflow":{"resource_root":".benchmark-data/clause/resources","device":"cpu","batch_size":8,"without_bert":false}}
```

评分无需 RAGFlow 在线、密钥或 `DCC_BUNDLE` 环境变量，只读取保存的预测、private 清单及其引用文件、评分资源。保持 bundle 中原正文和 context 文件可用。小样本诊断需显式 `--allow-partial`，不得作为全量分数。

输出仍为 `clause_answer-<action_id>.json`，内容仅 JSON 数组，每项恰好 `text`、`explanation` 两个字符串。任务 skill 要求原生 FINISH 选中该文件；不会将检索错误转换成 `[]`，不会自动修补答案。

默认 RAG skill 为每次审核委派设置最多 8 次检索，要求问题/分页去重、读取完整工具结果，并由显式获得检索工具的 Reporter 完成审核、JSON 写入和控制消息交回；这个预算属于实验方法配置，修改后必须另建 run 并记录 skill 版本。

RAG 改变的是证据获取方式。排序检索可能漏掉远距离矛盾，分块可能改变句子边界；这可能影响召回和逐句对齐，不能将 RAG 成绩直接当成原全文文件模式成绩。需分别记录模型、prompt、检索、分块和输入模式。

## 验证边界

`tests/test_clause_ragflow.py` 使用假的 RAGFlow 服务验证准备/建库/渲染、L1/L2、输入隔离、当前文档作用域、篡改拒绝、原文件模式以及评分代码复用。它不会启动 SP 或真实服务。真实服务解析质量和 SP 检索工具调用仍须在配置好凭据后进行独立冒烟验收，不能由这些测试推断已完成真实端到端运行。

CLAUSE 不需要 pyarrow。不要为本适配在固定 NumPy 1.26.4 的 CLAUSE 评分环境中直接安装最新 pyarrow；远程 FinAuditing Parquet 流程可使用其独立兼容环境。


## 完整共享 Release

维护者按固定协议准备全部 5,026 份文档：train 4,025、validation 501、test 500。每个样本拥有独立知识库，L1/L2 共用该样本的索引。标注只用于离线评分，不能上传 RAGFlow。

```bash
python scripts/dcc_clause_release.py prepare --source .benchmark-data/clause \
  --config .benchmark-data/clause-ragflow.json --root .benchmark-data/clause-shared
python scripts/dcc_clause_release.py index --root .benchmark-data/clause-shared
python scripts/dcc_clause_release.py render --root .benchmark-data/clause-shared
python scripts/dcc_clause_release.py verify --root .benchmark-data/clause-shared
python scripts/dcc_clause_release.py audit --root .benchmark-data/clause-shared \
  --descriptor .benchmark-data/clause-service-audit.json
python scripts/dcc_clause_release.py pack --root .benchmark-data/clause-shared \
  --archive .benchmark-data/clause-eval3-v1.tar.gz \
  --descriptor .benchmark-data/clause-release-descriptor.json --version dcc-clause-eval3-v1
```

`index` 可从现有账本恢复，`audit` 会检查每个知识库的文档状态并实际检索。不要并行运行修改同一个 root 的命令。发布前还必须进行真实 SP 文件/RAG 两模式验收，确认原生 FINISH 选中纯 JSON 文件及完整评分成功。服务审计不等于模型效果评测。

Release 仅包含正文、分离的标注、固定协议/prompt、公共服务设置、知识库映射及四套运行清单。不会打包 Key、日志、模型权重、环境目录或开发审核文件。发布包索引标记为只读，使用者不应重新 index；更换解析器、embedding 或数据应由维护者发布新版本，保留旧版本。

协作者下载并安装（descriptor 由维护者随版本提交，固定包名与 SHA-256）：

```bash
python scripts/dcc_clause_release.py download \
  --descriptor configs/dcc/releases/clause-eval3-v1.json \
  --root .benchmark-data/clause-eval3-v1
export DCC_BUNDLE="$PWD/.benchmark-data/clause-eval3-v1/test"
export DCC_CONFIG="$PWD/.benchmark-data/clause-eval3-v1/config.json"
```

也可以先手动下载附件，再使用 `install --archive ... --descriptor ... --root ...` 校验和安装。安装拒绝覆盖已有目录；运行后不要改写数据包里的清单、正文和标注。Git clone 不会下载 Release 附件。

选择输入与评分清单：

| 模式 | plugin / benchmark | 推理清单（相对 DCC_BUNDLE） | 评分清单 |
| --- | --- | --- | --- |
| RAG L1 | preprocessing.clause:make_plugin / clause_ragflow | public/clause-l1-tasks.jsonl | private/clause-l1-tasks.jsonl |
| RAG L2 | 同上 | public/clause-l2-tasks.jsonl | private/clause-l2-tasks.jsonl |
| 文件 L1 | 内置 clause | public/clause-files-l1-tasks.jsonl | private/clause-files-l1-tasks.jsonl |
| 文件 L2 | 内置 clause | public/clause-files-l2-tasks.jsonl | private/clause-files-l2-tasks.jsonl |

RAG 的完整 plugin 参数为 `--dataset-plugin dcc_harness.preprocessing.clause:make_plugin`。文件模式不传该参数，不需要 RAGFlow Key。评分 options 顶层只放当前选中的 benchmark，不能把其他 benchmark 的设置混进单模式评测。

### 共享服务的权限

GitHub 协作者权限只保证能下载仓库/附件，不等于 RAGFlow 检索权限。共享服务必须向协作者提供被授权访问这批知识库的凭据，或由服务管理员配置团队访问。任意账号新生成的 Key 不保证能访问他人权限为 `me` 的知识库。

用 `RAGFLOW_API_KEY_FILE` 指向自己机器上的受限凭据文件（建议 chmod 600），或通过运行环境提供 `RAGFLOW_API_KEY`。他人的服务器文件路径不能直接复用；不要把 Key 写入仓库、Release、示例命令或实验报告。使用者通常只需检索权限；创建和维护知识库的权限由维护者管理，具体可用权限机制以部署服务为准。

共享服务故障/知识库被删时，映射不会自动恢复索引。请保留可复现数据包和准备脚本；需要恢复时创建新版本，并记录新的知识库 ID。映射变更必须重新生成输入清单，不得继续混用旧实验结果。

## 全量实验与分片汇总

L1、L2 各有完整的 500 条测试样本。并行运行时可把同一模式、同一 prompt 版本的 public 清单分成互不重叠的分片，每个进程使用独立 run-dir；保持代码、模型、配置、工具和 skill 完全相同。切分后如果改变清单位置，应同步改写文件引用或使用绝对路径。

```bash
python scripts/collect_sp_benchmarks.py \
  --tasks .benchmark-data/test-public.jsonl \
  --run-dir .benchmark-data/runs/shard-0 \
  --run-dir .benchmark-data/runs/shard-1 \
  --output-dir .benchmark-data/runs/collected
```

汇总器校验执行指纹、输入覆盖、重复记录和答案哈希，保留原始答案、失败记录及分片来源；不重跑 SP、不修补 JSON，也不把不同代码或配置的旧实验拼接成新实验。之后以 collected 为 run-dir，使用对应的完整 private 清单调用原评分入口。缺少预测默认报错；只有显式 `--allow-missing` 才生成不完整汇总，诊断评分另需 `--allow-partial`。原始运行目录与轨迹应一并保留。

全量结果、日志、模型配置和服务凭据保留在本地实验目录，不属于共享数据 Release。严格全量评分成功与小样本冒烟成功是不同的验收项目；失败样本必须如实列出。

文件读取模式的长合同需要分段阅读全文。原生 `read_file` 支持仅指定起始行或结束行；越过 EOF 时返回总行数。skill 要求显式有界范围，遇到字符截断时缩小范围补齐，不能把截断响应当成已读完整页。本次适配同时修复了旧工具只传起始行时重复返回文件开头的问题。

若所选模型的上下文足够容纳合同，可在实验 YAML 中显式提高 `sandbox.read_file_output_max_chars`（例如 262144 个字符），减少不必要的分页。这个单位是字符，不是 token；应先核查最长合同与模型上下文限制，并将配置保存在推理 manifest 中。skill 先尝试一次完整文件读取，只有发生截断才分页；输入仍由文件工具获得。

两种模式的执行 skill 都要求去重、必要的完整句子引文和简洁解释，以减少冗长输出耗尽模型预算；不规定问题数量，不截断或改写模型已经交付的答案。达到模型输出上限而未完整交付的响应必须保留为失败，不能补成 JSON 或用空数组替代。

文件模式 skill 对较长答案使用原生 `write_file` 分批追加同一个数组，限制单次序列化量而非发现总数。完成闭合数组和读回校验后才能 FINISH；构建中的部分文件不是交付结果。评分入口仍严格拒绝未完成或非协议 JSON，宿主不会为模型补括号、删字段或改写发现。
