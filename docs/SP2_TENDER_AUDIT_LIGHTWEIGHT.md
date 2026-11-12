# SP2.0 标书审核轻量适配

当前保留的四组结果、最新评测、完整轨迹和开销入口见 [标书审核实验结果统一索引](TENDER_AUDIT_RESULTS.md)。2026-10-23 已将正式结果与历史调试／旧版评测分开归档，原路径通过符号链接继续兼容。本文后续的阶段记录保留当时口径；最新准确率以统一索引所指的三口径评测和四组对比为准。

本适配参考 SP1 的《StackPlanner1.0标书审核轻量适配与评测经验》，通过独立业务目录和默认不启用的通用扩展接口调用 SP2 原生图。Central 的动作、调度策略、反馈与记忆栈、原生恢复、Artifact 注册及 FINISH 验收均保留。默认角色 prompt 和 Web 服务配置不变。本实验不使用额外模型调用修复 JSON。

## 输入与上下文

使用 SP1 的审核要点表和真实错误汇总表，原文件只读访问。GT 表每行对应一个 case，共 20 个；13、17、18 等多规则行仍合并为一个 case。规则匹配、排序和拼接与归档的 SP1 轻量适配一致。

文档表格及 `真实错误汇总_v262_20260915.xlsx` 的旧 dataset ID 在本次预检中返回业务错误 102。正式验证改用 SP1 实际评测归档的 `benchmark_inputs/真实错误汇总_current_20261014.xlsx`，复制到本项目 `.deer-flow/experiments/tender-audit-inputs/`。已核验其 SHA-256 为 `1272220466c700512f9f7e9f3ad6bb0d8986fd555d87ec979671009ec1fb1a95`，并逐单元格确认仅 dataset ID 列与旧表不同；规则表字节完全一致。该目录的 `input-provenance.json` 保存归档路径、原映射 manifest 与核验说明。正式批次应复用这份输入，避免与失效映射混用。

模型只能收到 GT 的编号、标书名、审核要点选择器和知识库 ID。完整规则及起始关键词来自审核要点表，GT 的错误答案、位置、对比内容等字段不会进入模型输入。每次委派都确定性注入完整规则，超过上下文预算则报错，避免依赖 Central 转述或静默截断。

Researcher 只有 `local_search_tool(query)`，dataset 在客户端绑定，模型不能指定其他知识库。请求沿用 SP1 参数：`page_size=15`、`similarity_threshold=0`、`vector_similarity_weight=0.7`、`top_k=1024`、`keyword=false`、`highlight=false`、超时 120 秒。HTTP 和业务错误按失败记录。检索返回保留原始 chunk 内容、ID、文档与位置等元数据，不注入标准答案，也不做检索改写模型调用。 模型可见字段策略`canonical_content_source_metadata_v1`只删除`*_ltks`/`*_tks`派生索引、重复正文副本和空字段，保留完整正文、全部条款/数字、来源/位置与表格关联。每次检索仍返回完整正文，不用跨轮次“已见ID”替代；原始响应及客户端来源池完整保存，Reporter包及可读缓存采用相同投影。

Central 和全部子代理共享完整规则及交付格式，`score` 明确为 0–1 自报置信度，不是采购评分或需要原文证明的数值。原始规则的判断口径保持不变，case 5 不扩展到入围人数。

Reporter 接收原生阶段产物及实际检索原文；额外原文包先按 chunk、文档与内容去重，再以完整 chunk 限制在 96,000 字符以内，优先保留研究产物引用的来源。所有去重来源另存于当前 case 的 outputs/audit-evidence/，`source_manifest` 提供只读路径；省略的内容可按需读取，`truncated` 不代表已经读过。本次配置将 RAG 工具加入输出预算豁免，避免仅有 RAG 能力的 Researcher 收到无法读取的外置文件。此配置只作用于实验副本。

## JSON Artifact

Reporter 保留原生 `summary/artifact_content/artifact_type/artifact_metadata` 外层协议，在 `artifact_content` 中直接返回审核 JSON 对象，由已有原生适配器序列化；不再要求手写嵌套 JSON 字符串。类型为 `report_revision`，文件名为 `audit-case-XX.json`。本次角色副本关闭默认 Markdown 报告 skill，仅保留 read_file，原生产物适配器保存最终文件，避免重复写路径；通用角色定义不变。

原生委派处理器默认会为 Reporter 加入 read_file/write_file，因此实验 prepare_task 同步收窄 A2A 的 allowed_tools 和 metadata.tool_names，再交给原生 runtime 验证只读角色。只修改本次可信 profile，不放宽原生工具安全策略；对应集成回归覆盖完整 executor-provider 边界。

审核正文包含 `case_id`、`pass`、`needs_review`、`score`、`conclusion`、`reference` 和 `evidence[{chunk_id,quote,position}]`。`pass=false` 表示发现违规；`pass=true` 表示有充分证据通过；证据不足应设置 `needs_review=true`。

结果只从原生 FINISH 接受的、本次 run 的 Reporter Artifact 读取，不把 Central 总结当审核结果。当前运行时策略为 `json_schema_only_v2`：严格解析 JSON、检查类型/字段及 case ID，删除引用子串匹配和来源 ID 校验这一交付门槛。`evidence_evaluated=false`、`evidence_evaluation=offline_only` 明确表示尚未评测证据；格式通过不代表引用或结论正确。执行、格式、证据质量及 GT 核心错误召回分别报告。

引用可以是原文摘录或忠实概括，不要求是 chunk 子串；伪造来源、数字变化、断章取义仍是离线质量问题。原始 JSON、quote 和 RAG chunk 全部保留，不增加 JSON 解析修复、字符串替换或模型重写调用。旧 `whitespace_and_numeric_markdown_percent_v1` 批次保留历史引用校验结果，不能把 v2 格式通过率直接等同于旧策略的严格交付率。

原生“无数据”检查已修正指令/条件句误触发，真实未提供实际数据时仍保留防编造检查；长 JSON 报错显示实际触发短语。恢复提示对已声明 .json 产物使用 JSON，默认 Markdown 路径、三次 Reporter 尝试上限、调度动作及 FINISH 验收均保留。检索固有局限写入 reference，真实关键证据缺口通过 needs_review 表达，不虚构确定性。

## 独立运行

所有输出、缓存、凭据和临时运行目录位于 SP2。已有 Python/Conda 环境及 SP1 文件不变；实验不重启 Web 服务。以下命令从 SP2 根目录运行：

```bash
source .deer-flow/env.sh
python backend/scripts/run_tender_audit_sp2.py \
  --checks .deer-flow/experiments/tender-audit-inputs/审核要点_v20260910.xlsx \
  --ground-truth .deer-flow/experiments/tender-audit-inputs/真实错误汇总_current_20261014.xlsx \
  --env-file .deer-flow/experiments/tender-audit.env \
  --model qwen3-8-27b --case 1
```

