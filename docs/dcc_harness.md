# Benchmark plugins：独立推理与评分（v3）

`dcc_harness/` 通过原生 `make_sp_agent` 执行 SP2。插件只负责公开任务、文件、工具/技能和评测协议，不替换 Central 的规划、角色选择、子任务执行或 FINISH。当前内置 CUAD、WideSearch 和 CLAUSE EVAL3 In-Text。CLAUSE 的安装、数据准备、推理和完整七项评分见[协作者快速开始](clause_quickstart.md)；数据格式和评测细节见[协议说明](clause_benchmark.md)。

## 两个入口

使用已经安装 backend harness 和 `configs/dcc/requirements.txt` 的 Python 3.12 环境，在仓库根目录执行。下面是命令模板，不会在安装或审核时自动调用模型。

```bash
# 第一阶段：仅推理。模型连接参数也可来自 DCC_MODEL_BASE_URL/NAME/API_KEY。
python -B scripts/infer_sp_benchmarks.py \
  --tasks /path/to/cuad/test.jsonl \
  --dataset-source cuad \
  --run-dir /path/to/runs/cuad-run-001 \
  --base-url http://localhost:8000/v1 --model MODEL_ID \
  --temperature 0.2 --top-p 1 --max-tokens 8192 --max-actions 20 --timeout 900

# 第二阶段：仅评分。无需 SP 模型连接信息或模型 API key。
python -B scripts/evaluate_sp_benchmarks.py \
  --tasks /path/to/cuad/test.jsonl \
  --run-dir /path/to/runs/cuad-run-001 \
  --output-dir /path/to/evaluations/cuad-eval-001
```

推理参数显式覆盖原生配置中的对应模型设置，并记录在 manifest；`--seed` 默认不固定，指定后传给模型。`--max-actions` 限制在原生支持范围（当前 2–20），越界直接报错而非被原生静默夹取。只接受一个明确记录的模型配置，避免其他角色/摘要使用未记录的模型。保留 `--thinking`、`--recover-qwen-tool-calls`，工具和技能仍由原生角色权限筛选。

`infer_sp_benchmarks.py --check-config --benchmark cuad` 只检查配置，不调用模型。`--check-search` 会访问检索服务，需单独明确运行。推理阶段不需要 Judge。WideSearch 的评分阶段才要求 `--judge-base-url`、`--judge-model`（也支持对应环境变量）；`--exact-proxy` 是明确标记的替代诊断协议，不能称为原始 Judge 成绩。CUAD 和 CLAUSE 都不需要 Judge；CLAUSE 的 BERTScore 使用本地固定编码器。

可以在一个任务清单中混合多个 benchmark，按任务的 `benchmark` 选择插件，仍串行执行。评分用 `--benchmark NAME` 选择其中一种；不指定则评价全部。两阶段都用 `--dataset-plugin package.module:plugin_or_factory` 注册自定义插件。

## 模型可见输入与私有数据

完整 `Task` 保留评测身份与标签。输入构造器、任务提示词构造器和答案解析器只收到 `PublicTask`，它没有 `id`、`split`、`group_id`、`gold` 或私有 metadata。

| 字段 | 给模型的范围 |
|---|---|
| `prompt` | 公开任务说明 |
| `document` | 完整 UTF-8 文档；公共 staging helper 写成文件 |
| `files` | 一个或多个公开文本文件，以指定中性名称写入 uploads |
| `public_metadata` | 显式声明的公开约束，例如输出列名 |
| `id/split/group_id` | 只用于调度、校验、结果关联，不进入 agent 输入 |
| `gold/metadata` | 只供数据验证与评分；不传给输入构造器 |

例如：

```json
{
  "id": "private-record-id",
  "benchmark": "example",
  "split": "test",
  "group_id": "private-base-contract-group",
  "prompt": "Review the supplied document and return the specified JSON.",
  "files": [{"name": "document.txt", "path": "documents/source-record.txt"}],
  "public_metadata": {"output_language": "en"},
  "gold": {"answer": "evaluation only"},
  "metadata": {"source": "evaluation only", "category": "evaluation only"}
}
```

