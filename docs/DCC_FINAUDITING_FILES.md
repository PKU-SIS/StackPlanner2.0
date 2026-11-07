# FinAuditing：原生文件检索实验

本次使用内部划分的 **220 题测试集**（FinRE 88、FinMR 66、FinSM 66），沿用
[原划分](DCC_FINAUDITING_SPLIT.md)，与训练集的待审文档内容交集为 0。

选择 `document_mode: "files"` 和 `native:make_file_plugin`。每题的全部原始 XML
及规则以独立文件放到该题 user/thread 的 uploads，SP 通过已有 `grep`、`glob`、
`read_file` 和必要的本地 Python 检查。只改变访问方式，不切片、不改写 XML，也不修复
原数据中的 XML 片段。中性文件名为 `dcc-document-N.txt`，文件内容仍是逐字节一致的 XML。
文档不整段塞进 prompt；prompt 提供文件目录、原问题和原子任务的输出要求。

原 RAGFlow 配置、索引和知识库全部保留。文件模式不注册 `ragflow_retrieve`，不需要
RAGFlow Key，不发出 RAGFlow 请求，不修改 SP 的规划、委派或执行循环。
通过独立 prompt/Skill 片段注入文件检索要求，官方 evaluator 保持不变。

## 准备与使用

先下载原 `finauditing-v1` Release 并生成已有划分；已有目录直接复用。
跨服务器仍从同仓库 Release 获取完整原始文件，无需重新建库。

```bash
python scripts/dcc_split.py export \
  --bundle .benchmark-data/finauditing-v1/bundle \
  --definition configs/dcc/splits/finauditing-80-20-v1.json \
  --output .benchmark-data/finauditing-80-20-v1

python scripts/dcc_preprocess.py export-files \
  --config configs/dcc/preprocessing.finauditing.files.example.json \
  --bundle .benchmark-data/finauditing-80-20-v1/test \
  --output .benchmark-data/finauditing-files-test-v1

export DCC_BUNDLE="$PWD/.benchmark-data/finauditing-files-test-v1"
python scripts/dcc_preprocess.py doctor \
  --config configs/dcc/preprocessing.finauditing.files.example.json
```

导出器拒绝覆盖已有目录；保留样本、文档、规则、标签和 host 侧分组，重新生成文件访问
prompt 与输入指纹。原 RAGFlow bundle 和 Release 不变。
如需从原数据构建另一套文件实验，也可直接 `prepare` → `render`，不执行 `index`。

推理沿用 [benchmark CLI](dcc_harness.md)，指定
`--benchmark dcc_files --dataset-plugin dcc_harness.preprocessing.native:make_file_plugin`，
`--tasks "$DCC_BUNDLE/public/tasks.jsonl"` 和全新 `--run-dir`。
模型、预算等使用原实验配置；使用独立源码快照记录本次 DCCv2 修订。

```bash
python scripts/evaluate_sp_benchmarks.py \
  --tasks "$DCC_BUNDLE/public/tasks.jsonl" \
  --run-dir /path/to/new-file-inference \
  --output-dir /path/to/new-file-evaluation \
  --dataset-plugin dcc_harness.preprocessing.native:make_file_plugin \
  --options configs/dcc/evaluation/finauditing.files.example.json
```

评测选项中的 `bundle` 应指向实际目录。FinRE/FinSM 用官方解析与指标，FinMR 用官方
提示词及团队 DeepSeek 裁判；推理失败、无效格式及裁判解析失败均单独保留。

## 本次实验门槛

先从测试集按顺序各取一条 FinRE、FinMR、FinSM，检查真实文件访问、样本隔离、
原始文件哈希、无 RAGFlow 调用、无人工提问、正常完成及输出格式。选择不参考答案标签，
不以“答对”作为启动全量的条件。检查通过后，在独立目录重新跑全部 220 题；冒烟结果
不并入正式成绩。原始任务、规则和标签不因冒烟结果调整。