项目 `.env` 提供既有 Qwen 配置使用的 `VLLM_API_KEY`；实验凭据文件提供 `RAGFLOW_API_URL`、`RAGFLOW_API_KEY`，权限为 0600，不提交凭据或运行结果。RAG 客户端禁用进程代理继承，避免把绑定的数据集请求导向其他代理。

默认 Central 和子代理均启用 thinking，模型输出上限 8192 token；case 总超时 900 秒、无可观察进展超时 180 秒。当前实验配置副本将模型 HTTP 超时设为120秒、SDK内部重试设为0，保留原生中间件重试。 当前CLI另以`--llm-request-timeout`设置原生整次请求截止（默认150秒，必须小于无进展监测180秒，0表示禁用），仅影响本CLI进程及其worker，不写Web或全局配置。每个worker及预检保存`runtime-model-settings.json`，核对真实factory/client的SDK重试、HTTP超时、输出预算和生效截止值。副本在覆盖设置后重新验证以刷新按名称索引，并测试真实客户端参数；HTTP 超时不等于整次调用墙钟上限，外层900/180秒预算另行生效。下述13-case历史批次在此缓存修正前运行，实际SDK重试仍为1次。原生子代理角色预算及 Central 恢复策略仍生效。预检分别验证模型文本、工具调用和选中 dataset 的非空检索；预检失败不会启动正式 case。预检模型输出预算为 2048，独立于 case。

仅校验 20 个 case 的输入、不访问模型或 RAG：

```bash
python backend/scripts/run_tender_audit_sp2.py \
  --checks .deer-flow/experiments/tender-audit-inputs/审核要点_v20260910.xlsx \
  --ground-truth .deer-flow/experiments/tender-audit-inputs/真实错误汇总_current_20261014.xlsx \
  --full-batch --dry-run
```

真实全量运行需要显式 `--full-batch`。当前经用户确认的执行顺序为：SDK修正单独验证case9/10 → 实验层精简/截止控制与回归 → 冻结版本下case9/10各3次和case1/18对照 → 全部通过并复核后清理约定历史结果 → 新版Qwen完整20条。任何闸门失败都不清理历史、不启动全20。DeepSeek暂不运行；未来比较仍需独立批次和相同条件。CLI 已提供 `--model deepseek-flash`，需要项目内环境中的 `DEEPSEEK_API_KEY`；未配置时立即失败，不回退到其他模型。

可用 `--batch-name NAME --resume` 续跑：仅跳过已有成功 case，失败 case 新建 attempt。代码、输入哈希和主要参数不一致时拒绝续跑，要求另建批次。`--no-thinking` 可建立单独实验，不应混入首轮 thinking 基线。

## 实验请求内证据共享与连接兼容（v4）

实验模型配置副本合并 `Connection: close`，保留其他既有请求头，避免持久连接跨异步事件循环复用；不修改全局 config.yaml 或默认模型工厂。每个 worker 的真实 SDK 参数探针会核对该请求头。SDK 重试仍为0，HTTP读取超时120秒、原生整次截止150秒、thinking及8192输出上限保持不变。

Researcher 模型请求末端将重复的相同 chunk/document/正文存成一份 `audit_shared_sources`，每次检索仍保留原有顺序、工具调用ID、命中列表和顶层元数据，各命中用 `audit_source_ref` 加字段覆盖/删除清单精确还原。所有完整来源都在当前请求内；不同正文不合并，不生成摘要、不截断原文、不减少检索条数。请求不支持或没有缩短时保留原样。共享引用只用于输入压缩，最终 evidence 仍使用真实 chunk ID。原始工具结果、图状态及原生调度/重试不变，也不增加JSON修复。

默认关闭的通用 `SPExecutionExtensions.subagent_middlewares` 接口只接受受信任Python调用者；实验层仅为Researcher添加请求投影。`model_request_projections.jsonl` 保存投影前后完整可观察消息与压缩统计，实际模型callback记录投影后的输入，原有工具日志保存完整原始检索结果。凭据和provider私有思考继续按现有策略过滤。两项改进仍需真实case验证，不能据压缩比例保证不超时。

## v5 请求精简与超时倍增

原生任务、策略、默认prompt主干、完整审核规则、标书证据正文及历史reasoning回填保持。委派请求投影只把相同original_query/latest_user_input、task/objective、验收条件与当前任务记忆重述改为同一JSON内可解析引用，保留完整权威请求、规则、输出契约、真实历史、人工反馈、条目ID/优先级/状态。不同原始/最新请求不合并，不识别的上下文保持原样。原始与投影后消息均落盘，不修改原生A2A任务对象。

证据v2对同一请求的来源全文去重，公共dataset/document字段保存一次；命中用来源索引字符串或{ref,set,drop}表示，说明只在当前请求末尾出现一次。还原时先合并公共字段与来源字段，再应用drop/set；所有正文、位置、表格关联、相似度及未知元数据均保留。未缩短请求时不使用投影。最终evidence继续使用真实chunk ID，不能使用内部s索引。

memory_recaller仍按原生首次召回执行，只报告确有来源的新增长期历史，无新增时简短说明并返回空items。Central动态Skill索引及能力元数据通过默认关闭的skill_names扩展收窄为stackplanner-long-task与stackplanner-reporting；不改变全局Skill配置。

回归通过后运行`--timeout-multiplier 2`，仅本实验进程及worker生效：HTTP120→240秒、整次请求150→300秒、RAG有效值×2、模型预检150→300秒、Central初次90→180秒/重试60→120秒、子代理各自有效timeout×2、无进展180→360秒、case900→1800秒、worker收尾45→90秒。配置中正数timeout字段也在副本内翻倍；关闭/空值不启用，重试次数、token/turn/action预算不变。基础设施隔离loop启动5→10秒、收尾1→2秒、动态上下文注入5→10秒和Skill缓存等待也同步翻倍，轮询间隔不变。

Qwen适配器的实际SDK流式等待默认来自LangChain环境（当前120秒），不是通用factory对其他OpenAI类的240秒；倍增后实际240秒，以runtime-model-settings.json为准。该适配器带tools时仍使用原生非流式调用。父子进程携带未倍增的流式等待基值，避免worker再乘一次。所有超时及投影策略、实际角色配置应以manifest/effective_config及调用轨迹核验；记录数/usage未知项如实汇报，不添加JSON修复。

## 日志与指标

默认输出在 `.deer-flow/experiments/tender-audit/<batch>/`：

