"""Rule-level delivery keeps full code facts and lets native Central choose work."""

import asyncio
import json
from types import SimpleNamespace

from app.experiments.collusion_audit.profile import CollusionProfile
from app.experiments.collusion_audit.recovery import recovery_scope_key
from app.experiments.collusion_audit.repetition import CURRENT_DELEGATION
from app.experiments.collusion_audit.tools import AuditTools
from app.experiments.tender_audit.observability import AuditTrace


def profile(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "manifest.json").write_text(json.dumps({"schema_version": "1.0", "project_id": "synthetic", "documents": []}))
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "index.json").write_text(json.dumps({"ready": True, "fingerprint": "synthetic", "documents": {}}))
    return CollusionProfile({"合并规则ID": "M04", "规则名称": "test", "通用规则内容": "same negative deviation"}, project, tmp_path / "outputs", tmp_path / "cache", AuditTrace(tmp_path / "trace"), knowledge_root=kb, rule_level_review=True)


def test_rule_mode_has_no_row_ledger_gate_or_annotation_workflow(tmp_path):
    p = profile(tmp_path)
    assert p.adapters.ledger is None
    assert p.extensions().finish_validator is None
    assert p.extensions().scoped_delegate_recovery is True
    names = {tool.name for tool in p.tools}
    assert {"audit_m04", "audit_read_complete", "knowledge_read", "ragflow_retrieve", "read_file", "view_image"} <= names
    assert not {"audit_record_reviews", "audit_review_units", "audit_review_status"} & names
    prompt = p.user_input()
    assert "必须覆盖代码返回的全部可读review_rows" not in prompt
    assert "【可核对业务完成门】" not in prompt
    assert "完整代码结果" in prompt
    task = SimpleNamespace(subagent_type="researcher", action_id="a", metadata={}, context_refs={}, artifact_type_aliases={}, tool_execution_contract=None)
    p.prepare_task(task)
    assert "audit_review_coverage" not in task.context_refs
    assert task.context_refs["audit_task_context"]["rule"] == p.rule
    assert "逐条提交全部记录的交付门槛" in task.context_refs["audit_task_context"]["domain_guidance"]


def test_complete_reader_returns_over20_records_and_unknown_run_is_rejected(tmp_path):
    p = profile(tmp_path)
    run_id = "M04_20261107T000000_0123456789"
    root = p.adapters.outputs / "bid_audit" / run_id
    root.mkdir(parents=True)
    rows = [{"requirement": str(i), "response": str(i)} for i in range(53)]
    (root / "review_rows.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    (root / "result.json").write_text(json.dumps({"rule_id": "M04", "manifest_sha256": "source", "collections": {"review_rows": {"count": 53, "file": "review_rows.jsonl"}}}))
    (root / "sources.json").write_text("{}")
    tool = next(t for t in p.tools if t.name == "audit_read_complete")

    async def read():
        rejected = json.loads(await tool.ainvoke({"run_id": run_id}))
        p.adapters.runs.append({"run_id": run_id})
        token = CURRENT_DELEGATION.set("a")
        try:
            packet = json.loads(await tool.ainvoke({"run_id": run_id}))
        finally:
            CURRENT_DELEGATION.reset(token)
        return rejected, packet

    rejected, packet = asyncio.run(read())
    assert rejected["status"] == "error"
    assert packet["complete"] and packet["collections"]["review_rows"]["record_count"] == 53
    assert p.adapters.scope_refs("a") == {"code:M04:source"}


def test_source_scope_comes_from_real_receipts_and_is_stable_across_query_words(tmp_path):
    a = AuditTools("M04", tmp_path, tmp_path / "out", tmp_path / "cache", AuditTrace(tmp_path / "trace"), knowledge=object(), rule_level_review=True)

    async def read(action, question, chunk):
        token = CURRENT_DELEGATION.set(action)
        try:
            await a.recorded("ragflow_retrieve", {"question": question}, lambda: {"document_id": "d1", "chunks": [{"id": chunk, "content": "actual OCR"}]})
        finally:
            CURRENT_DELEGATION.reset(token)

    asyncio.run(read("a", "query one", "c1"))
    asyncio.run(read("b", "different wording", "c1"))
    asyncio.run(read("c", "query one", "c2"))

    def task(action):
        return SimpleNamespace(action_id=action, context_refs={}, input_refs=[], metadata={"_sp_recovery_scope_key": "forged"})

    assert recovery_scope_key(a, task("a")) == recovery_scope_key(a, task("b"))
    assert recovery_scope_key(a, task("a")) != recovery_scope_key(a, task("c"))
    assert recovery_scope_key(a, task("unobserved")) is None
