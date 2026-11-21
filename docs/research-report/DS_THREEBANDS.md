# DeepSeek 统一三档入口

一套运行代码通过 `--length-band 10000|20000|30000` 切换档位。此入口自动建立原生 `report_length` 合同，选择既有预算表，并让 Outline/Writer 使用对应档位提示。

| 档位 | 全文原生验收范围 | Outline 正文推荐预算 | 默认预算 |
|---|---|---|---|
|10000|9000–11000|3000–4000|3500|
|20000|18000–22000|8000–11500|9500|
|30000|27000–33000|13000–16500|15000|

正文预算由 Outline 在推荐区间中选择并分配到章节，与最终全文目标不同。档位入口同时开启 compact_outline、writer_task_cards、outline_budget_selection，关闭 length_auto_revision。原有手动 `--word-length` 入口保留；为避免歧义，不能与 `--length-band` 或其自动设置的手动篇幅开关混用。

下面命令只准备一次独立实验，不调用模型；输出和临时目录请使用自己的数据盘路径，输出目录必须尚不存在：

```bash
PYTHONPATH=backend:backend/packages/harness python backend/scripts/evaluate_report_full.py \
  --output /data2/pkuiti/zmy/sp2-runs/example-20k \
  --ids 17 --not-before 2026-11-21T00:00:00+00:00 --length-band 20000
```

将 `20000` 改为 `10000` 或 `30000`，并换一个输出目录，即切换档位。真正执行时需另建输出目录、配置自己的根目录 `config.yaml`/凭据、选择适当时间门限，并显式添加 `--start`。这不启动 RACE；评分沿用原独立流程。

程序集成可以调用 `deerflow.sp.report.length.contract_for_band(20000)`，把结果放入已有请求的 `report_length` 字段。当前提供脚本参数入口，没有新增前端下拉选项。

## 整合范围

统一代码从最初敲定的 DeepSeek 一万档出发，保留其中已有的显式报告入口修复、缺章恢复、原生需求/证据检查和审查。没有混入千问 v18、v19/v20/v21 或后来的 DeepSeek 回归适配。

一万档使用原简洁大纲和 Writer 提示。两万档保留原简洁大纲及展开已选论点的 Writer 提示，并将其中误写的“30000-word variant”纠正为当前目标 20000。三万档保留原先的实质章节论点展开提示（4–6 为建议，必要范围/证据优先）及 Writer 提示；这些文本只在三万合同中生效。预算范围和原生全文门槛保持原档位值。

`skills/public/report-word-budget/SKILL.md` 按全文合同区分短档/三万档，避免技能说明与角色提示冲突。前期研究、初次大纲、固定补研、最终大纲、章节写作、原生审查与验收的顺序保持不变，不增加模型调用或成稿长度处理。

`deliveries/deepseek-threebands/` 保留原三个精确快照和十四篇历史结果，作为审计证据。其历史字数/RACE/token 不是本次统一代码的新实跑成绩；该归档对应的统一入口初版只做离线回归；后续主线整合验收另见下节，不能据此保证字数每次都命中区间。

## 离线验证

统一版的 SP2 报告相关回归共 532 项通过；格式整理后，三档入口及禁止长度返修相关的 20 项检查通过。修改的五个 Python 文件通过 Ruff 检查和格式检查。原三档归档及历史变体的文件哈希校验通过。上述初版验证时尚未对统一版重新生成完整报告或运行 RACE。

## sp2DeepResearch 主线整合验证（2026-11-21）

保留 `sp2DeepResearch` 为报告主线名，统一三档代码上增加独立验证过的空大纲一次纠正补丁。707项报告相关回归通过；一次原始query case17的一万档完整原生Central smoke正式交付7/7章节，耗时470.0秒，全文mixed-word-estimate-v1估算9410，位于9000–11000，最终文件SHA通过。未新增RACE或两万/三万档实跑；不是Gateway/前端验收或全系统稳定性结论。冻结历史结果保持原样，Qwen v18独立保留。具体源提交和归档标签见 [分支整理记录](BRANCH_CONSOLIDATION_20261121.md)。
