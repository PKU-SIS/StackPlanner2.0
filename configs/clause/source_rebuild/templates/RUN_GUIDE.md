# CLAUSE Eval 3 文本内部矛盾：服务器运行说明

更新日期：2026-10-30。当前实验版本：`intext-yes-whole-document-v1`。本文是面向服务器执行与大模型读取的 Markdown 工作说明，已移除旧版附录 A—C 的全部逐类别详细结果表。保留任务定义、必要统计、输入输出、指标协议和历史基线概览；增加本次质量筛选、数据划分与实际运行命令。旧 PDF 是划分前的历史阅读版，不再作为当前运行配置。

所有相对路径默认以解压后的 `CLAUSE_Eval3_InText_ready/` 为根目录。默认只在 `test` 集运行，`l1` 提示，不调用 LLM Judge。现有原始下载不变，本次筛选和划分另存为运行包。

## 1. 上传哪些文件，还缺少什么

本地原始作者快照：`upstream/CLAUSE/`，其中 `datasets/` 包含公开的全部原始数据，`All_Exp_codes/` 包含作者发布脚本，`paper/` 包含论文 LaTeX。它不是现成的服务器可运行项目：脚本有作者本机路径、命名约定、提示版本差异，部分代码还会调用 Judge 或自动下载资源。

本次应上传整个 `CLAUSE_Eval3_InText_ready/`，或同名 `.tar.gz` 压缩包；不必上传约 500 MB 的全部上游资料。运行包完整包含本次使用的 5,026 份内部矛盾文档、对应全部 GT、固定划分、两套内部提示、请求导出、可选推理客户端、无 Judge 评分代码、核验与来源记录。

原始快照和专项包都包含数据与文本，但“复制数据后即可运行”仍取决于服务器环境及模型准备。以下依赖状态需区分：

| 内容 | 包中状态 | 服务器还需做什么 |
| --- | --- | --- |
| 筛选后的输入与 GT、train/validation/test 清单 | 完整包含 | 无需重新下载 CLAUSE 数据 |
| L1/L2 提示、导出与本地评分代码 | 完整包含 | 修改运行命令中的模型和输出目录 |
| 英文 Punkt 分句器、WordNet | 已下载并包含 | 无需临时下载 NLTK 资源 |
| Python 包 | 列出依赖文件；未包含 Linux 安装包 | 建议 Python 3.10/3.11；安装 requirements 文件 |
| BERTScore 的 RoBERTa-large 权重 | 未下载；附固定版本下载脚本 | 服务器预下载约 1.4 GB 权重，或先显式跳过 BERTScore |
| 被测 LLM 的推理能力 | 不包含模型权重或服务 | 使用你自己的模型/推理框架/服务；先生成预测 |
| Explanation Judge / Law Judge | 当前流程不启用 | 不需要 Judge API key、Judge 推理或费用 |

BERTScore 的预训练编码器属于本地相似度计算资源，不是 LLM-as-Judge。五项相似度中的 BERTScore 需要这个编码器；ROUGE/METEOR/Miss/Extra 不需要。被测模型推理仍是必须的一步：“不跑 Judge”不等于“不用模型生成答案就能评测能力”。评分代码读取已经生成的预测，单靠数据文件不会产生模型成绩。

若服务器可联网，在服务器安装 Python 依赖并运行下载脚本即可。若服务器完全离线，需要提前在可联网机器下载 RoBERTa 权重并放入同一包的 `resources/models/roberta-large/`，同时准备适配服务器 OS/Python/CUDA 的 wheels 或容器。不要直接把本机 macOS 的 Python 安装目录当成 Linux 依赖复制。

## 2. 任务、来源与原始规模

只运行 Eval 3 / In-Text Contradiction：输入一份扰动后的完整英语合同，输出全部内部问题的原文片段与解释。不运行 Eval 1、Eval 2，不检查外部法律，不计算 Law Match，也不以其他任务预测结果作门控。

五类问题为：歧义、不一致、术语不一致、遗漏、结构缺陷。它们既包括相互排斥的条款，也包括内部定义冲突、引用了缺失内容、交叉引用断裂等较宽的文档缺陷。标准预测不要求输出类别。来源为 CUAD 商业合同与 ContractNLI（简称 NLI，主要是保密协议）。它仍是合同领域样本，不能单独证明对所有文档类型的通用检查能力。