| 文件/目录 | 内容 |
| --- | --- |
| `run_manifest.json`、`cases.json` | commit、适配/默认 prompt 的源码哈希、输入表和锁文件哈希、选定 case、模型和预算 |
| `preflight/` | 模型/检索预检及其独立调用日志与开销 |
| `case_XX/attempt_NNN/` | 每次运行独立的输入、配置、运行目录和原始日志 |
| `delegation_request_projections.jsonl` | 委派任务投影前后消息、role/action ID、重复字段引用与字符统计 |
| `model_request_projections.jsonl` | Researcher 请求投影前后的完整可观察消息、原文出现/唯一来源数、字符数与策略版本 |
| `model_requests.jsonl`、`model_calls.jsonl` | 可观察的完整输入、输出、工具调用参数、调用 ID、角色、时延、provider usage |
| `tool_calls.jsonl`、`tool_raw_results.jsonl`、`tool_visible_results.jsonl` | RAG 请求、状态、时延、chunk 与实际返回工具内容 |
| `reporter_evidence_inputs.jsonl`、`reporter_raw.*` | Reporter 的证据包和未经业务修复的原始响应 |
| `native-events/`、`events.jsonl`、`final_state.json` | 原生 journal、显式动作/进展、最终状态；过滤凭据与 provider 私有思考 |
| `artifacts/`、`artifact_raw.json`、`validation.json`、`result.json` | 被 FINISH 选中的原始文件、JSON/schema 格式校验与证据待离线评测标记，以及格式通过时的解析结果 |
| `metrics.json`、`attempt-summary.json` | 耗时、分角色模型调用与 token、RAG 调用/失败、chunk 数量/重复率、停止原因 |
| `aggregate_metrics.json`、`summary.json` | 全部尝试与成功尝试开销分列，预检不计入 case 成本，业务质量待独立评审 |

调用数按 LangChain 逻辑模型调用统计；SDK 内部 HTTP 重试不单列，不声称等于服务端请求数。token 仅来自 provider usage，缺失值标为未知，汇总是已知下界。异常或强制结束的 worker 从已落盘调用记录恢复成本，未返回 usage 的在途请求仍标记未知。 模型记录另存`ended_at`、`error_type`、最多8层脱敏`error_causes`以及`terminal_source`，区分provider回调和run_cleanup补记；空消息取消仍记为失败，不能据补记的超时直接推断服务端状态。旧 attempt 永不覆盖。

## 代码与测试

业务实现位于 `backend/app/experiments/tender_audit/`，CLI 位于 `backend/scripts/run_tender_audit_sp2.py`。通用扩展涉及 `sp/extensions.py`、`sp/runtime.py` 和 `subagents/executor.py`，不接收来自 API context 的可执行 hook。

记忆召回兼容修复在原生 `build_subagent_runtime_middlewares()` 中复用已有的 `SystemMessageCoalescingMiddleware`，紧随 `sp-memory-recaller` 的日期/记忆上下文中间件接入。仅模型请求中的角色 prompt 和日期提醒合成一个前置 SystemMessage，持久状态不变，用户记忆仍保持 HumanMessage。其他角色中间件链、SP2 的动作和调度策略不变，不关闭或绕过记忆召回。

测试覆盖 GT 隔离、规则拼接、绑定检索、业务错误、配置副本、路径与符号链接边界、usage 去重/脱敏、严格 JSON 格式与证据评测分离、精确 Artifact 选择，以及失败尝试成本保留。v2 回归另覆盖真实缺数据仍被拦截、指令/条件句不误触发、JSON 恢复格式、证据去重与只读来源、原生对象序列化且不修复非法 JSON。通用扩展测试同时覆盖启用和不启用路径，确保原生工具策略和 usage collector 保留。

## 首轮单 case 记录（2026-10-21）

适配及 SP2 相关回归：276 项通过。全量后端测试：7242 项通过、25 项跳过、7 项失败；7 个失败测试 ID 均在原始 HEAD 归档中复现，没有新增失败 ID。原始归档另有两项仓库标记位置相关失败。完整日志和对照位于 `.deer-flow/experiments/tender-audit-validation/`；全量测试收集之后补加的预检成本测试包含在最终 276 项回归中。

正式 case 位于 `.deer-flow/experiments/tender-audit/qwen-case01-sp1-baseline-20261021/`。模型及绑定知识库预检通过，SP2 原生调度完成研究、Reporter 注册 JSON Artifact 和 FINISH。Reporter 原始外层协议与审核正文均可直接解析为 JSON，schema 通过。离线人工对照 GT：成功发现食材质量/数量/称重不合格时，合同扣除当次货款 10%，采购需求不支付当次全部货款（100%）的冲突。

本次严格状态是 **`invalid_result`**：第一处引用将 RAG 原文 `10\%` 写成了 `10%`，缺少 Markdown 转义反斜杠。仅忽略空白的引用校验因此失败，第二处引用通过。未修改原始 JSON、未放宽验证、未进行额外模型修复，也未据此生成通过验证的 `result.json`。格式等价问题只作为诊断记录，不能计入严格通过。

case 耗时 182.21 秒，逻辑模型调用 9 次（Central 4、Researcher 3、Reporter 1、memory-recaller 1），provider 已知输入 206375、输出 5686、总计 **212061 token**。记忆召回请求返回 HTTP 400 `System message must be at the beginning.`，其消息中存在两个 SystemMessage（角色指令、日期提醒），用量未知，所以 token 汇总是下界。主流程继续完成；本次未修改通用消息构造来规避此错误。RAG 调用 4 次全部成功，返回 60 个 chunk，去重后 33 个。Reporter 原文包保留 56 个完整 chunk，记录 `truncated=true`，同时接收原生研究产物。

两次预检各有独立开销记录，未计入上述 case 数据；其中首次预检因旧 dataset ID 失败，正式 case 未启动。实际请求核验确认 Researcher/Reporter 每次模型请求均包含完整规则，所有 RAG 请求只使用绑定 dataset，运行源码哈希与 manifest 相符，日志未检出所用凭据明文。

首轮未启动 Qwen 20-case 或 DeepSeek 批次。随后按用户确认修复引用转义和记忆召回兼容问题，以新批次执行下述单 case 复验，保留首轮文件和指标。

## 修复后单 case 复验（2026-10-21）

本轮只修复两个问题：业务校验增加可审计的百分号转义等价和原文匹配分项，Reporter 增加忠实复制原文的要求；原生 memory-recaller 接入已有 SystemMessage 合并中间件。原生调度策略、默认角色 prompt、报告 skill 文件、vLLM provider 代码均未修改。模型工厂已经自动请求流式 usage，新增 MockTransport 回归确认其能正确记录无工具记忆召回的 token，无需修改全局模型配置或已有环境。

测试结果：相关回归 **377 项通过**；全量 **7261 项通过、25 项跳过、7 项已有失败**，失败测试 ID 完全不变。一次全量运行在断言统计结束后发生解释器退出异常，随后最终全量重跑正常退出，状态 1 对应原有 7 项失败。对照日志及摘要位于 `.deer-flow/experiments/tender-audit-validation/fixes-validation-summary.json`。

新运行目录为 `.deer-flow/experiments/tender-audit/qwen-case01-compat-fixes-20261021/`，与首轮使用相同表格、dataset、模型、thinking、输出预算、RAG 参数和超时。原生 Central 动作顺序为：

```text
sp_recall_memory → sp_delegate(researcher) → sp_delegate(reporter) → sp_finish
```

