# 固定首遍保留

代码1e301a7；三主题各计划4轮。实际3轮（各第一轮）均失败，后9轮因依赖未执行。
低代码253404 token/7个工具错误；制造业166116 token/5个工具错误；A2A230581 token/6个工具错误。
均未交付新版本，旧版SHA未变。人工介入被测运行0次，批次内未调代码、Skill或query。
根因：基线已有completed的local-t0002-1写作请求，新会话第二轮用同一编号，
begin_writer_attempt拒绝已completed请求，导致Writer保存失败，随后Central多次错误恢复。
修复改用原生ledger全部已有write_requests分配唯一local编号，保留原请求与重放幂等；没有放松校验或增派agent。
新固定复测另开目录，原失败不覆盖。