原论文只报告基模型推理评测，没有在发布物中提供可直接认证的本内部子集 train/dev/test manifest。因此可以把发布内容视为“待划分的评测样本池”；不能把它称为作者正式公布、训练用途上已经隔离好的测试集。本次 train/validation/test 是为后续训练方法新增的自定义划分，不是论文官方划分。模型预训练是否接触过原合同也不是此次分组检查能判定的。

| 口径 | 标注文件 | 非空成对文件 | 扰动条目 |
| --- | --- | --- | --- |
| 原论文表 1：内部子集 | 3776 | 未单列 | 12448 |
| 当前公开快照：内部子集 | 5160 | 5132 | 18513 |
| 本次筛选后：实际使用 | 5026 | 5026 | 18192 |

公开包另有 6 份无同名 GT 的内部 TXT，未进入候选标注集合，亦未加入运行包。公开包与论文原始规模不一致，筛选与划分又进一步改变了集合；后续结果不能直接称为复现论文原总分。

筛选前长度供估算资源：5,132 份非空全文删除位置标记后，平均约 31,267 字符，中位数约 14,884，P95 约 120,456，最大 338,388；CUAD 中位数约 32,846，NLI 约 10,246。这里是 Python 字符数，不是模型 token 数，也不是本次测试集的独立长度统计。使用自己的 tokenizer 检查输入提示、全文与输出上限，禁止静默截断。

## 3. 本次质量筛选：已实际执行

原始快照固定提交：`c81c6a5251771cab3d9f3d081f71c388957cf936`。筛选依据是发布 JSON 中原有的 `contradiction_exists` 字段，严格要求每项等于字符串 `YES`。这代表原模型核验标签通过，不等于本次获得了逐条人工/律师认证。

采用“整份文档保留或排除”的规则：

1. `perturbation` 列表必须非空，且能配对到完整输入。
2. 所有扰动都必须是 `YES`。一项为 `NO` 或 `null`，整份文档排除。
3. `original_text`、`changed_text`、`contradicted_text` 为字符串，且每项至少有一边可用作匹配目标；每项 `explanation` 必须非空。
4. 不在保持输入不变的同时，仅删去个别未通过验证的 GT。否则被删条目的改动仍留在文档里，模型指出它可能被误记为多报。
5. 输入逐字删除 `<*$p$*>`，不做整份空白/标点重写。保留原始 GT 的其余内容及顺序。

| 排除原因 | 排除文件数 | 说明 |
| --- | --- | --- |
| 空标注 | 28 | 不能直接当作已认证无矛盾负例 |
| 含非 YES 标注 | 105 | 原始共有 126 NO、6 null，全部随所在文档排除 |
| 缺少/空 explanation | 1 | 保证训练与解释输出字段完整 |
| 合计 | 134 | 5,160 - 134 = 5,026 |

筛选总计移除 321 项扰动，其中 YES 189、NO 126、null 6。最终保留 18,192 项，全部为 YES。没有单独删除 NO 后继续使用原文；因此最终不是仅把 18,513 减去 132。

缺失 changed_text 的空字符串不单独作为排除理由，只要另一边 contradicted_text 可用；这与作者 scorer 允许命中另一处冲突片段的规则一致。也没有按整段子串能否直接找到来一刀切筛选，因为原评分按句子相交，排版与多处扰动覆盖会影响整段检索。

没有新增全量语义重标注。此前样例中有解释诉诸“修改前版本”而不是当前文档内部冲突的情况，YES 不能保证每项都适合严格逻辑矛盾判断；后续可另做独立人工质检。为避免改变任务范围，本轮没有凭主观猜测大批删改标注。

详细排除记录：`provenance/excluded_documents.jsonl`；规则、数量与来源分类：`provenance/filter_split_report.json`。原始上游数据没有被改写。

## 4. 本次划分：默认只用 test 测试

目标比例为 train/validation/test = 80%/10%/10%，比例作用在“基础合同组”上，不是把每个扰动条目随机拆开。同一基础合同在五个类别下产生的全部保留版本都属于同一集合；一份文档的多个扰动也始终留在一起。

组键为来源 + 文件名规范化（Unicode NFKC、大小写归一、去掉 .txt/.snippet 后缀、合并连续空白）。额外检查去空白规范化、忽略大小写后的完全相同输入，不允许跨集合。此次没有发现需要跨不同组再合并的完全相同输入；未声称做了所有近重复模板的语义聚类。