最终状态 **`completed`**，审核 JSON、schema、证据原文匹配全部通过。两条证据均记录 `match=exact`、`evidence_exact_valid=true`，本轮没有依赖百分号显示等价放行。原始 quote 保留了原文百分号前的反斜杠。人工离线对照 GT：`pass=false`、`needs_review=false`，正确检出合同扣除当次货款 10% 与采购需求不支付当次全部货款（100%）的冲突；两处 chunk ID 与位置均有实际检索来源。

记忆召回实际输入为一个 SystemMessage 加一个 HumanMessage，角色指令和日期均保留；调用成功，provider 用量正常返回。全部 11 次模型调用均成功，`token_usage_complete=true`。

| 指标（不含预检） | 本轮结果 |
| --- | --- |
| 耗时 | 366.07 秒 |
| 模型逻辑调用 | 11 次：Central 4、memory-recaller 1、Researcher 4、Reporter 2 |
| provider 输入 / 输出 / 合计 token | 424482 / 13076 / **437558** |
| RAG 请求 | 6 次，全部成功且仅访问绑定 dataset |
| 返回 / 唯一 chunk | 90 / 34 |
| 原文证据匹配 | 2 / 2 |

本轮较首轮增加了研究检索轮次、Reporter 对原生研究产物的一次读取，以及成功的记忆召回，耗时和用量相应上升；调度未被强制压缩。单个 case 不代表完整批次的平均开销。

Reporter 原始整段响应含简短前言，整段不能直接当作 JSON；SP2 既有传输协议解析器提取原生信封后，审核正文可以直接严格解析。已核验 Artifact 正文与 Reporter 原始 `artifact_content` 完全一致，内容哈希与原生 Artifact ref 一致，业务处理链未增加 JSON 修复、输出改写或额外重写模型调用。`single-case-review.json` 和 `case_01/attempt_001/trace-verification.json` 明确记录这一格式区别。

核验还确认每次 Researcher/Reporter 模型输入都包含完整规则、运行源码与 manifest 哈希相符、默认 prompt 与 provider 未改、日志未检出所用凭据明文。截至本次单 case 复验完成时，Qwen 完整批次和 DeepSeek 尚未启动；随后按用户确认执行下述 Qwen 全批。

## Qwen 完整 20-case 批次（2026-10-21）

目录为 `.deer-flow/experiments/tender-audit/qwen-20cases-native-20261021/`。20 条 GT 各独立运行一个 attempt，保留上述修复后的配置、thinking、原生调度与 JSON Artifact，不增加额外 JSON 修复，不重跑失败 case。DeepSeek 未运行。

| 指标 | 结果 |
| --- | --- |
| 完整运行范围 | 20/20，09:47–11:54 UTC，约 127.43 分钟（含预检及启动开销） |
| 原生 FINISH / 审核 JSON 可解析 / schema 通过 | 17 / 16 / 14 |
| 完成且通过全部交付校验 | **11/20（55%）** |
| 离线核对 GT 核心错误 | 原始文本完整命中 17、漏检 2、无可评估结果 1；包含未被接受的候选文本，不能作为最终交付率 |
| 最终可用且完整命中 GT | **11/20（55%）** |
| 模型逻辑调用 | 261 次：Central 89、memory-recaller 33、Researcher 76、Reporter 63 |
| provider 已知输入 / 输出 / 总 token | 9517049 / 261932 / **9778981**（6 次失败请求 usage 未知，合计为下界） |
| RAG | 108 次，失败 0，耗时合计 50.82 秒，返回 1620 个 chunk；逐 case 去重合计 762 个 |
| 严格成功 case 开销 | 122 次模型调用，已知 4178476 tokens，62 次 RAG |
| 无效/失败 case 开销 | 139 次模型调用，已知 5600505 tokens，46 次 RAG |

本集均为已知有错误的正例，不能用来计算正常样本误报率或总体分类准确率。17 条原始文本命中与 11 条严格可用结果须分列：case 15 的命中来自未被 FINISH 接受的候选产物，case 10 的候选产物漏检；完整命中共同核心也不代表所有附加判断或子规则解释都正确。

9 条无效/失败结果：case 1 非法百分号 JSON 转义；case 2/3/17 将 HTML 表格引用改写成纯文本/管道行；case 5 的 `score=null`；case 9 无可观测进展超时；case 10 总超时及原生修订/写文件冲突；case 15 原生“无数据”检查误触发后转人工、没有 FINISH；case 18 原生修订中将置信度误解为 0–100 综合得分，最后 `score="unknown"` 不合 schema。语义漏检为 case 5（前三名入围/仅第一名中标）与 case 10（税金/税余措辞差异）；部分规则与 GT 的范围存在歧义，逐条记录在离线核对中。本轮没有中途修复这些问题，也没有改动原系统已有的7项失败测试。

所有 case 的可观测模型完整输入输出、工具参数与原始返回、原生事件、Reporter 产物及开销均已保存；凭据脱敏，provider 私有隐藏推理不记录。183 个 JSONL 文件解析通过，请求终止记录与指标相符，模型仅 Qwen，输入、锁文件及 manifest 中源码哈希保持一致。有效配置一致；快照中未启用选项 `tool_progress.exempt_tools` 的集合显示顺序有差异，原始文件保留，离线核验仅按集合成员比较。33 次记忆召回模型调用均成功，没有复现 SystemMessage 顺序 HTTP 400；读取不存在记忆路径等工具失败另记，不当作模型兼容失败。

详细文件：

- [完整报告与逐条结果](../.deer-flow/experiments/tender-audit/qwen-20cases-native-20261021/offline_review/report.md)
- [逐条 CSV](../.deer-flow/experiments/tender-audit/qwen-20cases-native-20261021/offline_review/per_case.csv)
- [开销及各 Agent 明细](../.deer-flow/experiments/tender-audit/qwen-20cases-native-20261021/offline_review/cost_summary.json)
- [离线 GT 语义核对及边界](../.deer-flow/experiments/tender-audit/qwen-20cases-native-20261021/offline_review/reviews.json)
- [最终日志、配置与哈希核验](../.deer-flow/experiments/tender-audit/qwen-20cases-native-20261021/offline_review/final_verification.json)


## Qwen 13-case 定向验证（2026-10-21，格式门槛 v2）

13条来自此前9条未成功交付（1、2、3、5、9、10、15、17、18），加4条原成功对照（4、12、14、16），不是SP2固定样本数。仅Qwen，保留原生调度、JSON Artifact、完整轨迹与开销；case5规则不扩大，无额外JSON修复。

正式批次：`.deer-flow/experiments/tender-audit/qwen-13cases-format-v2b-20261021/`；详细报告、逐条CSV、GT/证据核对、成本及完整性核验在其 `offline_review/` 下。11/13完成原生交付并通过格式检查；最终JSON完整命中GT核心9/13。case4与5判为通过而漏检，case9与10因Researcher模型请求无可见进展被180秒监测取消，无最终结果。case15正常FINISH，未重现旧缺数据误报；case18保留0–1置信度并正确区分25/27及100/102。

