# SP 系统 DCC 数据加载与预处理实现概述

通过独立的数据准备层，将数据集适配为 SP 的文档筛查任务。当前发布 FinAuditing 和 RuleArena NBA；加载器、处理插件、文档接入模式和 prompt 片段可独立替换。

## 处理流程

原始数据 → 统一样本 → 文档与规则分离 → 按模式建库或直接输入 → 组装 prompt → 接入 SP。

每个样本统一包含问题、待审文档、规则和答案标签。长文档送入 RAGFlow，短场景直接进入 prompt；规则始终作为文件供 agent 读取，标签单独保存。

## 原始数据处理范围

FinAuditing 基线只执行一次 JSON 解码，按原始标题拆分问题、六类 XBRL 文档、任务说明和 US GAAP Taxonomy；整理边界空白，移除末尾空 `Answer:` 标记，记录原文的 `None` 缺失文档，并对相同文件做本地存储去重。

问题、文档正文、数值、标签及官方 test 划分均保留。FinRE、FinMR、FinSM 的任务要求和输出格式直接使用各自的原始说明。基线 `processors` 为空，不增加摘要、推理、数据合成或按答案筛选证据；原始 Parquet 同时随包保留。发布时逐条核对全部输入和标签。

NBA 按官方三段格式拼接球队情况、球员情况和操作，场景全文直接输入，完整 CBA 规则保留为文件。保留原始历史工资假设、zero-shot 指令与输出要求，`True` 表示违规；不补全工资表或修正标签。三个难度等级保留为元数据，不当作训练／验证／测试划分。

## RAGFlow 与 SP 接入

- **独立建库**：每个样本一个知识库，同一题的关联文档共库；工具固定检索当前题目的库及文档。
- **自动建库**：脚本完成上传、切片、索引与状态检查，支持有限并发和中断续跑。基线使用 `naive`、512 token，关闭自动关键词和问题生成，参数独立记录、可配置。
- **Prompt 注入**：通用、数据集和单样本片段分层组合。任务说明直接注入；规则与 taxonomy 由 agent 读文件，文档证据通过 `ragflow_retrieve` 获取。
- **原生插件接入**：复用 SP 的 `BenchmarkPlugin`、PublicTask 和文件上传机制。NBA 使用 `dcc_inline` 插件，直接输入完整场景，不注册检索工具、不需要 RAGFlow Key；不修改 agent 执行循环。

## 跨服务器复用

处理好的数据通过[同仓库 GitHub Release 附件](https://github.com/PKU-SIS/StackPlanner2.0/releases/tag/dcc-finauditing-v1)发布，包含原始数据、规范化输入、规则、单独保存的标签、prompt、配置及已有知识库映射。版本和校验值由仓库内的发布清单固定。

其他人 clone 后，使用已登录的 GitHub CLI 下载：

```bash
python scripts/dcc_preprocess.py download \
  --release finauditing-v1 --output .benchmark-data/finauditing-v1
export DCC_BUNDLE="$PWD/.benchmark-data/finauditing-v1/bundle"
export DCC_CONFIG="$PWD/.benchmark-data/finauditing-v1/config.json"
```

配置共享 RAGFlow 的 API Key 后即可使用；不需要自己部署服务或重新建库。数据与 prompt 路径均随安装目录解析。下载过程校验压缩包、文件和样本映射，不修改远端知识库。

NBA 使用同样的下载命令，版本和目录均改为 `rulearena-nba-v1`，无需 RAGFlow。见 [NBA 数据包与使用说明](DCC_RULEARENA_NBA.md)。

## 本次交付

FinAuditing 共 **1,102 条**：FinRE 440、FinMR 332、FinSM 330，已完成全量预处理、独立建库和发布。NBA 共 **216 条**：三个难度分别为 81、89、46，已完成直接输入适配与发布。两种模式均覆盖数据核对、下载校验和 SP 输入接入测试。

本次范围为数据准备和接入；DCC 各子任务评分后续接到仓库现有通用评测器。扩展或优化预处理时使用独立配置和新数据包版本。依赖安装、运行和发布命令见 [详细使用说明](DCC_PREPROCESSING.md)。
