# 围串标响应类工具：PDF读取、取消与事实缓存

本指南说明围串标适配中的通用计算恢复机制。原件不改写，SP原生Central仍自主选择角色、步骤和复核深度，不增加模型输出或JSON修复。

## 混合PDF索引的重复遍历

部分PDF的混合交叉引用结构同时通过`Prev`和`XRefStm`指向先前区段。缺少已访问偏移检查时，PDFMiner可能反复展开相同区段，使原件读取长时间不返回。这种等待发生在代码工具内部，提高模型输出预算无法解决。

`bid_audit.pdf_access`为每份文档提供独立的`PDFDocument`子类，记录已读取的xref偏移。pdfplumber构造器仅注入此子类；页码、文本、表格、元数据以及调用方流的所有权沿用原有语义。不修改已安装库，不进行全局替换，也不修复或重写原PDF。

所有原件表格读取入口统一使用该适配，相关解析缓存版本随实现更新。去重仅针对PDF索引结构的重复访问，原文相同句子、规则、候选和引文均保留。

## 计算隔离与取消

仅取消`asyncio.to_thread`的等待不会终止底层线程。响应类M03/M04/M07工具在持久化知识库与共享事实缓存模式下，通过`calculation_process`使用独立子进程执行同一算法。

- 计算限时1800秒，低于原生3600秒委派限额，不延长模型预算。
- 适配器取消通过ContextVar携带的Event传播到计算进程。
- 取消或超时先向进程组发送TERM，必要时KILL，并等待回收。
- 计算失败保留为真实工具错误，不伪造空结果、成功回执或报告。
- 实际运行中的进程生命周期、请求、原始结果和worker日志写入当前输出的`calculation_jobs`，便于检查退出与取消情况。

## 只缓存代码事实

`calculate_cached`缓存无模型的计算产物，不保存模型判断或报告。缓存键包含manifest、原件SHA、规则、工具实现、解析原文及位置摘要、PDFMiner等依赖版本。

读缓存前仍校验原件SHA和缓存文件完整性；输入或实现变化会失效，篡改会报错。缓存命中后将事实文件物化到当前运行输出，后续取证和报告仍由本轮SP独立完成。计算结果的`partial`状态、歧义行及未恢复范围不能被解释为无风险或规则已充分检查。

## 回归验证

在`backend`目录，使用已配置的独立环境运行：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:packages/harness:../vendor/bid_audit_tools \
  .venv/bin/python -m pytest \
  tests/test_collusion_pdf_access_v28.py \
  tests/test_collusion_calculation_process_v28.py \
  tests/test_collusion_calculation_cache_v28.py
```

覆盖合成混合xref的文本和页码保真、并发文档隔离、原文件不变、流所有权、坏文件失败、子进程超时与取消回收，以及缓存命中、输入变化失效和篡改拒绝。真实项目验证应另用隔离运行目录保存结果，结果及轨迹不提交到代码仓库。