| 指标 | 正式13条 |
|---|---:|
| 格式交付 | 11/13（84.6%） |
| 最终结构化GT核心完整召回 | 9/13（69.2%） |
| 离线证据评价 | supported 6、partially_supported 5、无结果2 |
| 观测墙钟 | 4095.84秒（68.26分钟，含预检） |
| case内部耗时合计 | 4075.71秒 |
| 逻辑模型调用 | 145次，2次usage未知 |
| provider已知输入/输出/合计tokens | 5626249 / 139143 / 5765392（下界） |
| RAG | 74次，全部成功，累计36.27秒 |
| 返回chunks / 逐case去重合计 | 1110 / 520 |

本组均为有错误正例，上述是交付率和GT核心召回，不是总体分类准确率。同样13条旧结果：旧引用门槛下交付4条，仅将其原生接受的最终产物按新格式门槛重算则已有7条；本次11条应与7条比较。删除引用门槛不能全部算成模型能力提升。case4旧版曾检出，本次看到商务/技术后续排序差异却聚焦方向判通过，构成语义退化；case5保留规则/GT范围差异。case2、3、12虽命中核心，仍有定位或附加判断瑕疵，已离线单列。

本次还暴露两项适配边界：首次启动因原生Reporter自动附加write_file与只读工具配置冲突而中止，仅尝试case1；修正prepare hook并全量回归后才启动正式批次。中止记录保留17次模型调用、至少311959 tokens、4次RAG、约361.11秒，未混入正式13条。正式+中止两次预检另计4次模型调用、737 tokens、10次RAG；本轮服务调用总计166次、至少6078088 tokens、88次RAG，3次usage未知。详见 `offline_review/session_costs.json`。

正式运行中源码保持冻结，结束后118个JSONL可解析，请求均有终止记录，原生聚合与离线计数一致，来源/输入/锁文件哈希及产物哈希核对通过。配置探针发现缓存不符：快照写SDK max_retries=0，原生按名称查询仍取旧ModelConfig，实际客户端为1；`all_checks_passed=false`如实保留该项，不能声称本批次关闭了SDK重试。实际请求只使用Qwen。运行源码快照、参数探针及验证时间保存在 `offline_review/`。

批次结束、归档及核验后，才重新验证实验AppConfig副本以刷新索引，并新增测试核对真实factory/client参数及全局配置不变；本轮13条结果仍属于修正前源码，不回写、不额外重跑。后续可另验证原生整次请求deadline与无进展监测的配合。case15虽交付成功但多次重复读取，且显式metadata.skill_names仍加载reporting Skill；需后续评估适配层Skill选择和重复读取限制，共现不等于因果。

批次前最终回归为7305通过、25跳过、同7项已有失败，定向314通过；批次后配置索引修正新增1个回归测试，最终全量为**7306通过、25跳过、同7项已有失败**，定向275通过，ruff及diff检查通过。对应日志与摘要在 `.deer-flow/experiments/tender-audit-validation/v2c-*`，未增加真实模型调用。


## 2026-10-22 稳定性闸门：未通过，历史未清理，完整20条未启动

本轮按确认方案完成SDK单独基线、实验层改进及回归，再冻结源码验证。SDK基线case9完成且命中40/43，但有3次120秒请求超时；case10完成且无请求超时，但未命中GT税金/税余核心。随后增加输入字段投影、原生150秒整次截止、真实客户端参数核验和异常原因链。13项新增测试先复现12项失败/1项通过，最终定向320通过；全量7319通过、25跳过、同7项已有失败。

冻结版本下case9第1次485.86秒、case10第1次353.45秒，均正常FINISH且零请求超时，两条GT核心均被提及，但有附加解释瑕疵。case9第2次Researcher请求在120.114秒发生APITimeoutError→ReadTimeout；实际SDK重试0、HTTP120、原生整次截止150均核验，故不是旧缓存不生效，也不是外层180秒监测触发。可见消息已累积到114873字符。验证器按零超时闸门主动停止该轮和后续验证：worker_failed/-2是主动终止记账，不是另一个独立SP2故障。共尝试3次目标验证（2通过1失败），剩余3次及2个对照未启动。

因此没有执行任何历史清理，没有启动新版完整20条。旧数据、失败轨迹和全部本轮开销均保留。当前阶段报告在`.deer-flow/experiments/tender-audit-current-20261022/phase_report.json`，闸门与回归摘要在同目录。含SDK基线、3次闸门及全部预检：103次逻辑调用、至少2059306 tokens、40次RAG全部成功；33次usage未知，合计为下界。调用数不是HTTP尝试数，未推算金额。

原因链另外确认快速APIConnectionError来自event loop绑定错误。仅用本地模拟HTTP服务复现：默认连接复用跨两个loop失败，Connection:close时两次均成功；尚未修改真实实验配置。失败输入的同请求共享证据包离线模拟可从114873减到80637字符，75次来源出现合为43份全文，全部原文及元数据能精确重建；此方法尚未实现或真实模型验证。后续应另确认补充方案，按新冻结版本重做完整闸门，不以本版两次成功凑数。case4/5规则、原生调度和无JSON修复约束保持，DeepSeek未跑。


## v4 回归与单次闸门：模型预检连接失败，未清理历史

两项改进已实现并冻结：实验客户端合并Connection:close；Researcher在单个模型请求内无损共享重复来源。默认关闭的通用middleware扩展不改变原生调度和默认prompt。离线重放上一轮失败请求，75次来源出现合为43份全文，序列化消息116140→81963字符（该口径包含完整消息字段，区别于旧报告的仅可见inputs长度）；75次原文及元数据均能精确还原。本地跨loop模拟默认复用失败，关闭连接时两次成功。

回归：定向295通过；CI=1全量7312通过、25跳过、同7项已知失败。首次修改前基线误包含需实际服务的client_live模块，另有4项因模型连接被拒绝而失败；后续使用仓库CI开关跳过live模块，因此不能直接与旧批次的通过数比较。真实模型由独立闸门验证。ruff及diff检查通过。

本轮按确认要求仅安排case9/10各一次，但case9的模型预检在6.388ms即报APIConnectionError→ConnectError→ConnectionRefusedError，目标为http://127.0.0.1:18071/v1；只读监听检查也未发现18071监听。不是ReadTimeout，没有进入case9审核，case10未启动。SDK0、HTTP120、原生deadline150、thinking开启、8192输出上限和Connection:close均经真实factory/client参数核验。遵守失败停止，没有重建隧道、更换端点或自动再试。

审核验证仅产生1次失败预检逻辑调用，usage未知，0次RAG；已知token合计0仅是已知下界。首次基线的live连接探测另记，不混入审核指标。此前15个结果目录、49个验证结果文件均未删除，完整20条未启动，DeepSeek未跑。case4/5及其他规则哈希保持。阶段报告、源码快照、测试日志、预检完整可观察轨迹与开销在`.deer-flow/experiments/tender-audit-v4-20261022/`；当前需要先恢复Qwen本地转发接入才能重新验证。


