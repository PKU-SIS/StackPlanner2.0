# FinAuditing 内部训练/测试划分

对现有 `finauditing-v1` Release 的 1,102 题做约 80/20 划分，固定 seed 42。
这些题原本都来自上游 test；本划分用于团队后续实验，不是官方 train/test。

| 子任务 | 训练 | 测试 |
| --- | ---: | ---: |
| FinRE | 352 | 88 |
| FinMR | 266 | 66 |
| FinSM | 264 | 66 |
| 合计 | **882** | **220** |

按待审文档的完整内容 SHA-256 分组：共享任意一份文档的样本必须在同一侧，
包括 FinRE、FinMR、FinSM 之间的复用，以及 A 与 B、B 与 C 共享文档的传递关系。
共形成 285 组，最大组 242 题；分组完成后再满足各子任务的题数配额。
训练集有 1,666 份唯一待审文档，测试集 489 份，**文档交集为 0**。
公开审计规则、US GAAP taxonomy 等参考规则可共用，不作为待审文档分组。

不删题、不改原文、规则、答案或 prompt，也不根据模型成绩筛选。
原 Release、原 bundle 和既有实验输入保持原样；只在新目录设置 host 侧 split/group 字段。
RAGFlow 复用每题原有专属知识库和 document ID，只导出分区内的索引映射，
不上传、不重新切片、不重新建库。分区的索引映射为只读视图，拒绝再次执行 `index`。

## 使用

先按[跨服务器说明](DCC_PREPROCESSING.md#跨服务器使用-release-数据包)下载原 Release，
再在仓库根目录生成两个独立可用的数据包；这一步不需要 RAGFlow Key 或网络请求：

```bash
python scripts/dcc_split.py export \
  --bundle .benchmark-data/finauditing-v1/bundle \
  --definition configs/dcc/splits/finauditing-80-20-v1.json \
  --output .benchmark-data/finauditing-80-20-v1
```

输出为 `.benchmark-data/finauditing-80-20-v1/{train,test}/`，各自包含
`public/tasks.jsonl`、规则/文档/上下文、`private/gold.jsonl` 和只读 `index.json`。
运行时将 `DCC_BUNDLE` 和任务路径指向选定分区；`DCC_CONFIG` 仍使用原 Release 的
`config.json`，检索仍需原共享服务的 Key。原生 SP 插件和离线 evaluator 不需要改动。
脚本校验来源指纹、逐题输入哈希与文档隔离，拒绝覆盖已有目录。

## 扩展与复现

仓库内只提交划分清单及来源指纹，数据继续从原 Release 获取。
通用文档分组构建器通过 `module:function` 注入，不在 SP harness 中增加数据集分支：

```bash
python scripts/dcc_split.py create \
  --builder dcc_harness.preprocessing.document_splits:make_definition \
  --bundle .benchmark-data/finauditing-v1/bundle \
  --train-fraction 0.8 --seed 42 --output /path/to/new-definition.json
```

算法按固定哈希顺序选择完整文档组，保持子任务比例；配额无法满足时明确报错，
不会拆散共享文档组。现有 NBA 划分清单和分配结果不变。
