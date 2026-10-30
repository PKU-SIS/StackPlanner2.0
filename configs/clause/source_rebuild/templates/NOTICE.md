# 来源说明

- 原论文：Better Call CLAUSE: A Discrepancy Benchmark for Auditing LLMs Legal Reasoning Capabilities，Findings of EACL 2026；作者稿标识 arXiv:2511.00340。
- 作者仓库：https://github.com/mananroychoudhury/Better-Call-CLAUSE
- 固定提交：`c81c6a5251771cab3d9f3d081f71c388957cf936`。
- 上游 datasets Git 树：`a51edc3815cc1b7c084c8aa5366780dc26a011fa`。
- 来源合同：CUAD 与 ContractNLI；原论文章节 A.1 列两者许可为 CC BY 4.0。保留原数据来源及作者归属。
- 本包只取 Eval 3 / In-Text，新增整文档 YES 筛选、按基础合同分组划分及可移植入口；不是作者官方划分或官方复现成绩。
- `tools/metric_core.py` 的句子相交/DFS/拼接逻辑依据作者 GPT 版 `Text_Alignment_&_Exp_Match.py`，原文件保存在 `provenance/upstream_code/`。未为作者代码自行指定未发布的许可证。
- 英文 Punkt、WordNet 下载自 NLTK 官方 nltk_data 仓库；下载 URL、归档哈希与所保留资源记于 `resources/nltk_resources_provenance.json`，WordNet 归档内保留 LICENSE/README。
- BERTScore 依据 Tiiiger/bert_score v0.3.13；RoBERTa-large 模型元数据来自 Hugging Face 官方 FacebookAI 仓库，固定 revision 见下载脚本。模型权重不随本初始归档分发。