分别在 CUAD 与 NLI 内按 `SHA256("20261030|" + group_id)` 稳定排序，用最大余数法分配组数。种子字符串固定为 `20261030`。论文 one-shot 示例来自 2TheMart 合同，将这一基础合同的全部保留版本固定放入 train，避免示例落在 validation/test。除该约束外不依照模型得分挑选划分。

| 集合 | 基础合同组 | 文档版本 | 扰动条目 | 用途 |
| --- | --- | --- | --- | --- |
| train | 881 | 4025 | 14604 | 训练、构造示例 |
| validation | 110 | 501 | 1779 | 调参、提示与训练策略选择 |
| test | 110 | 500 | 1809 | 最终统一评测；当前默认 |

| 来源 | train 组/文档/扰动 | validation 组/文档/扰动 | test 组/文档/扰动 |
| --- | --- | --- | --- |
| CUAD | 408 / 1893 / 5250 | 51 / 235 / 634 | 51 / 240 / 658 |
| NLI | 473 / 2132 / 9354 | 59 / 266 / 1145 | 59 / 260 / 1151 |

已确认三份集合的 record_id、基础合同组互斥，且无规范化后完全相同全文跨集合。每个集合都覆盖两来源与全部五类。因每个基础合同保留的类别数与扰动数不同，文档数和扰动数不会恰好是 80%/10%/10%。

固定清单：`splits/train.jsonl`、`splits/validation.jsonl`、`splits/test.jsonl`。训练和示例只取 train，策略选择只取 validation；后续正式报告先用 test 的 500 份文档、1,809 项 GT。不要用 test GT 选提示、训练、筛选模型或生成训练答案。与历史基模型对比时，也应让这些模型重新在同一 test 上执行。

## 5. 输入、提示与预测格式

### 5.1 运行包组织

```text
CLAUSE_Eval3_InText_ready/
  README.md
  RUN_GUIDE.md
  requirements.txt
  requirements-bert.txt
  data/documents/<record_id>.txt
  data/annotations/<record_id>.json
  splits/train.jsonl
  splits/validation.jsonl
  splits/test.jsonl
  prompts/l1_system.txt
  prompts/l2_system.txt
  prompts/provenance.json
  resources/nltk_data/
  resources/roberta_model_info.json
  tools/export_requests.py
  tools/run_inference.py
  tools/score.py
  tools/metric_core.py
  tools/prepare_resources.py
  tools/verify_bundle.py
  tools/self_test.py
  provenance/
```

split JSONL 每行包含 record_id、group_id、source、category、split、file_name、text_path、ground_truth_path、扰动数与哈希。路径相对运行包根目录，无本机绝对路径依赖。

GT 顶层为单元素数组，其对象含 `file_name`、`perturbation` 列表。每个扰动含 `type`、`original_text`、`changed_text`、`location`、`contradicted_location`、`contradicted_text`、`explanation`、`contradiction_exists`、`justification`。位置字段是条款描述字符串，不是字符偏移。GT 仅用于训练集合或评测器；推理消息不包含这些参考信息。

### 5.2 提示条件

默认 `l1` 是本次整理的严格内部文档检查提示：定义五类、要求仅从同文档举证、排除外部法律、仅输出 text/explanation 两字段。它针对当前研究目标做了修订，**不与论文原 L1 提示逐字等同**。论文原文五类定义混有外部法律分支，L1 又有额外类别键的格式张力，因此不直接把原段落原封不动当成当前规范。

可选 `l2` 在同一提示后增加一个仅来自 train 的部分上下文与输出示例，不包含 test 答案。默认仍先运行 l1，以便比较基础方法。实际提示和哈希保存在 prompts/；原论文提示片段与发布代码提示仅作为 provenance/ 参考。不要把“发布脚本名 L2”自动认定为论文完整 one-shot 设置。

模型收到 system prompt 和 `DOCUMENT START ... DOCUMENT END` 包围的去标记全文。导出器不会把文件名、source、category、GT 或原始修改位置放入 messages。record_id 只是外部存储标识，包含在请求记录中而不是聊天正文中。

### 5.3 模型预测与存储

每份文档输出：