## Qwen 本地转发恢复（2026-10-22）

故障位于本地接入：18071没有监听，已有18070到同一SSH服务器8070的转发仍正常，使用相同凭据查询返回qwen3.8-27b。未找到原18071进程退出日志，不能进一步认定其退出原因。已用现有SSH身份后台重建127.0.0.1:18071→远端127.0.0.1:8070，保留SP2原模型地址，未修改现有18070转发或SSH凭据/known-hosts。

本地辅助命令：`bash .deer-flow/qwen-tunnel.sh start`建立转发，`bash .deer-flow/qwen-tunnel.sh status`查看监听；已有18071监听时不替换。日志只写SP2的`.deer-flow/setup/qwen-recovery-20261022/ssh-tunnel.log`。SSH在后台运行，并设置30秒keepalive、3次失联退出；该辅助脚本不提供自动重连。

恢复后模型清单HTTP200，最小非thinking文本请求正常回复OK，provider usage为输入22、输出2、合计24 tokens。模型请求及开销完整保存在同目录model-smoke/；报告为recovery_report.json。本次仅恢复和最小连通验证，未重启case9/10审核、历史清理或完整20条，DeepSeek未运行；冻结实验源码哈希未变化。


## 恢复连接后的单次闸门：case9读取超时，已停止（2026-10-22）

连接恢复后按用户确认，沿用v4冻结源码与既有回归结果，从头安排case9/10各一次。独立目录为`.deer-flow/experiments/tender-audit-v4-resumed-20261022/`，不覆盖此前预检失败记录。Qwen文本/工具预检和绑定知识库预检通过，case9进入原生研究流程。

case9执行到第6次检索后，Researcher模型调用在120.108666秒报APITimeoutError→ReadTimeout，闸门主动终止；恢复记账时长约324.87秒。worker_failed/-2为主动终止状态，不是另一个独立故障或900秒总超时。未进入Reporter交付，因此没有可评估的最终审核结论。case10、历史清理、完整20条均未启动，既有15个结果目录及49个验证结果文件全部保留。

本次无连接类错误，Connection:close与SDK0、HTTP120、原生deadline150均经实际客户端核验。证据投影应用4次，全部正文及元数据可精确还原；最终90次来源出现合为35份全文。投影器完整消息对象口径196796→143078字符，其计数含可观察日志中省略的字段，不能称为可见输入长度；失败请求的可观察inputs序列化为76645字符。两项改进实际生效，但仍不足以消除本次读取超时；服务端逐请求排队/输入处理/生成时序未取得，不能进一步断定是哪一阶段耗尽等待。

含预检：10次逻辑模型调用，1次失败且usage未知，已知输入128440、输出9105、合计至少137545 tokens；7次RAG全部成功（case内6次、预检1次），RAG耗时合计3.278002秒。case内单列8次模型调用、至少137181 tokens。全部模型请求均有终止记录，13个JSONL可解析，未发现配置凭据出现在日志中，冻结源码/输入及20条规则哈希保持。原生调度、case4/5原口径、无JSON修复、仅SP2写入、DeepSeek不跑约束保持。完整阶段报告为同目录phase_report.json。


## v5 输入精简与双倍超时：case9/10各一次通过（2026-10-22）

按用户确认实施前三项输入精简：委派消息对重复任务/规则/schema使用同请求内引用；Researcher证据正文按来源共享，公共元数据提取且保留差异覆盖；记忆召回只要求新增历史信息，Central收窄Skill索引。完整权威规则、证据正文、历史reasoning保留，实际来源字段可精确还原。原生调度不变，未增加JSON修复，case4/5口径未变。

新增14项回归；定向362通过，全量7326通过、25跳过、同7项已有失败，无新增失败。ruff和diff检查通过。回归后以`--timeout-multiplier 2`运行独立审核进程，实际HTTP与SDK stream计时120→240秒、原生整次请求150→300秒、无进展180→360秒、case900→1800秒；其他相关内部超时同步加倍，清单与实际客户端探针在`timeout_inventory.json`。默认倍率仍为1，不修改既有服务的环境。

从头独立执行case9/10各一次，均正常原生FINISH、JSON/schema有效、零模型请求失败/超时，全部usage已知。case9耗时454.69秒，11次模型调用、225761 tokens、7次RAG；case10耗时472.00秒，15次模型调用、321954 tokens、6次RAG。case9最长单次请求150.53秒完成。两组另有4次模型预检共715 tokens和2次RAG；含预检共30次模型调用、548430 tokens、15次RAG全部成功。开销指provider token，不推算金额。

离线核对：case9正确检出40/43分，case10检出税余/税金措辞差异。两条`pass=false`表示发现文档问题，不是运行失败。case10附加的付款条款冲突判断部分可能属于补充约定，仍需人工判断，不能全部计为正确。一次成功不能保证今后不超时。

运行与完整逐调用输入/输出、工具结果、来源投影及开销保存于`.deer-flow/experiments/tender-audit-v5-20261022/`。完整性核验确认32个JSONL可解析、原生产物哈希一致、冻结源码和20条规则哈希不变、来源正文可精确还原。逐调用耗时及token在`per_model_call.csv`与`per_model_call.json`，完整阶段报告为`phase_report.json`，离线语义核对为`offline_reviews.json`。

当前等待用户检查本轮报告，历史结果全部保留，完整Qwen20条尚未启动，DeepSeek未运行。


## 新版完整Qwen20条：v5双倍超时（2026-10-22）

用户检查case9/10单次闸门报告后授权清理和全量测试。严格删除既定15个历史结果目录和49个验证结果文件（共1857文件、217152628字节），保留输入表格、凭据/环境配置、辅助脚本、原始源码及v4/v5闸门轨迹。清单记录在`.deer-flow/experiments/tender-audit-qwen20-v5-20261022/cleanup_manifest.json`。上文已删除目录中的历史结果链接不再有效；历史文字摘要保留，新版结果以下述批次为准。

本轮不修改运行源码，沿用已回归的v5冻结版本，使用`--full-batch --timeout-multiplier 2`，thinking开启、8192输出上限、SDK重试0、HTTP与SDK stream 240秒、原生整次请求300秒、无进展360秒、case1800秒。case4/5原口径、原生调度、完整规则/证据正文/历史reasoning保留，无JSON修复。20条各从头运行1次，没有额外重跑，DeepSeek未运行。

批次目录为`.deer-flow/experiments/tender-audit/qwen-20cases-v5-20261022/`。北京时间2026-10-22 16:39:11至18:33:10，观测墙钟6839.00秒（113.98分钟）。原生FINISH 20条，JSON/schema有效交付19条（95%），最终结构化GT核心完整命中17条（85%）。原始文本含非法产物命中18条（90%）；仅看有效结果`pass=false`为18条，其中case5发现的是同文件另一真实问题，不能算该case指定GT命中。全为已知错误正例，不能估计正常标书误报率或总体分类准确率。