`path` 相对清单所在目录解析，也支持 `{"name":"document.txt","text":"..."}`。源文件路径不会进入 `PublicTask`。当前通用 staging helper 支持 UTF-8 文本，不负责 PDF/Word/OCR 转换；相关 benchmark 应在数据准备阶段完成转换，并记录版本。

公开文件名必须是普通 basename，禁止绝对路径、目录跳转、隐藏名和路径分隔符。数据准备者必须选择不泄漏答案类别的中性名称。文档按 UTF-8 字节原样写出，保留 CRLF；不摘要、不截断、不建立 RAG。`document` 默认写为 `document.txt`，CUAD 沿用 `contract.txt`。

输入构造器只允许返回经过检查的 `uploaded_files`，不能覆写 `messages`、原生结束状态或其他 graph 字段。框架组装消息、公开文件列表和不可变 `sp_task_contract`，使任务说明在子任务委派时保留。公开任务说明加文件列表不得超过原生 contract 的 12,000 字符上限；较长正文必须进入文件。

兼容旧数据：仍能加载 `metadata.document_file/document_key`；仅 WideSearch 的旧 `answer_columns` 或 `evaluation.required` 被显式迁移到公开列名，其他 metadata 不迁移。新数据应直接使用 `public_metadata`。

这是一条**数据传递边界**，不是操作系统级隔离。Python 插件是可信代码；默认 LocalSandboxProvider 和已启用的 host bash 也不是容器。严格测试环境应让推理进程只访问公开输入、把标签和评分进程放在独立目录/权限或容器中。不要把完整标注目录作为 agent 的上传目录。可单独准备不含 gold/私有 metadata 的推理清单，评分时再使用完整清单；两者的公开输入、ID、split 必须一致。

## 插件接口

`BenchmarkPlugin` 的公开输入接口：

- `prepare_task(PublicTask) -> str`：公开任务要求。
- `prepare_inputs(PublicTask, thread_id) -> dict`：推荐使用通用 `stage_files`。
- `skills_path`、`tools`：任务级原生技能与附加工具，不改变角色权限。
- `validate_dataset(list[Task])`：可选，推理与评分均执行，用于数据协议/划分检查。推理清单可以没有标签，验证器不应强制要求推理端标签。

离线评分接口：

- `parse_answer(PublicTask, raw_delivery) -> JSON value`：校验最终交付的格式；只能看到公开任务。
- `setup_evaluation(EvaluationContext)`：可选，每个 benchmark 每次评测仅调用一次。可以加载 BERTScore 等本地资源并缓存在 `context.resources`。
- `score_task(Task, raw_delivery, EvaluationContext) -> dict`：读取完整 gold/私有 metadata，返回逐样本指标。
- `aggregate(list[Task], list[scored_row], EvaluationContext) -> dict`：必需，拥有分母、分组、计数求和、空值和最终报告逻辑。每个 row 包含 `answer/metrics/task_id`。
- `requires_judge`：声明评分是否需要 Judge。只有显式声明 `supports_exact_proxy=True` 的插件才接受 `--exact-proxy`（内置 WideSearch 支持）；真实 Judge 与 proxy 不能同时启用。
- `evaluation_version`、`evaluation_resources`：记录评分协议与外部资源文件/目录的内容指纹。

`EvaluationContext` 提供 `judge`、`exact_proxy`、`options`、`resources`、`diagnostic_partial`、`provenance`。通过 `--options options.json` 传入只供评分使用的参数，JSON 按 benchmark 分组，例如 `{"example":{"batch_size":8}}`。插件可在 `provenance` 中记录实际加载的模型 revision、截断数量等，最终写入报告。

