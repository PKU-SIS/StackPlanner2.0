# DCC 数据加载、预处理与 RAGFlow 建库

这一层负责把数据集变成可供原生 SP 使用的公共任务输入，不执行 agent，也不计算评测指标。
RuleArena NBA Transaction、CLAUSE 及未来数据集都可以复用同一条处理链；本次提供
FinAuditing 和通用 JSONL 两个 loader，不预设其他数据集的字段或评分方式。

```text
loader(options) -> Sample
  -> processor(sample, options) -> ...
  -> prepare: 文档 / 规则 / 私有标签分离，内容哈希去重
  -> index: RAGFlow 建库 -> 上传文档 -> 切片/索引 -> 轮询完成
  -> render: 公共上下文 + 通用/数据集/样本 prompt 片段 -> SP Task JSONL
  -> native BenchmarkPlugin: 上传规则文件 + 挂载原生检索工具
```

## 快速使用

### 跨服务器使用 Release 数据包

FinAuditing 的完整数据包在[同仓库 Release](https://github.com/PKU-SIS/StackPlanner2.0/releases/tag/dcc-finauditing-v1)
发布。普通使用者下载一次即可复用已完成的共享 RAGFlow 知识库，无需自己下载原始数据或重新建库。
原始 Parquet、数据集卡片和来源版本也随包保留，供回查或重新处理。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r configs/dcc/preprocessing-requirements.txt
# 私有仓库附件通过 GitHub CLI 下载；已登录时无需重复登录。
gh auth login
.venv/bin/python scripts/dcc_preprocess.py download \
  --release finauditing-v1 --output .benchmark-data/finauditing-v1

export DCC_BUNDLE="$PWD/.benchmark-data/finauditing-v1/bundle"
export DCC_CONFIG="$PWD/.benchmark-data/finauditing-v1/config.json"
export RAGFLOW_API_KEY_FILE=/path/to/your/ragflow.key
.venv/bin/python scripts/dcc_preprocess.py status --bundle "$DCC_BUNDLE"
.venv/bin/python scripts/dcc_preprocess.py retrieve \
  --config "$DCC_CONFIG" --bundle "$DCC_BUNDLE" \
  --sample-id finauditing-finmr-0 --question 'PaymentsToAcquirePropertyPlantAndEquipment'
```

也可从 Release 页面手动下载附件，给 `download` 增加 `--archive /path/to/finauditing-v1.tar.gz`，
其余步骤相同。下载脚本按仓库内的 `configs/dcc/releases/finauditing-v1.json` 固定版本，
校验压缩包 SHA-256、全部文件哈希、任务清单和知识库范围后才安装；不覆盖已有目录。
这些检查均在本地完成，不创建或修改 RAGFlow 知识库。

包内结构：

```text
finauditing-v1/
  raw/FinAuditing/           # 原始 Parquet、README、来源版本和校验值
  bundle/                   # 下文定义的统一样本、规则、标签和已完成索引映射
  config.json               # 共享服务配置，所有数据/prompt 路径均相对本目录
  prompts/                  # 此版本实际使用的 prompt 片段
  release.json              # 文件清单、校验值及原始样本一致性核对结果
  README.md                 # 来源、许可与使用说明
```

不同服务器通过同一个 RAGFlow 地址和账号 Key 访问同一组知识库，ID 无需转换。
`index.json` 是这套服务的映射记录，不是向量索引备份；若更换为另一套 RAGFlow，应重新建库。
运行 SP 时使用 `bundle/public/tasks.jsonl`，并注册
`--dataset-plugin dcc_harness.preprocessing.native:make_plugin`；其余模型与 SP 配置沿用
[通用 benchmark 推理说明](dcc_harness.md)。原生适配器只给 agent 问题、规则文件和当前样本检索工具。

### 从原始数据重新准备

在仓库根目录运行。离线预处理只需要轻量环境，无需安装整个 SP：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r configs/dcc/preprocessing-requirements.txt

# 用服务器上已有的私有 Key 文件，或直接设置 RAGFLOW_API_KEY。
# 不要把真实 Key 写进 JSON 配置、prompt 或 Git。
export RAGFLOW_API_KEY_FILE=/home/qiuzh/.config/stackplanner/ragflow.key
export DCC_CONFIG="$PWD/configs/dcc/preprocessing.finauditing.example.json"
export DCC_BUNDLE="$PWD/.benchmark-data/finauditing-rebuilt"

.venv/bin/python scripts/dcc_preprocess.py prepare \
  --config "$DCC_CONFIG" --output "$DCC_BUNDLE"
.venv/bin/python scripts/dcc_preprocess.py doctor --config "$DCC_CONFIG"
.venv/bin/python scripts/dcc_preprocess.py index \
  --config "$DCC_CONFIG" --bundle "$DCC_BUNDLE"
.venv/bin/python scripts/dcc_preprocess.py render \
  --config "$DCC_CONFIG" --bundle "$DCC_BUNDLE"
.venv/bin/python scripts/dcc_preprocess.py status --bundle "$DCC_BUNDLE"
.venv/bin/python scripts/dcc_preprocess.py retrieve \
  --config "$DCC_CONFIG" --bundle "$DCC_BUNDLE" \
  --sample-id finauditing-finmr-0 --question 'PaymentsToAcquirePropertyPlantAndEquipment'
```

`base_url` 使用站点根地址 `https://v262-min-ragflow.pkubir.cn`，而不是用户设置页
`/user-setting/api`。认证依次读取配置指定的环境变量（默认 `RAGFLOW_API_KEY`）、
`api_key_file`，或 `RAGFLOW_API_KEY_FILE` 指向的文件。配置文件中的相对路径以
配置文件目录为基准；自定义 Python 插件自行解释其 `options`。

小规模验证可以在单独配置中设置 `loader_options.limit_per_task: 1`，每个 FinAuditing
子任务取一个样本；`prepare --limit N` 则限制整个 loader 总共输出 N 条。全量运行
应去掉两类限制，并使用新的输出目录。全量 prepare 不触发联网建库。

### 输出契约

```text
bundle/
  manifest.json               # 版本、计数、输入指纹
  index.json                  # 知识库/文档 ID、切片状态与样本检索范围
  public/
    samples.jsonl             # 规范化后的公共样本
    documents/<hash>.<ext>    # 仅供建库；不上传到 agent sandbox
    rules/<hash>.<ext>        # 规则和参考材料；不进入 RAGFlow
    contexts/<id-hash>.json   # 样本检索范围与公共文件清单
    tasks.jsonl               # render 后生成的 dcc_harness.data.Task
  private/
    gold.jsonl                # 按样本 ID 对齐的原始标签和来源信息
```

`private/` 权限为 700。原始来源信息、DQC ID 和 gold 均不转入 prompt；只有 loader
显式声明的 `public_metadata` 可进入公共输入。`tasks.jsonl` 是宿主侧 Task 清单，
`gold` 留空，标签由评测器通过样本 ID 加载 `private/gold.jsonl`。推理侧使用
`task.public_input()` 得到 PublicTask，仅包含公开指令和中性文件名下的文件正文，
不包含样本 ID、split、来源路径或私有 metadata。不把 bundle 根目录挂到 agent；原生
适配器只向当前 user/thread 的 uploads 写入规则文件及公共 context。
适配器在运行前检查索引已完成、知识库按样本独立，以及渲染时的检索范围仍与当前
索引一致；旧版共库任务或过期 context 不能直接带入 agent。
这项隔离控制的是应用的数据流，不替代部署层面的文件系统权限和 sandbox 隔离。

## 数据集插件

`Sample` 和 `Asset` 是稳定扩展契约（`dcc_harness.preprocessing.models`）。
`Sample.documents` 保存待审文档，`Sample.rules` 保存规则/参考文件，`gold` 和
`metadata` 为评测侧私有数据，`public_metadata` 必须显式选择可公开字段。
`Asset` 恰好提供 `text` 或 `path` 其中之一。已有 PDF/Word/表格等文件按原字节上传，
交给 RAGFlow 对应 parser 解析；文本/XBRL 默认保留标签、数字、单位与关系，不做
可能破坏语义的清洗。可以在 processor 中按数据集有选择地转换。
原生 SP 的规则/参考文件使用 UTF-8 文本；二进制规则文件应先由 processor 转为文本。

通用 loader（`loader: "manifest"`）从 JSONL 读取：

```json
{"id":"case-001","dataset":"my_dataset","split":"test","question":"检查这份文档是否满足规则。","documents":[{"id":"report","path":"documents/report.pdf"}],"rules":[{"id":"policy","text":"本数据集的公开检查规则。"}],"gold":{"violations":[]}}
```

文件路径相对 JSONL；拒绝越出其目录的文件引用。现有材料在其他目录时，先将输入
组织在同一数据根目录，或提供明确处理该布局的自定义 loader。

自定义数据源无需修改 registry 或 harness：

```python
# my_package/inputs.py
from dataclasses import replace
from dcc_harness.preprocessing import Asset, Sample

def load(options):
    yield Sample(
        id="case-001", dataset="my_dataset", split="test", question="检查合规性",
        documents=(Asset("report", text="待审内容"),),
        rules=(Asset("policy", text="公开规则"),),
        gold={"expected": "仅供评测"},
    )

def preprocess(sample, options):
    return replace(sample, question=sample.question + options["suffix"])
```

配置 `loader: "my_package.inputs:load"`，并在 `processors` 中按顺序声明
`{"use":"my_package.inputs:preprocess","options":{"suffix":" 请保留证据。"}}`。
这些 Python 扩展属于受信任代码；将其包加入运行环境/PYTHONPATH。

### FinAuditing

从服务器已有的 `FinRE/test.parquet`、`FinMR/test.parquet`、`FinSM/test.parquet`
流式分批读取，一次 JSON 解码原始 `query`，按数据中的七个固定标题精确分离内容。
六类 filing 文档进入 RAGFlow；原始任务说明与 US GAAP Taxonomy 作为规则/参考文件。
数据集专属任务要求和最终输出格式同时由 `configs/dcc/prompts/finauditing/` 中的
FinRE、FinMR、FinSM 三个片段直接注入输入 prompt，内容使用数据集原始任务说明。
示例配置已经按子任务启用这些
片段：FinRE 输出单个关系标签，FinMR 输出提取值/计算值的 JSON，FinSM 输出标识符数组。
原文标为 `None` 的缺失文档不伪造内容，记录在公共缺失文档清单中。异常结构直接报错，
避免在上游格式变化时静默丢失数据。

保留官方 test 划分，不生成训练或验证样本。按子任务加前缀构造唯一 ID；
`answer`/`gold` 只进入私有文件，不猜测新的统一指标。来源 parquet 哈希、下载清单中
的 repository/revision 一起保留；如果下载清单带校验值，会先校验原始文件。

基线配置的 `processors` 为空。除一次 JSON 解码、结构拆分、边界空白整理和移除末尾
空 `Answer:` 标记外，不改写问题或文档，不提取摘要，不合成数据，不依据标签筛选证据。
RAGFlow 的 `naive` 切片、512 token 和关闭自动关键词/问题生成是可配置的建库参数，
不作为原始数据的一部分；方法优化可在独立配置和新输出目录中进行。

## Prompt 注入

默认片段位于 `configs/dcc/prompts/`。组装次序是：

1. 通用默认层，或配置中的 `prompts.layers` 整体替换列表。
2. `prompts.datasets[dataset]`：当前数据集片段。
3. `prompts.samples[sample_id]`：单样本片段。

片段可以是相对配置目录的 Markdown 路径，或 `literal:...` 字符串。使用严格的
`${variable}` 替换，未知变量会报错；字面 `$` 写成 `$$`。可用变量为 `question`、
`rules`、`documents`、`sample_metadata`、`context_file`。数据集/样本 ID 仅供宿主选择片段，
不作为模板变量进入 agent。没有 gold、私有 metadata 或认证变量。更多公开信息由 loader 显式声明，
通过 `sample_metadata` 注入。prompt 文件内容不递归执行模板替换。

规则正文不会塞进初始 prompt；prompt 只列出规则文件，要求 agent 用原生文件工具
读取。文档只列公共 inventory，并要求调用原生 `ragflow_retrieve` 接口获得证据。
改 prompt 后只需重跑 render，无需重新上传、切片。
FinAuditing 的 `prompts.datasets` 映射可以分别替换这三个片段；未来数据集采用相同方式
提供自己的任务要求与输出约束。领域规则及 taxonomy 仍通过规则文件读取。

## RAGFlow 建库和续跑

`ragflow.index_workers` 控制同时处理的样本数，范围 1–16，默认 1，FinAuditing 示例为 8。
每个线程独立使用 HTTP session，索引进度集中串行落盘；遇到错误停止提交新样本，保留
已经完成和正在处理样本的进度。同一个 bundle 仍只允许一个 index/render/pack 命令操作。

使用公开 REST API：创建/查找 dataset、上传 document、POST dataset chunks 启动解析、
GET documents 轮询状态，以及 POST retrieval 检索。创建参数通过 `dataset_options`
透传，因此不同部署的 `chunk_method/parser_config` 或 `parse_type/parser_id` 可在配置
中选择，不需要改数据链路。参考 [RAGFlow HTTP API](https://github.com/infiniflow/ragflow/blob/v0.22.1/docs/references/http_api_reference.md)。

`doctor` 通过 RAGFlow 0.26+ 的 `GET /api/v1/models?type=embedding` 检查账号可用的
embedding 模型，只输出模型名称/provider 信息。示例配置启用 `check_embedding_models`，
在上传之前拦截未配置模型的情况。更早版本没有该模型端点时可关闭这项检查，实际
解析状态仍然必须校验。若默认模型报 provider 不存在，需要先在 RAGFlow 配置模型，
再在 `dataset_options.embedding_model` 明确选择该部署接受的模型 ID。

每个样本建立独立知识库，配置固定为 `knowledge_base_scope: "sample"`。同一数据集
中的独立题目也分别建库；只有当前样本需要联合检查的关联文档才放在同一个库。
例如 FinAuditing 一题的多类 XBRL 文档可以在该题专属库内联合检索，其他题目的
文档不进入这个库。规则文件始终不建库。

知识库名称带样本 ID 和公共输入/解析配置指纹。文档按内容哈希命名：本地存储可以
跨样本去重，但 RAGFlow 远端只在同一样本内去重，不为节省上传而合并独立样本的库。
每个检索请求固定携带当前样本唯一的 `dataset_id` 及其确切 `document_ids`，工具不接受
agent 自行传入其他库/文档 ID；返回越界文档时直接报错。任务生成时还会检查各样本
没有共用知识库 ID。
若续跑时发现样本库中出现清单之外的文件，流程会报错，避免外部文件参与该题检索。
所有文档必须达到 DONE 且 `chunk_count > 0`，index 才标为 ready。真实检索应另用
retrieve 验证，DONE 状态本身不证明召回质量；这次框架也不将 RAG 改写后的成绩
混称为原论文评测结果。

上传与 ID 记录逐条保存到原子替换的 ledger。中断后重跑同一 index 命令，通过确定性
知识库名/文件名核对已完成工作；创建和上传写请求不自动盲目重试。失败/取消的解析
需要检查原因，再传 `--retry-failed` 显式重试。超时保留 ledger，不删除远程数据。
公共输入或解析配置变化要求新的 bundle；CLI 文件锁防止同一 bundle 同时建库/render。
索引 ledger 使用 schema version 2，不接受早期按数据集共库的 version 1 记录。已有
旧索引时应新建 bundle/知识库；只有离线预处理产物、还没有 index.json 的 bundle 可以
直接按新策略首次建库。RAGFlow 默认 embedding 配置的变更不会自动修复旧知识库；
模型配置后应使用新建的知识库，并核验实际继承的模型。
不要在 RAGFlow UI 手动替换这些受管理的文档内容或解析配置；本地校验无法证明 UI
中的同名文档没有被外部修改。当前流程不提供删除或覆盖知识库的命令。

## 接到原生 SP

在原生 SP 的 Python 环境中，注册现有插件接口：

```python
from dcc_harness.data import load_tasks
from dcc_harness.datasets.benchmark_plugins import resolve_dataset_plugin, configure_dataset_app, build_inputs

plugin = resolve_dataset_plugin("dcc", ("dcc_harness.preprocessing.native:make_plugin",))
app = configure_dataset_app(base_app, plugin)
# 加载 public/tasks.jsonl；DCC_BUNDLE 指向当前 bundle。
task = load_tasks(bundle / "public/tasks.jsonl")[0]
inputs = build_inputs(plugin, task.public_input(), thread_id)
# inputs 提供 messages、uploaded_files 和不可变 sp_task_contract。
# 每个样本使用新的 user/thread，保留原生 SP 的执行入口。
```

配置运行进程的 `DCC_BUNDLE`、`DCC_CONFIG` 和 RAGFlow 认证。原生插件提供 DCC
Skill、规则文件上传和一个只读 REST 检索工具；不改 CentralAgent、调度器或 agent loop。
网络请求在线程中执行，沿用 SP 的 user/thread 路径解析。

本阶段插件的 `parse_answer` / `score_task` 留空，已有 `evaluate_sp_benchmarks.py` 会
明确拒绝以该插件评测。仓库 v3 已将推理与离线评测拆开；下一阶段为 DCC 各子任务
补齐 parser、scorer、聚合指标及标签加载，接到现有通用评测流程。

## 发布新版本

维护者先完成全量 index 和 render，再打包；`pack` 要求 FinAuditing 基线处理插件为空，
会逐条比较源数据与规范化后的问题、文档、规则和答案，并核对初始 prompt 包含原始任务说明。

```bash
python scripts/dcc_preprocess.py pack --config "$DCC_CONFIG" \
  --bundle "$DCC_BUNDLE" --source /path/to/original/FinAuditing \
  --version finauditing-v2 --output /path/to/finauditing-v2.tar.gz
```

把输出的附件名和 SHA-256 写入新的 `configs/dcc/releases/finauditing-v2.json`，字段为
`repository`、`tag`、`asset`、`sha256`，然后随代码提交。将压缩包上传至同仓库对应 tag 的
Release，下载到新目录验证后发布。已有版本保持不变；二次方法优化使用新的配置与版本。
RAGFlow 实际索引保存在共享服务中，附件仅携带已完成索引的映射。

## 验证

```bash
python -m pip install pytest ruff
python -m pytest tests/test_dcc_preprocessing.py tests/test_dcc_releases.py -q
# 原生 SP 依赖已安装时：
python -m pytest tests/test_dcc_native.py -q --no-sp-runtime
ruff check dcc_harness/preprocessing scripts/dcc_preprocess.py tests/test_dcc_*.py
```