```json
[
  {
    "text": "Exact complete sentence(s) or passage copied from the document.",
    "explanation": "What other part of the same document it conflicts with and why."
  }
]
```

无问题输出合法空数组 `[]`。每项严格只含 text/explanation 两个字符串；text 不得为空；不要求 law citation、子类或位置偏移。建议解释仍使用英语，与数据及历史协议一致。

预测文件保存为 `runs/<run>/predictions/<record_id>.json`。模型不需要知道真实扰动数。完整句子摘录很重要：原提示允许 phrase，但实际匹配需要至少一个完整相同句子，半句可能被判未命中。

## 6. 当前只计算哪些指标

本阶段启用：Miss、Extra、ROUGE-1 F1、ROUGE-2 F1、ROUGE-L F1、METEOR、BERTScore F1。可附报 signed_balance、normalized_gap 与覆盖量，评分脚本已记录。

暂不运行：Explanation Match 的 Accuracy、Completeness、Clarity、Legal Reasoning、Match，及全部 Law Match。虽然暂不评分解释，模型输出仍保留 explanation，以保持任务格式并供后续人工或 Judge 检查。`score.py` 不导入或调用 OpenAI/Gemini/Judge 客户端，不需要它们的 API key。

### 6.1 匹配规则与 Miss/Extra

沿用作者 GPT 版的匹配和拼接逻辑：每项 GT 的非空 changed_text 与 contradicted_text 各建一节点，保留同一原始缺陷 ID。预测每项建一节点。对文本执行真实 NLTK 英文分句，每句仅 strip/lower；GT 与预测只要共享一句完全相同的归一化完整句子就连边。取无向二部图连通分量，同时含 GT 与预测的才为有效组。

不统一句内空格或句末标点，不用语义相似度建立边；允许一对多、多对一、多对多。命中 GT 任意一边即可不漏检，共享缺陷 ID 不自动连边。同一缺陷两边若分别命中，可能形成两个质量评分组。

```text
n = 当前文档 GT 原始缺陷 ID 数
m = 当前文档预测项数
G_hit = 任意一边进入有效匹配组的 GT 缺陷 ID 集合
P_hit = 进入有效匹配组的预测 ID 集合
FN = n - |G_hit|
FP = m - |P_hit|
Miss_d  = FN/n，n=0 时取 0
Extra_d = FP/m，m=0 时取 0
类别率 = 对该来源、类别下各文档的比率等权平均
```

两者越低越好；原始范围 0—1。空预测且有 GT 时 Miss=1、Extra=0。Extra 是“未对齐标注的比例”，不保证每个未对齐问题在语义上都是错误。重复预测若都命中 GT，可能不增加 Extra；不能拿统一 TP 组数套通用检测 F1 公式。

辅助数量项：(m-n)/(m+n) 为 signed_balance，abs(m-n)/max(m,n) 为 normalized_gap，零分母均取 0。只衡量数量，不检验内容。

### 6.2 五项相似度

在每个有效组中，按作者 GPT 版 DFS 遍历顺序分别拼接所有入组 GT 节点与预测文本，组内只比较这两个字符串一次。不是逐项最佳配对，也不是按合同原文排序；不把未入组的另一边自动补进 GT。这里统一采用 GPT 版，不采用 Gemini 版的 set 去重/字典序排序。

| 指标 | 计算与当前配置 |
| --- | --- |
| ROUGE-1/2 F1 | rouge-score 0.1.2，分别为 unigram/bigram 重叠，use_stemmer=True |
| ROUGE-L F1 | 同一库的最长公共子序列 F1；rougeL，不是 rougeLsum |
| METEOR | NLTK 3.8.1；word_tokenize；唯一 GT 参考；真实 WordNet；默认 alpha=.9、beta=3、gamma=.5 |
| BERTScore F1 | bert-score 0.3.13；本地 roberta-large，第 17 层；idf=False；rescale_with_baseline=False；慢 tokenizer |

ROUGE-n：P=匹配 n-gram 数/预测总数，R=匹配数/GT 总数，F1=2PR/(P+R)；ROUGE-L 换成 LCS 匹配。METEOR 先做精确/词干/同义词匹配，P、R 同理，Fmean=P*R/(.9*P+.1*R)，penalty=.5*(匹配块数/匹配词数)^3，最终为 (1-penalty)*Fmean；无匹配为零。

