# SP2 围串标审核

CollusionAudit 包含 M01–M15 计算工具、原生 SP 扩展、测试及 HTML 报告 skill。
Central 自主委派和复核，计算工具提供候选和原件定位，原生 FINISH 接受当前运行
实际生成的报告。工具候选需要模型结合原件判断。

## 安装与配置

使用 Python 3.12，从仓库 backend 目录执行：

~~~bash
uv sync --group dev
uv pip install --python .venv/bin/python -r ../collusion_audit/requirements-extra.txt -e ../vendor/bid_audit_tools
cp ../configs/collusion_audit/config.example.yaml ../config.yaml
cp ../configs/collusion_audit/model.env.example ../.env
~~~

填写 .env。模型选项 qwen3-8-27b 使用本地 OpenAI 兼容服务，
aliyun-qwen3.8-27b 使用 DASHSCOPE_API_KEY 和 DASHSCOPE_BASE_URL。
模型需要工具调用能力，M06 原图复核需要可用的视觉输入能力。

RAGFlow 使用 RAGFLOW_API_URL 和 RAGFLOW_API_KEY。协作者需自行取得
有权读取映射中知识库的 Key，通过环境变量、--env-file 或 --ragflow-env-file
提供。知识库 ID 对应已有资源，服务端仍按凭据授权访问。

## 准备项目原件

来源目录需按轮次、采购包、投标人组织。例如：

~~~text
项目目录/
  招标文件.pdf
  投标文件/采购包1/甲公司(包1)/投标书.pdf
  投标文件/采购包1/甲公司(包1)/分项报价表.pdf
  投标文件/采购包1/甲公司(包1)/开标一览表.pdf
~~~

第二轮目录名包含“第二次开标”。支持 PDF、DOCX 和 TXT 原件。
自动归类规则面向当前包 1 的材料结构，其他组织形式可编写同一 schema 的清单，
指定 id、相对 path、role、bidder、round、lot 和 expected_sha256。
准备后应核对文档角色、轮次和采购包。准备器排除历史分析目录和报告类文件名。

~~~bash
python -m app.experiments.collusion_audit.prepare   --source /path/to/original-project   --output /path/to/prepared-project   --project-id my-project
~~~

默认使用分支中的通用 rules.json。--rules-workbook 可从 Excel
“合并规则总表”仅导出九个通用字段。--include-tender-archives 可从名称含
“招标文件”的 ZIP 中补充原件，保留归档路径检查与重复检测。
输出需使用新目录。prepare_complete 模块也接受同样的显式参数。

## 复用远程 MinerU 解析

collusion_audit/shared/jiuzhoutong-mineru-v16.json 对应
“2026九州通-广州中医药大学第一附属医院胃肠镜系统采购项目”。
映射包含原件相对路径及 SHA256、逻辑 ID、dataset/document ID、解析设置、
物理页数、chunk 数和解析快照校验值。项目有 14 份原件，其中 13 份 PDF
已完成一文档一知识库解析，另外一份 DOCX 通过本地原件工具读取。
原件与解析正文保存在本地或服务端，凭据由协作者配置。

持有同一批原件后执行：

~~~bash
python -m app.experiments.collusion_audit.prepare   --source /path/to/jiuzhoutong-originals   --output /path/to/prepared-jiuzhoutong   --shared-mapping ../collusion_audit/shared/jiuzhoutong-mineru-v16.json

python -m app.experiments.collusion_audit.shared_knowledge   --mapping ../collusion_audit/shared/jiuzhoutong-mineru-v16.json   --project /path/to/prepared-jiuzhoutong/project_complete   --output /path/to/knowledge-jiuzhoutong   --env-file /path/to/local-ragflow.env
~~~

同步仅查询已有文档及下载已有 chunks，校验原件身份、远程文档范围、
完成状态、解析快照 SHA256 及物理页码，随后原子发布本地 index.json、
chunks.jsonl 和 parsed.md。远程资源删除、重解析或权限不足会明确报错。
其他项目可显式新建知识库：

~~~bash
python -m app.experiments.collusion_audit.ingest   --project /path/to/prepared-project/project_complete   --output /path/to/new-knowledge   --env-file /path/to/local-ragflow.env
~~~

ingest 会创建、上传并解析新的知识库。
电外科 CZ2025-1008 项目建库仍在准备阶段，随附映射来自已完成的胃肠镜项目。

## 运行审核

从 backend 执行。--project 指向包含 manifest.json 的原件目录，
--rules 指向准备器输出的规则文件，每批使用新名称。

~~~bash
python -m app.experiments.collusion_audit.runner   --all-rules   --project /path/to/prepared-jiuzhoutong/project_complete   --rules /path/to/prepared-jiuzhoutong/rules.json   --knowledge-root /path/to/knowledge-jiuzhoutong   --config ../config.yaml --env-file ../.env   --output-root /path/to/audit-runs --batch-name jiuzhoutong-html   --model aliyun-qwen3.8-27b --timeout 14400   --rule-level-review --quality-controls --report-format html   --resilient-delivery --central-efficiency --reporter-delivery   --researcher-no-thinking --evidence-role-capacity   --evidence-max-tokens 16384 --evidence-request-timeout 960   --reporter-max-tokens 32768 --reporter-request-timeout 900   --central-retry-timeout 480 --central-retry-max-tokens 8192
~~~

这些容量参数对应最终 HTML 迭代采用的配置。--rule M01 可选择单条规则。
--model qwen3-8-27b 使用本地服务，按服务容量调整参数；
--central-efficiency 专用于 Alibaba HTML 质量审核，本地服务运行时省略。
省略 --knowledge-root 可使用本地原件及计算工具，扫描页与复杂表格的文本覆盖
取决于原件可读性。每条规则单独保存报告、attempt-summary.json、工具和模型
轨迹及开销，completed 需要原生 FINISH 和实际报告校验成功。

## 分支范围与验证

原始基准为 SP2 1459577。本分支包含此前招标审核辅助代码，围串标复用其中的
模型配置、计时与观测设施。通用 SP 扩展默认关闭，接口说明见 backend/AGENTS.md。

~~~bash
PYTHONPATH=.:packages/harness .venv/bin/python -m pytest tests/test_collusion*.py tests/test_sp*.py
PYTHONPATH=../vendor/bid_audit_tools .venv/bin/python -m pytest ../vendor/bid_audit_tools/tests
~~~

原始数据、运行结果、轨迹、环境、浏览器和缓存由 .gitignore 排除。
共享映射包含项目身份和定位元数据，原件访问权限按项目资料共享约定取得。
历史交付经历多轮开发及恢复，开发阶段参考过项目对照材料；
当前源码和命令可复现处理流程，模型输出会随服务及执行路径变化。