三条未形成指定GT可用结果：case4仅判断主排序方向，漏掉原文已检索到的商务优先/技术优先差异；case5发现case4对应的排序差异，未命中前三名入围/第一名中标；case15原始文本正确指出G250101/LNXY-XFYJS-2025-006，但Reporter信封reference含未转义双引号，整个最终文件JSON非法。case15原生报告族验收仍FINISH，实验层格式检查记`invalid_result`，未修复或重跑。CLI返回1表示这条格式失败，完整20条实际均已执行完。

离线证据评价supported 13、partially_supported 6、无有效结构化结果1。case9虽命中40/43，但误把跨页“计划图”当独立空白项；case10、13、19部分附加冲突判断需复核；case12误把“小时内”上界当固定值。case13还对调了包001/002的位置标签。GT核心命中不等于报告全部断言正确。case4首轮占位检索触发原生loop_capped及补证；case4/18的memory-recaller各发生一次目录误读，后续恢复。

审核内250次模型调用，输入4838776、输出293529、合计5132305 tokens；146次RAG全部成功，检索调用耗时合计120.072496秒。另计预检2次模型/366 tokens及8次RAG。整个新版批次含预检252次模型调用、5132671 tokens、154次RAG，模型失败/超时0、usage缺失0，RAG失败0。费用以provider token为准，无货币或GPU遥测。审核内Researcher占46.20% tokens、Reporter占34.17%。所有失败开销均计入，未混入此前闸门调用。

核验全部通过：225个JSONL可解析，请求均有终止记录，来源投影逐字段精确还原，原生产物哈希、输入、锁文件、20条规则及冻结源码一致，实际只请求Qwen；未检出配置凭据明文。配置快照中未启用的`tool_progress.exempt_tools`集合显示顺序不同，仅离线按成员比较，原快照保留，其他有效配置一致。

结果入口：

- [完整可读报告](../.deer-flow/experiments/tender-audit/qwen-20cases-v5-20261022/offline_review/report.txt)
- [20条完整结果CSV](../.deer-flow/experiments/tender-audit/qwen-20cases-v5-20261022/offline_review/complete_results.csv)
- [20条完整结果JSON](../.deer-flow/experiments/tender-audit/qwen-20cases-v5-20261022/offline_review/complete_results.json)
- [逐次模型调用CSV](../.deer-flow/experiments/tender-audit-qwen20-v5-20261022/per_model_call.csv)
- [审核内开销与Agent明细](../.deer-flow/experiments/tender-audit/qwen-20cases-v5-20261022/offline_review/cost_summary.json)
- [含预检总开销](../.deer-flow/experiments/tender-audit/qwen-20cases-v5-20261022/offline_review/all_costs_including_preflight.json)
- [最终完整性核验](../.deer-flow/experiments/tender-audit/qwen-20cases-v5-20261022/offline_review/final_verification.json)
- [阶段总报告](../.deer-flow/experiments/tender-audit-qwen20-v5-20261022/phase_report.json)


## DS V4.1 Flash完整20条已启动（2026-10-22）

用户授权保留新版Qwen20条全部结果，在同条件下启动独立DS20条批次。Qwen批次与控制目录的1426个原文件哈希均保留并核验；本轮不清理历史。DS批次为`.deer-flow/experiments/tender-audit/ds-20cases-v5-20261022/`，控制与启动记录在`.deer-flow/experiments/tender-audit-ds20-v5-20261022/`。