BERTScore 基于上下文 token 嵌入：预测 token 对 GT token 取最大余弦相似度后平均得 precision，反向得 recall，再取调和平均。它是局部相似度，不是矛盾真实性判断或问题检出的 F1。编码器固定为 `FacebookAI/roberta-large`、revision `722cf37b1afa9454edce342e7895e588b6ff1d59`。上游 bert-score v0.3.13 的英文默认映射也是 roberta-large/17 层；本次显式固定配置。

论文附录声称 BERTScore 使用 deberta-xlarge-mnli 并重标定，公开脚本并未一致实现。当前选择贴近已核验的公开 GPT 代码，因此不是论文附录配置的逐项复制。BERTScore 会按 tokenizer 长度上限截断拼接字符串，当前脚本记录受影响组数；ROUGE/METEOR 不沿用这一编码器截断。

本地编码器仅用来计算 BERTScore，不生成 Judge 判断；固定权重、首次下载后评分可完全离线运行。官方实现参考：[BERTScore v0.3.13](https://github.com/Tiiiger/bert_score/tree/v0.3.13)。

### 6.3 聚合、有效分母与失败处理

```text
组分数 s_dk = metric(组内 GT 拼接串, 组内预测拼接串)
文档分数 S_d = mean_k(s_dk)，仅对有有效组的文档
来源/类别分数 = mean_d(S_d)，仅对具有数值分数的文档
来源五类总览 = 五个类别分数的等权平均
```

组分与文档均值保留四位小数；Miss/Extra 为文档宏平均。漏检和多报不在相似度中补零。无任何匹配的文档保留 Miss/Extra，但相似度为 null/无数值，不进入该指标均值。一个模型可能漏一半、多报一半，唯一命中的组 ROUGE 却是 100%；必须同时报告计数率与质量分。

结果 JSON 同时给出：`by_source_category`、`source_five_category_equal_macro`、`all_documents_macro`。前两者用于按来源/类别比较；最后一项是所有本次有效文档等权的额外总览，不能与前者混称同一加权口径。文本组数、无匹配文档数、文本有效文档数及类别有效分母同时保存。

当前便携版对缺失/无效预测采用严格预检：默认不生成总分，先修复缺失。合法 `[]` 仍正常计为全漏检；不能把 API 失败、截断、格式错误替换成 []。这与作者脚本静默跳过缺失文件的行为不同，是本次为固定分母做的明确修订。`--allow-partial` 仅供诊断，输出会标记不完整，不作为完整测试成绩。评分中途异常会停止，不自动记零或省略。

结果 JSON 的比率为 0—1，历史表格常乘 100 显示百分数。没有官方给出的单一综合分，不自行把七项加权成“CLAUSE 总分”。

## 7. 在服务器实际运行

### 7.1 上传、解压、核验

上传 `CLAUSE_Eval3_InText_ready.tar.gz` 及同名 `.sha256`；服务器先校验压缩包，再解压：

```bash
sha256sum -c CLAUSE_Eval3_InText_ready.tar.gz.sha256
tar -xzf CLAUSE_Eval3_InText_ready.tar.gz
cd CLAUSE_Eval3_InText_ready
python tools/verify_bundle.py
```

verify_bundle 只用 Python 标准库，检查数据配对、YES 标签、文件哈希、输入位置标记、集合互斥和示例归属。provenance 中的文件校验清单只覆盖发货时已有文件；之后下载模型或生成 runs/ 不会要求这些新增文件已在旧清单中。

### 7.2 环境与资源

建议用独立 Python 3.10/3.11 环境。只跑 Miss/Extra + 四个词面指标的诊断环境：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python tools/self_test.py
```

完整七项指标：先按服务器 CUDA/CPU 环境安装合适的 PyTorch（依赖要求 >=2.6,<3），再安装其余固定依赖和编码器：

```bash
python -m pip install -r requirements-bert.txt
python tools/prepare_resources.py --download-bert
```

requirements-bert 也包含基础依赖。仅此下载步骤需要访问 Hugging Face；编码器来自固定 revision，文件哈希另存 MODEL_REVISION.json。NLTK 的英语分句器与 WordNet 已打包，指标代码不会临时联网补资源。PyTorch 的 CUDA 构建应按服务器条件选，不按本机 Mac 的环境选。两组参考说明：[NLTK 资源安装](https://www.nltk.org/data.html)、[BERTScore 实现](https://github.com/Tiiiger/bert_score/tree/v0.3.13)。

### 7.3 导出测试请求

```bash
python tools/export_requests.py --split test --level l1 --output runs/baseline_l1/test_requests.jsonl
```

每行是 record_id 与 messages；共 500 行。将 messages 交给你的模型推理框架，并按 record_id 保存输出。请求导出只是文件生成，不调用任何模型。

可选：已经运行了兼容 Chat Completions 的本地模型服务时，可使用随包客户端：

```bash
python tools/run_inference.py --requests runs/baseline_l1/test_requests.jsonl --base-url http://127.0.0.1:8000/v1 --model YOUR_SERVED_MODEL_NAME --output runs/baseline_l1
```

这条命令调用的是被测模型，不是 Judge；执行前需要你自己启动服务并准备被测模型。若端点需要鉴权，通过 MODEL_API_KEY 环境变量提供，代码不硬编码密钥。默认 temperature=.2、top_p=1、max_tokens=8192；按模型能力调整并记录，不静默裁剪输入。客户端拒绝输出截断和无效 JSON，保留原始响应、参数、请求哈希和错误，支持同配置续跑。

如果使用其他推理框架，只要最终产生 `runs/baseline_l1/predictions/<record_id>.json` 即可，无须使用随包客户端。当前没有为任何特定模型代为下载、启动或执行推理。

### 7.4 运行全部无需 Judge 的指标

模型预测全部准备好后，在本地编码器已下载的条件下执行：

```bash
python tools/score.py --split test --predictions runs/baseline_l1/predictions --output runs/baseline_l1/metrics.json --device cuda
```

无 GPU 可将 device 改为 cpu，BERTScore 会更慢。所有指标均读本地数据和本地编码器，不发送文本给外部 Judge。输出 metrics.json 与 metrics_per_document.jsonl；包含匹配组、比较字符串、各组指标、逐文档分数、有效分母与环境版本。重复运行请选择新输出路径，脚本不会覆盖既有总分。

如果权重尚未下载，可先明确运行不含 BERTScore 的诊断：

```bash
python tools/score.py --split test --predictions runs/baseline_l1/predictions --output runs/baseline_l1/metrics_no_bert.json --without-bert
```

此时只产生 Miss/Extra、ROUGE-1/2/L、METEOR，BERTScore 被显式省略而不是填零；不是完整五项相似度结果。不要把缺失模型时的诊断结果标成已跑全部指标。

### 7.5 训练方法的输入与评测

训练过程从 train manifest 读取全文与 GT，可自行构造监督输出、奖励或训练目标；原 GT 同时给 changed/contradicted 两个可命中片段，如何转成单个训练 text 目标要记录，不能偷偷采用 test 解答。validation 只用于调参和选方法。测试时统一导出相同 test messages，不提供 GT 或类别提示，执行同一评分脚本。

不要因训练方法不同重新随机划分或只挑测试中容易成功的文件；保留 split manifest 哈希。新 test 的成绩需要自己重新测，论文历史表格只能作背景。

## 8. 原论文结果与可比性

正文表 5/6 混合内部与外部类别，不能直接作为本任务成绩。此前已从表 11/12/13 提取内部行，共 320 行、1,200 个数值，并与官方 PDF 核对。按本次要求，此工作说明不再包含全部逐类别附录，也不列当前跳过的 Judge 四维详细表；原始转录作为 provenance/paper_results_intext.json 保留。

以下只给无需 Judge 部分的历史概览：对每来源五个已发表类别值简单等权平均，单位 %，不是在新 test 上重跑，也不是作者新发表的总分。Miss/Extra 越低越好，其余越高越好。

### CUAD：历史五类均值

| 模型 | 设置 | Miss | Extra | R1 | R2 | RL | METEOR | BERT F1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| GPT-4o mini | L1 | 74.84 | 78.54 | 52.18 | 51.32 | 51.54 | 41.30 | 91.62 |
| GPT-4o mini | L2 | 76.26 | 77.34 | 56.34 | 55.44 | 55.40 | 45.68 | 92.38 |
| Gemini 2.0 | L1 | 61.16 | 59.22 | 66.84 | 65.78 | 65.84 | 58.70 | 94.00 |
| Gemini 2.0 | L2 | 60.96 | 59.38 | 66.40 | 65.32 | 65.40 | 58.16 | 93.92 |
| Gemini 2.5 | L1 | 52.06 | 78.98 | 60.42 | 58.12 | 58.14 | 51.90 | 92.36 |
| Gemini 2.5 | L2 | 57.26 | 74.18 | 60.88 | 58.54 | 59.00 | 53.04 | 92.46 |
| LLaMa 3.3 | L1 | 69.10 | 79.88 | 58.84 | 57.62 | 57.84 | 48.46 | 92.74 |
| LLaMa 3.3 | L2 | 64.60 | 74.86 | 58.94 | 57.76 | 57.88 | 48.26 | 92.76 |

### NLI：历史五类均值

| 模型 | 设置 | Miss | Extra | R1 | R2 | RL | METEOR | BERT F1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| GPT-4o mini | L1 | 69.20 | 63.04 | 58.56 | 57.56 | 57.78 | 49.42 | 92.74 |
| GPT-4o mini | L2 | 73.06 | 54.80 | 60.76 | 59.64 | 59.92 | 52.30 | 93.06 |
| Gemini 2.0 | L1 | 57.72 | 54.80 | 67.16 | 65.36 | 65.94 | 61.98 | 93.30 |
| Gemini 2.0 | L2 | 60.12 | 49.06 | 67.86 | 66.12 | 66.52 | 62.08 | 93.46 |
| Gemini 2.5 | L1 | 53.90 | 70.56 | 64.12 | 61.36 | 61.68 | 59.38 | 92.56 |
| Gemini 2.5 | L2 | 55.32 | 64.92 | 64.92 | 62.00 | 62.38 | 60.84 | 92.62 |
| LLaMa 3.3 | L1 | 54.88 | 58.72 | 63.34 | 61.78 | 62.34 | 55.54 | 93.34 |
| LLaMa 3.3 | L2 | 57.88 | 62.76 | 63.06 | 63.52 | 62.84 | 55.84 | 87.96 |

论文 L1 为 zero-shot、L2 为 one-shot；被测模型为 GPT-4o-mini、Gemini 2.0 Flash、Gemini 2.5 Flash、LLaMa 3.3 70B。推理 temperature=.2，top-p 对 GPT/LLaMa 为 1、Gemini 为 .95，最大输出 8192；无固定 API seed。它们与当前适配提示、新筛选集、新 test 的实验条件不同。

历史结果显示，高 BERTScore 与严重漏检可以同时存在，one-shot 也不一贯降低漏检。LLaMa/NLI/歧义/L2 的 BERTScore 原值 66.1 与相邻值差异较大，已在官方 PDF 核对，保留原样，没有擅改。不要凭历史均值给新 test 预填模型成绩。

## 9. 本地验证和使用限制

已执行的数据核验：5,026 份文档输入与 GT 成对，18,192 项全部 YES，分组互斥，去标记输入哈希核对，one-shot 来源组属于 train。检查记录见 provenance/。

已执行代码验证：真实英文 Punkt、真实 WordNet、真实 ROUGE/METEOR 的 11 个边界回归；7 组输入与作者原 DFS 匹配结果逐项对照；6 对字符串的四个词面指标与原函数比较；500 份测试文档的合法空预测端到端检查、12 份真实数据格式诊断、缺失/无效预测的严格拒绝以及 500 条推理请求导出检查。

上述均是程序检查和合成预测，不是模型实验。RoBERTa-large 权重未在本机下载/执行，实际被测 LLM 与 Judge 均未运行；BERTScore 权重分支的全量运行需在服务器安装依赖并下载编码器后完成。不能把本次准备记录当成基线成绩。

关键来源：作者仓库 [Better-Call-CLAUSE](https://github.com/mananroychoudhury/Better-Call-CLAUSE)，固定 commit 见第 3 节；论文为 Findings of EACL 2026 的 Better Call CLAUSE。数据与指标的原始来源代码保存在 provenance/upstream_code/；当前 runnable tools/ 是明确去掉 Judge、适配路径并加强缺失预测检查的便携版。论文称来源 CUAD/ContractNLI 为 CC BY 4.0，原作者与来源信息随包保留；未据此另行宣称作者代码有一个未发布的开源许可证。
