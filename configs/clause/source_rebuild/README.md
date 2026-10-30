# CLAUSE 固定上游重建资源

入口为仓库根目录的 `scripts/dcc_prepare_clause.py --download`，实现位于
`dcc_harness/datasets/clause_source.py`。只需要 Python 3.10+ 标准库准备数据；
SP2 的运行环境另按项目要求安装。完整操作见 `docs/clause_benchmark.md`。

本目录由用户提供的 `CLAUSE_source_rebuild_supplement.tar.gz` 集成：

- 补充包 SHA256：`c893ee2a4a1025ebd923571d48af5a89605c7ddd67bdc78c30acc814c0ecbddc`。
- 上游 commit：`c81c6a5251771cab3d9f3d081f71c388957cf936`。
- 上游 root tree：`1298b34966edf98f1a03a59820915f88de4a0742`。
- 上游 datasets tree：`a51edc3815cc1b7c084c8aa5366780dc26a011fa`。
- 运行包校验清单 SHA256：`fd0139a93064c983337b0dd1e03f0af5ef26833b0702329f350bb4f469cd10af`，也固定在 `configs/clause/protocol.json`。

`templates/` 保留 34 份冻结文件：固定划分、prompt（包括一个训练示例）、
来源/校验信息，以及原运行包的辅助代码与说明。不包含完整正文、完整标注、
NLTK 语料、模型或环境。划分清单只有文件路径、ID、分组、数量和哈希等元数据；
其中 source/category 属于评分侧信息，不能送入 agent 输入。

**冻结模板不可格式化或手动改写。** `configs/clause/.gitattributes` 禁止 Git 对这些模板和 L1/L2 提示词自动转换换行，避免跨系统 checkout 改变校验字节。 它们由原运行包的 SHA256 清单逐项认证。
重建器执行模板中已认证的筛选算法，再逐文件比较完整输出，而不是直接把冻结
清单当作下载成功的证明。协议升级需要显式产生新的数据版本与校验基线。

原补充包的 `provenance/upstream_code/` 四份历史源码没有纳入本仓库；其中
存在硬编码凭据。重建时，冻结算法从已验证的上游下载内容将它们恢复到本地
Git 忽略目录，仅用于原归档内容一致性校验，绝不导入或执行。运行评分使用
DCC 插件，不使用这些历史 API/Judge 脚本。不要把生成的 ready 目录加入 Git。

重建器对上游全部 Git blob/tree 进行验证；从临时目录重建，校验全部
10,092 项文件后才发布 ready 目录。适配器另外验证校验清单自身的固定哈希，
拒绝缺失、多余、篡改或符号链接文件，再转换为现有 public/private 格式。

本目录的 `templates/README.md`、`RUN_GUIDE.md` 和其他 provenance 文件是
原运行包的冻结记录，不是 DCC 当前操作入口。运行 DCC 时以仓库
`docs/clause_benchmark.md` 为准。
