# CLAUSE Eval 3 / In-Text：服务器运行包

当前版本：`intext-yes-whole-document-v1`，2026-10-30。

**先阅读 [RUN_GUIDE.md](RUN_GUIDE.md)。默认运行 test：500 份文档、1,809 项扰动；不运行 LLM Judge。**

| 集合 | 基础合同组 | 文档 | 扰动 |
| --- | ---: | ---: | ---: |
| train | 881 | 4,025 | 14,604 |
| validation | 110 | 501 | 1,779 |
| test | 110 | 500 | 1,809 |
| 合计 | 1,101 | 5,026 | 18,192 |

仅保留所有扰动均为原发布核验标签 YES 的完整文档；排除空标注、含 NO/null 和缺解释的文件。同一原合同的不同类别版本不跨集合。YES 是发布者的模型验证结果，不是本次人工逐条复核。

包中已包含数据、GT、固定划分、内部 L1/L2 提示、可选被测模型客户端、独立本地评分代码及英文 NLTK 资源。**不包含服务器 Python 依赖、BERTScore 编码器权重或被测 LLM 权重。**

## 最小工作流

```bash
# 在运行包根目录；先核验，无第三方依赖
python tools/verify_bundle.py

# Python 3.10/3.11 独立环境中安装；PyTorch 构建按服务器 CUDA/CPU 选择
python -m pip install -r requirements-bert.txt
python tools/prepare_resources.py --download-bert
python tools/self_test.py

# 只导出 test 输入，无 GT；之后用你自己的推理框架生成预测
python tools/export_requests.py --split test --level l1 --output runs/baseline_l1/test_requests.jsonl

# 预测就绪后；每份保存为 predictions/<record_id>.json
python tools/score.py --split test --predictions runs/baseline_l1/predictions --output runs/baseline_l1/metrics.json --device cuda
```

以上最后一步计算 Miss/Extra、ROUGE-1/2/L F1、METEOR、BERTScore F1；无外部 Judge 调用。BERTScore 使用本地 RoBERTa-large/17 层、无重标定。下载编码器后评分默认禁止 Hugging Face 联网。

`--without-bert` 可显式生成缺少 BERTScore 的诊断结果，需要的基础依赖为 requirements.txt；不会把缺失的 BERTScore 伪造为零。默认不允许预测缺失或 JSON 无效；合法 `[]` 会计为真实空预测。不要将推理失败转成 []。

## 证据与复现

- `provenance/filter_split_report.json`：全部筛选统计、固定种子、分组算法与逐来源/类别计数。
- `provenance/excluded_documents.jsonl`：逐文件排除原因。
- `provenance/group_assignments.jsonl`：基础合同组的固定集合。
- `provenance/package_checksums.jsonl`：发货文件的相对路径与 SHA-256。
- `provenance/local_workflow_verification.json`：本地代码验证记录；不是模型基线结果。
- `prompts/provenance.json`：提示哈希与训练示例来源。
- `provenance/upstream_code/`：保留的作者原始匹配代码，仅作来源对照，不是当前入口。
- `provenance/paper_results_intext.json`：历史论文内部子集结果；不是本次 test 的成绩。

这是在公开快照上新增筛选/划分与提示适配的研究版本。原论文基线不能直接与新的 test 结果当成同集对比；需在同一 test、提示和评分配置下重新运行基模型。
