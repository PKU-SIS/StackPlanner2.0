【只读核对结论（基准：当前活动版本 t0009 / final report-6.md，tree_version 8；未检索、未写作、未改文件）】

一、三个小节与独立章节
1. 【迁移演练建议】保留。位于 n9「退出验收与合同条款：把迁移能力写进采购」末尾（正文第 12–17 段），含演练设计目标、流程状态、数据与附件、独立读取与留痕、人员交接五项，以及“通过／存在缺口／不可判定”的记录产出建议。
2. 【新资料核对】保留。同节第 6 段，逐条列出 Power Apps 与 Mendix 的导出／迁移路径与缺口。
3. 【迁移边界核对】保留。同节末段，按“已证实／附条件／未知”三类落位，并给出“既有正文无过度推断”的核对结论。
4. “补充：平台退出能力对照”独立章节已删除。当前章节目录只有 7 节：效率证据的实测量级与收益结构(n2)、厂商主张、平台表达能力与收益反转的边界(n3)、许可与容量(n5)、治理人力与技术债(n6)、锁定发生在哪一层(n8)、退出验收与合同条款(n9)、面向技术负责人的判断与建议(n11)；文末 n11 结论段也无残留标题、表格或正文。

审核提示（rv6，advisory，不影响交付）：【迁移边界核对】实际约 900 字，超出该节点 addendum2_target_words=400；与【新资料核对】在“组件／模型包 vs 记录／数据库两条互不替代路径”上有重述（因要求两段原文保留，属可接受重述）；正文把 S304、S305 写成“同一仓库较早版本的官方文档源文件”，严格说是同一文档（maker/canvas-apps/export-import-app.md）的两个版本，措辞可更严谨，结论方向不受影响。

二、后续轮新加入的官方来源及其支持的判断

A. 【新资料核对】轮（11 个，来源性质以正文标注为准）
- S288 Solutions overview（Microsoft Learn 参考文档）：解决方案是把应用与组件跨环境搬运的 ALM 机制，可含站点地图、表、流程、Web 资源、选项集、流。
- S227 Export solutions（Microsoft Learn）：托管解决方案与默认解决方案不可导出，官方建议以非托管解决方案定期导出备份；非托管导出只含已发布组件，须先 Publish all changes，缺失依赖需在导出前补齐。
- S224 Distribute a model-driven app using a solution（MicrosoftDocs powerapps-docs 源文件）：导出产物是供 Import solution 消费的 .zip；用解决方案导出应用时应用 URL 不随之导出。
- S219 Export to an Excel static worksheet（MicrosoftDocs 源文件）：静态工作表可导出当前视图全部页或仅当前页，单次上限 10 万行，模型驱动默认每页 50 行，系统视图列需 System Administrator 或 System Customizer 才能改。
- S213 Dataverse data export options（Microsoft Learn 第一方训练模块，教学材料）：maker 门户“导出数据”按所选表导出全部列与全部行，每表一个 CSV 打包；选择（选项）列只含数值不含标签，查找列只含父行 GUID；Synapse Link 仅在表启用更改跟踪后才纳入。
- S242 导出应用程序包（docs.mendix.com 第一方产品文档）：Studio Pro 可导出 .mpk 并用 Import App Package 重新导入；.mxsolution／.mpk 选项仅在具备发布附加模块与解决方案权限（经合作伙伴计划获得）时可见；包内数据可选无数据／现有快照／当前数据库新快照，后者需本地至少运行过一次应用。
- S246 export-project-package-dialog（mendix/docs 官方仓库源文件）：项目包中的快照只含本地数据库数据与本地运行应用时上传的文件。
- S275 Migrate Your Mendix Database（docs.mendix.com）：可在任意受支持数据库间复制，方式为空目标库＋自定义设置后启动应用，导出用 pg_dump -O -x -Fc；官方明确不能靠数据库迁移或恢复备份在两个应用间搬数据（每个应用对实体做唯一标记），替代路径是 Database Replication 模块。
- S237 Restoring a Backup（mendix/docs 第一方部署文档）：DB-only 恢复不恢复任何文件，可能导致数据缺失或应用无法按预期运行；目标库须容纳解压后体积并额外约 30% 开销，否则只得部分恢复；pg_dump 1.16 及以下随 PostgreSQL 17 及以下。
- S232 Migrating Data in Mendix on Kubernetes Environments (Preview)（docs.mendix.com）：该迁移工具按原样提供、不再增强，仅支持 PostgreSQL，只导出被 System.FileDocument 引用的文件，未被使用的文件被忽略。
- S268 MXSDK FAQ（mendix/docs 官方仓库）：Model SDK 与 Model API 为 public beta、支持 Mendix 6.0.0 及以上、Model API 托管在欧盟、初始 mpk 不超过 250MB，可访问并完整导出全部元模型元素。