框架不为未知 benchmark 自动猜测平均方式。需要简单宏平均时，插件可显式调用 `macro_average(rows, keys)`：缺失值/`null` 被排除，并报告每项分母；不同协议应实现自己的汇总。CUAD 已接入原有 `evaluate_cuad`，输出 AUPR、P@80/90 recall、micro P/R/F1，另列 task macro。WideSearch 保留现有逐样本评分，按其指标汇总。

插件可先只实现输入接口收集推理结果，再补评分接口；评分入口要求解析、逐样本评分和汇总三个接口齐全。评分模块宜延迟加载昂贵依赖，避免推理被评分环境约束。

## 结果、完整性与重跑

推理目录：

```text
manifest.json                   # v3 inference 身份、公开输入指纹、模型/工具/技能配置
predictions.jsonl               # 原始最终交付、SHA256、任务状态；没有评分
report.json                    # 覆盖率、交付完成率、失败分类；没有 benchmark 分数
attempts/0000/result.json        # 单次任务结果
attempts/0000/state.json         # 最终原生状态
attempts/0000/trace.jsonl         # 原生事件流
native-state/                   # 每用户/线程的原生 uploads、outputs 等
```

只有原生 FINISH 接受的交付才算推理完成。指定最终 artifact 时读取完整文件并校验线程、run、状态、路径和内容哈希，不拿 Central 摘要或最后一个子 agent 的回答代替答案。没有 artifact 时使用原生接受的文本交付。合法交付里的非法 JSON 仍被收集，之后由评分阶段判定，不能触发重新推理。

`--resume` 只补未记录任务；配置、代码或公开输入变化时拒绝续跑。已记录失败保持原样，不自动重试；需要重新推理时使用新运行目录。已有 attempt 结果已落盘而汇总尚未追加的崩溃窗口可以恢复；中途损坏的结果行不会被静默忽略。v2 结果不能冒充 v3 续跑或评分，需显式迁移后另行审查。

评分目录：

```text
manifest.json       # 推理 manifest/预测指纹、完整评分数据指纹、代码/资源/依赖版本
scores.jsonl        # 解析后的答案、逐样本指标、评分失败类型（严格预检失败时不生成）
report.json         # 各 benchmark 独立汇总、分母与完成状态
```

评分不修改推理文件，每次使用新 `--output-dir`，可调整评分插件/参数后重评同一批交付。缺少、重复、未知、输入不一致或哈希损坏的预测被拒绝；所选 benchmark 必须覆盖推理 manifest 中的全部对应任务，不能把子集冒充全集。

默认严格模式下，缺失/失败/格式错误的预测导致预检失败，不生成指标汇总；评分依赖或评分函数失败是 `evaluation_error`，汇总失败是 `aggregation_error`，都不改写原始推理状态。不会把这些失败替换成 `[]` 或零分。`--allow-partial` 才允许对有效部分做诊断汇总，并明确标记；插件收到的任务与已评分 row 一一对应。

退出码：完整成功为 0；推理未全部交付或评分未全部成功为 2；参数/协议错误由 CLI 报错。`complete_split` 只表示覆盖该运行 manifest 的全部所选任务，不自动证明是上游官方测试集；官方范围应由数据协议及插件验证器约束。

## 不执行 SP2 的审核

```bash
python -B -m pytest \
  tests/test_benchmark_lifecycle.py tests/test_sp_benchmarks.py tests/test_sp_dataset_plugins.py \
  --no-sp-runtime -q -p no:cacheprovider
```

`--no-sp-runtime` 跳过真实 graph 集成测试，并将原生 `make_sp_agent` 替换成失败哨兵。接口测试只能显式替换为模拟 graph；仍使用真实配置加载、原生文件 sandbox、交付校验和 CUAD/WideSearch 评分代码。不请求 SP 模型服务，不运行 benchmark 推理。运输层回归测试仅使用本地模拟 HTTP 服务。