API拒绝`ds-v4.1-flash`字面名称；按[官方当前模型说明](https://api-docs.deepseek.com/)，使用`deepseek-flash`访问DeepSeek-V4.1-Flash-1017。保留名称探测错误记录，不混入正式case开销。使用仓库已有`deerflow.models.patched_deepseek:PatchedChatDeepSeek`保留官方工具多轮要求的reasoning_content，无运行源码修改、无JSON修复。独立模型配置与0600权限凭据文件均在本轮控制目录，默认config.yaml及Qwen环境不变；密钥不写入文档或命令行。

实际验证通过：文本、流式工具调用、工具结果回传后的续答，3次模型调用共808 tokens；43项既有provider/reasoning回传/审核投影定向测试通过。直接照API别名调用的早期400记录另存model-probe-invalid-alias/。运行时参数与Qwen一致：HTTP/SDK240秒、原生请求300秒、无进展360秒、case1800秒、SDK重试0、thinking开启、max_tokens8192。除模型端点/凭据/名称、现有provider及thinking协议字段外，应用配置核验与Qwen一致。case4/5及全部20条规则哈希不变，保留完整规则、证据、历史reasoning和原生调度。

北京时间2026-10-22 18:50启动后台批次；批次文本/工具预检和8个知识库预检通过后，从case1开始顺序执行，各一次。运行状态为WORK_LOG.json，控制日志controller.log，批次日志batch-launcher.log。CLI逐case保存完整可观察轨迹、原始RAG响应、JSON Artifact与开销；结束后保留summary.json、aggregate_metrics.json，并自动导出逐次模型调用CSV/JSON。审核准确性仍需结束后离线核对，不把成功执行当语义正确。


## DS完整20条离线复核及与保留Qwen批次对比（2026-10-22）

DS批次已完成并离线复核，不新增模型调用、重跑或JSON修复。北京时间18:50:49至19:16:41，观测墙钟1551.55秒（25.86分钟）；控制器完整生命周期1555.90秒包含启动/结束轮询，审核case时长之和1521.13秒（25.35分钟）。每条仅attempt_001，20条均原生FINISH，最终JSON/schema全部有效。实际API模型为启动阶段确认的`deepseek-flash`，独立配置名仍为`ds-v4.1-flash`。

沿用同一GT和完整规则，case4/5不扩大口径：DS最终结构化GT核心命中17/20（85%），与保留Qwen17/20相同；格式交付DS20/20（100%）、Qwen19/20（95%）。两者共同命中15条，DS独有命中4、15，Qwen独有10、19，共同漏判5。DS原始文本GT核心也为17/20，Qwen为18/20（case15原始文本命中但JSON非法）。DS `pass=false`19条，case19为true，全部needs_review=false；19条不通过不能当作19条指定GT命中。全为已知错误正例，不能据此估计正常标书误报率或总体分类准确率。

DS三条漏判均为语义/范围判断：case5已读到前三名入围与第一名中标，但认为主排序方向一致，以同文件商务/技术优先级的其他真实问题作为结论；原规则聚焦排序方向而GT关注人数的范围张力保留。case10明确看到税余/税金，认为仅字面不同、实质一致，报告其他质保金条款问题，按固定GT不计命中。case19准确引用领取中标通知书同时/领取成交通知书同时，认为同一支付节点、只称谓不统一，判通过；按现有GT记漏判，同时披露GT是否把模板称谓差异等同时间差异的解释边界。不擅自重标GT，也不以“提到词语”代替检出。

人工来源与论证复核：DS supported15、partially_supported5（3、5、12、13、16）。所有引用chunk ID均在本case已返回来源中；不要求quote为连续子串。partial包括case3将未齐发票顺延与收到发票后期限混为同条件、case5章节号误写、case12把小时内上界当固定值辅证、case13部分可能互补的付款条件及保证金期限上限解释、case16用第1日领取并支付作为不符合七日内的错误例子。命中GT不等于全部附加断言正确。DS9正确识别“计划图”为跨页续行，DS13未把70%与100%加成170%，DS18正确把102分作为25/27差异派生；case17额外评分观察在需求章节之外，未加分。

| 指标（含批次预检） | DS | 保留Qwen |
| --- | ---: | ---: |
| 观测墙钟分钟 | 25.86 | 113.98 |
| 模型逻辑调用 | 274 | 252 |
| 输入token | 4,817,052 | 4,839,070 |
| 输出token | 286,290 | 293,601 |
| 总token | 5,103,342 | 5,132,671 |
| RAG调用 | 176 | 154 |
| RAG调用时长之和（秒） | 70.287873 | 122.920981 |
| 模型请求失败/超时、usage缺失 | 0 | 0 |
| RAG失败 | 0 | 0 |

DS墙钟减少77.31%，本轮Qwen耗时约DS的4.41倍；总token只减少0.571%，模型调用反而增加22次（8.73%），RAG增加22次（14.29%）。审核内DS为272次模型、5,102,926 tokens、168次RAG；批次预检另2次模型/416 tokens和8次RAG。审核内单次模型耗时DS平均5.27秒、P95 15.55秒、最长28.15秒；Qwen平均26.80秒、P95 71.44秒、最长152.36秒。DS提速主要来自调用时延而非总token下降，但端点、provider、分词器、硬件、负载、缓存均不同，不能据此隔离模型固有能力；每case仅1次也不构成稳定性保证。

DS的输入缓存命中由provider报告1,992,576 tokens，占输入41.37%，已包含在输入总量中；Qwen没有对应明细，不能当作零缓存。无独立reasoning token明细、首token时序、实际账单或本地GPU成本遥测，不估算金额。全部274条审核/预检调用记录均为stream=false，未返回first_token_ms。启动前另有3次成功模型探测共808 tokens、2次无效模型字面名400且usage未知，保留但不混入正式批次开销。

原生调度保持：DS自主增加perception（case2两次、case14四次模型调用），48次委派；没有Qwen case4的loop_capped。DS case11记忆召回read_file读取尚不存在的记忆文件，返回一次FileNotFoundError，随后正常交付且命中GT；Qwen两次目录读取错误同样恢复。此类工具错误与GT漏判、模型请求失败分别记账。

离线完整性核验全部通过：225个JSONL可解析，全部请求终止、usage/metrics/aggregate一致，原生产物哈希与来源无损重建正确，完整规则存在于每次Researcher/Reporter请求；冻结运行源码、provider源码、输入、锁文件、规则及独立DS配置不变。双倍超时和无额外JSON修复设置保持。保留Qwen两个目录的1426个原文件逐一SHA256一致，未修改或清理。完整轨迹及开销保留，供应商未提供的内部信息不虚构。

结果入口：

- [DS完整可读报告及逐条原始结论/证据](../.deer-flow/experiments/tender-audit/ds-20cases-v5-20261022/offline_review/report.txt)
- [两模型20条逐条对比CSV](../.deer-flow/experiments/tender-audit/ds-20cases-v5-20261022/offline_review/qwen_vs_ds/per_case_comparison.csv)
- [两模型对比总表JSON](../.deer-flow/experiments/tender-audit/ds-20cases-v5-20261022/offline_review/qwen_vs_ds/summary.json)
- [DS完整结果CSV](../.deer-flow/experiments/tender-audit/ds-20cases-v5-20261022/offline_review/complete_results.csv)
- [DS逐次模型调用CSV](../.deer-flow/experiments/tender-audit-ds20-v5-20261022/per_model_call.csv)
- [DS最终完整性核验](../.deer-flow/experiments/tender-audit/ds-20cases-v5-20261022/offline_review/final_verification.json)
- [DS阶段总报告](../.deer-flow/experiments/tender-audit-ds20-v5-20261022/phase_report.json)


## Qwen case15单次重跑成功并替换（2026-10-22）

用户要求不修改代码、只重跑case15一次，成功后完全替换旧失败结果。本次沿用同一冻结运行源码、模型与有效配置，超时倍率2、SDK0、thinking开启、max8192、原生调度及无JSON修复保持。一次重跑原生FINISH，JSON/schema有效，正确检出封面`G250101`与公告`LNXY-XFYJS-2025-006`编号差异，pass=false、needs_review=false。

审核耗时261.157396秒；模型11次，输入206074、输出10383、合计216457 tokens；RAG6次，模型/RAG失败0。重跑预检另2次模型/368 tokens和1次RAG；本次新运行含预检共13次模型/216825 tokens、7次RAG。新模型输出、工具结果、Artifact未修复或改写。

旧Qwen case15失败目录已删除（64文件、4803977字节），新case实际目录接入原`qwen-20cases-v5-20261022/case_15/`；为使新原始轨迹里的创建路径继续有效，独立重跑批次的case15路径作为指向该实际目录的SP2内符号链接保留，不重复存放。其余19条Qwen及全部20条DS原始case文件逐一哈希保持。仅修改实验结果、派生报表和本说明，没有修改代码、默认配置或既有环境。

当前Qwen20由原19条加case15一次成功重跑构成，并非重新连续运行的全20条。因此当前格式交付20/20（100%）、最终结构化GT核心18/20（90%），未命中4/5；DS仍为20/20交付、17/20命中，未命中5/10/19。对比表已同步：共同命中16，DS独有4，Qwen独有10/19，共同漏判5。该对比包含Qwen失败后择用成功重跑，不能当作独立一次性全量基准；上文原始一次性结果及4.41倍墙钟比较仅是当时历史记录，当前组合不具有连续全量墙钟时间。

当前所保留Qwen20审核内共251次模型、5182984 tokens、147次RAG，case时长之和6861.242237秒。加原全量批次预检为253次模型、5183350 tokens、155次RAG；额外case15重跑预检单列。若两个实际预检均计入当前所保留结果，则255次模型、5183718 tokens、156次RAG。旧失败尝试已从当前结果/开销中排除，不能将选取后的成本当作从未重跑的全部历史实际支出。

[替换记录](../.deer-flow/experiments/tender-audit/qwen-20cases-v5-20261022/case15_replacement.json)、[本次重跑报告](../.deer-flow/experiments/tender-audit-qwen-case15-replacement-20261022T122805Z/rerun_report.json)、[更新后Qwen完整结果](../.deer-flow/experiments/tender-audit/qwen-20cases-v5-20261022/offline_review/complete_results.csv)、[更新后Qwen/DS逐条对比](../.deer-flow/experiments/tender-audit/ds-20cases-v5-20261022/offline_review/qwen_vs_ds/per_case_comparison.csv)。