B. 【迁移边界核对】轮（6 个，均为 MicrosoftDocs／microsoftdocs 官方仓库或 Microsoft Learn 文档）
- S304 export-import-app.md（main 版）：应用包逐项枚举“App”“Power Automate”可随包，“自定义连接器／连接／Dataverse 定制／网关”明确为 No、须在目标环境重建；存在 Dataverse 依赖（流、连接引用等）的画布应用不支持应用包方式，应用包与 Dataverse 解决方案包不兼容。
- S305 同一文档较早版本：导入时连接可从已有列表选择或新建（2020 年版导入流程），即授权在目标环境重新完成。
- S292 Move configuration data with Configuration Migration tool（microsoftdocs/power-platform）：配置数据是定义定制功能、通常存于自定义实体的数据，明文区别于终端用户数据（account、contacts 等）；配置数据迁移不支持迁移 Image 列。
- S325 Files and images overview：File 列无法随记录检索、只返回 fileid 值；Attachment & Note 可随记录检索。
- S319 Migrate Data Between Dataverse Environments Using the OData Connector：业务记录通道要求两端均为 System Administrator 或 System Customizer、具备 Power Apps／Power Automate／Dataverse 许可、两端均为带数据库的环境；多对多关系数据不能导入、须手工安排父表先于子表、Status 与 Status Reason 不可映射。
- S312 Export and import a canvas app as a single app file：单文件 .msapp 只包含当前正在编辑的那一个画布应用。

三、来源结论 vs 报告判断/建议
- 来源结论（上文表格，可直接引用官方原文含义）：各平台“可导出”至少分解决方案／模型包 与 数据／数据库 两条路径，且各带前置条件与语义损失。
- 报告综合（非来源原话）：两条路径“互不替代”；“有导出按钮≠可退出”；“可完整导出模型”不能推出“可迁移到其他平台”，更不能推出“可无成本完整迁移”；S305 的“凭据不随包”属间接推断（正文已注明官方无逐字表述）。
- 报告建议（非来源结论）：采购清单单列应用 URL 与环境绑定、被引用/孤立文件、选项集标签与查找外键语义、数据库容量与恢复开销、pg_dump 版本兼容；把路径／对象／前置条件／未知项写成一四列表并要求现场演示签字；【迁移演练建议】全部内容（隔离副本演练、第三方无原系统环境读取、人员交接验证等）均为设计建议，报告未实施过演练。
- 记为“未知”（来源未证明，不等于官方否认）：Power Apps 附件是否随数据导出、导出解决方案的精确角色清单、解决方案能否携带配置数据、平台间是否存在跨平台等价交换格式、Mendix 导出物在平台之外的许可边界。
- 来源间口径不一致：pg_dump 版本（S275 写 1.14 及以下随 PostgreSQL 12–15，S237 写 1.16 及以下随 PostgreSQL 17 及以下），正文要求以目标环境当期要求为准。

四、核对范围说明：本节结论来自 t0009 当前正文与已保存的来源元数据；未逐条重读全部来源原文，provenance 摘录也只覆盖部分行，未展示部分不代表未保存。
