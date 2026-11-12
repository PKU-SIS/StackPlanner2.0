"""Isolation and autonomous native-SP integration contracts."""

import json
from pathlib import Path

import pytest

from app.experiments.collusion_audit.data import allowed_rule_fields, build_manifest, clean_rules, safe_document
from app.experiments.collusion_audit.profile import CollusionProfile


def test_rules_never_export_history():
    row = {"合并规则ID": "M01", "规则名称": "generic", "通用规则内容": "criterion", "通用判断方法": "method", "出现项目": "SECRET_PROJECT_HISTORY", "项目状态概览": "SECRET_VERDICT", "报告使用状态": "SECRET_REPORT"}
    result = clean_rules([row])
    assert result == [{key: row.get(key) for key in allowed_rule_fields}]
    assert "SECRET" not in json.dumps(result)


@pytest.mark.parametrize("path", ["分析报告/original.pdf", "分析结果/output.pdf", "历史分析报告_原三家目录/bid.pdf", "采购包1/company/风险初筛报告.pdf", "采购包1/company/archive.zip"])
def test_historical_and_archived_inputs_rejected(path):
    assert not safe_document(Path(path))


def test_legitimate_analysis_in_project_name_is_allowed():
    assert safe_document(Path("第一次开标/定量分析系统招标文件/招标文件.pdf"))


def test_round_bidder_and_quote_roles_are_preserved(tmp_path):
    p = tmp_path / "第一次开标/采购包1/甲公司(包1)/分项报价表.pdf"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"original")
    manifest = build_manifest(tmp_path, [p])
    d = manifest["documents"][0]
    assert (d["role"], d["bidder"], d["round"], d["lot"]) == ("quote_detail", "甲公司", "1", "1")


def test_prompt_never_prescribes_next_agent():
    import inspect

    source = inspect.getsource(CollusionProfile)
    assert "先委派 researcher" not in source
    assert "再委派 reporter" not in source
    assert "sp_delegate(" not in source


def test_read_file_cannot_escape_project_or_outputs(tmp_path):
    profile = object.__new__(CollusionProfile)
    profile.outputs = tmp_path / "outputs"
    profile.outputs.mkdir()
    with pytest.raises(ValueError):
        profile.output_path("/outside/project/.env")
    with pytest.raises(ValueError):
        profile.output_path("/mnt/user-data/outputs/../../old_results.json")


@pytest.fixture
def scoped_profile(tmp_path, monkeypatch):
    from app.experiments.tender_audit.observability import AuditTrace

    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "vendor/bid_audit_tools"))
    project = tmp_path / "project"
    project.mkdir()
    docs = []
    for number, company in enumerate(["A", "B"]):
        path = project / f"{number}.txt"
        path.write_text("这是投标人自行编写且具有足够长度的专属服务响应内容双方在现场共同执行检查方案。")
        docs.append({"id": f"d{number}", "path": path.name, "role": "bid", "bidder": company, "round": "1", "lot": "1"})
    (project / "manifest.json").write_text(json.dumps({"schema_version": "1.0", "project_id": "synthetic", "documents": docs}))
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    trace = AuditTrace(tmp_path / "trace")
    rule = {key: "通用测试规则" for key in allowed_rule_fields}
    rule["合并规则ID"] = "M01"
    return CollusionProfile(rule, project, outputs, tmp_path / "cache", trace)


def test_only_current_rule_calculator_is_registered(scoped_profile):
    names = [tool.name for tool in scoped_profile.tools]
    assert "audit_m01" in names
    assert all(f"audit_m{i:02d}" not in names for i in range(2, 16))
    assert "bash" not in names
    assert "web_search" not in names


def test_tools_execute_real_algorithms_and_preserve_evidence(scoped_profile):
    import asyncio

    tools = {tool.name: tool for tool in scoped_profile.tools}
    result = json.loads(asyncio.run(tools["audit_m01"].ainvoke({})))
    assert result["status"] in {"ok", "partial"}
    assert result["risk_verdict"] is None
    assert result["finding_count"] >= 1
    evidence = json.loads(asyncio.run(tools["audit_read_result"].ainvoke({"run_id": result["run_id"], "collection": "evidence", "limit": 1})))
    assert evidence["total"] >= 2
    assert evidence["next_offset"] == 1
    assert evidence["items"][0]["document_id"] in {"d0", "d1"}


def test_tools_reject_host_paths_and_other_rule_runs(scoped_profile):
    import asyncio

    tools = {tool.name: tool for tool in scoped_profile.tools}
    result = json.loads(asyncio.run(tools["read_file"].ainvoke({"file_path": "/outside/project/.env"})))
    assert result["status"] == "error"
    result = json.loads(asyncio.run(tools["audit_read_result"].ainvoke({"run_id": "M02_20261106T000000_abcdef1234"})))
    assert result["status"] == "error"


def test_original_text_reads_do_not_invent_pages(scoped_profile):
    import asyncio

    tool = next(tool for tool in scoped_profile.tools if tool.name == "project_read")
    result = json.loads(asyncio.run(tool.ainvoke({"document_id": "d0"})))
    assert result["pages"][0]["physical_page"] is None
    assert "自行编写" in result["pages"][0]["text"]


@pytest.mark.parametrize("role", ["researcher", "reporter", "perception", "coder"])
def test_profile_crosses_native_executor_boundary_without_changing_role(monkeypatch, scoped_profile, role):
    from types import SimpleNamespace

    from deerflow.sp.runtime import DR2SPExecutorProvider
    from deerflow.sp.subagents import SPSubagentTask
    from deerflow.subagents.config import SubagentConfig

    captured = {}
    original = SubagentConfig(name="sp-" + role, description="test", internal=True, skills=[])

    class Executor:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def execute(self, prompt):
            captured["prompt"] = prompt
            return SimpleNamespace(
                status="completed",
                result=json.dumps({"summary": "done", "artifact_content": "computed evidence", "artifact_type": "research_observation", "artifact_metadata": {"completion_status": "complete"}}),
                error=None,
                stop_reason=None,
                token_usage_records=[],
            )

    monkeypatch.setattr("deerflow.subagents.get_subagent_config", lambda *a, **kw: original)
    monkeypatch.setattr("deerflow.subagents.SubagentExecutor", Executor)
    provider = DR2SPExecutorProvider(app_config=SimpleNamespace(), parent_model="model", runnable_config={}, extensions=scoped_profile.extensions())
    task = SPSubagentTask("action", role, "审核当前规则", "test")
    result = provider({}, SimpleNamespace(context={"thread_id": "t", "run_id": "r"}, stream_writer=None)).execute(task)
    assert result.is_success
    assert task.subagent_type == role
    assert captured["config"].name == "sp-" + role
    assert "audit_m01" in captured["config"].tools
    assert "通用测试规则" in captured["prompt"]
    assert original.tools is None


@pytest.mark.parametrize("role", ["reporter", "coder", "researcher"])
def test_finished_markdown_is_accepted_without_forcing_author_role(role):
    from app.experiments.collusion_audit.runner import select_markdown_artifact

    ref = {"run_id": "r", "artifact_id": "accepted", "virtual_path": "/mnt/user-data/outputs/M01.md", "metadata": {"target_agent": role}}
    state = {"sp_current_stage": "finished", "sp_last_final_artifact_ref": "accepted", "sp_current_artifact_refs": {"report": ref}}
    assert select_markdown_artifact(state, "r") == ref
    assert select_markdown_artifact(state, "other-run") is None


def test_collection_overview_rejects_traversal_even_in_a_forged_summary(scoped_profile):
    import asyncio

    run_id = "M01_20261106T000000_abcdef1234"
    directory = scoped_profile.outputs / "bid_audit" / run_id
    directory.mkdir(parents=True)
    (directory / "result.json").write_text(json.dumps({"collections": {"../../private": {"count": 1}}}))
    tool = next(t for t in scoped_profile.tools if t.name == "audit_collection_overview")
    result = json.loads(asyncio.run(tool.ainvoke({"run_id": run_id, "collection": "../../private"})))
    assert result["status"] == "error"
    assert "Invalid collection name" in result["error"]


def test_markdown_writer_uses_native_path_argument_and_recovers_created_file(scoped_profile):
    import asyncio
    from types import SimpleNamespace

    from deerflow.sp.subagents import SPSubagentTask
    from deerflow.sp.subagents.dr2_adapter import normalize_dr2_subagent_result

    writer = next(tool for tool in scoped_profile.tools if tool.name == "write_file")
    path = "/mnt/user-data/outputs/M01.md"
    result = json.loads(asyncio.run(writer.ainvoke({"path": path, "content": "# M01\n\n原始Markdown报告。\n"})))
    assert result["created"] == path
    assert (scoped_profile.outputs / "M01.md").read_text().startswith("# M01\n")
    task = SPSubagentTask("a", "coder", "整理报告", "report")
    scoped_profile.prepare_task(task)
    messages = [{"type": "ai", "tool_calls": [{"id": "write", "name": "write_file", "args": {"path": path, "content": "# M01"}}]}, {"type": "tool", "tool_call_id": "write", "name": "write_file", "content": json.dumps(result)}]
    raw = SimpleNamespace(status="completed", result="```json invalid outer envelope", ai_messages=messages, error=None, stop_reason=None)
    normalized = normalize_dr2_subagent_result(raw, task=task)
    assert normalized.artifact_metadata["created_paths"] == [path]
    assert normalized.artifact_content is None


def test_native_finish_of_txt_is_not_silently_renamed_as_markdown():
    from app.experiments.collusion_audit.runner import select_markdown_artifact

    state = {"sp_current_stage": "finished", "sp_last_final_artifact_ref": "a", "sp_current_artifact_refs": {"generated_file": {"run_id": "r", "artifact_id": "a", "virtual_path": "/mnt/user-data/outputs/report.txt"}}}
    assert select_markdown_artifact(state, "r") is None


def test_tender_zip_staging_excludes_reports_and_traversal(tmp_path):
    import zipfile

    from app.experiments.collusion_audit.prepare_complete import add_tender_archives

    source = tmp_path / "raw"
    (source / "第二次开标").mkdir(parents=True)
    archive = source / "第二次开标/第二轮招标文件.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("真实招标文件.pdf", b"original-pdf")
        handle.writestr("真实招标文件.docx", b"original-docx")
        handle.writestr("分析报告/招标文件.pdf", b"SECRET_HISTORY")
        handle.writestr("../招标文件.pdf", b"OUTSIDE")
        handle.writestr("__MACOSX/._招标文件.pdf", b"metadata")
    destination = tmp_path / "clean"
    destination.mkdir()
    added, duplicates = add_tender_archives(source, destination)
    assert len(added) == 2 and not duplicates
    assert all("SECRET" not in p.read_text() for p in destination.rglob("*") if p.is_file())
    again, duplicates = add_tender_archives(source, destination)
    assert not again and len(duplicates) == 2


def test_baseline_scope_is_computed_from_each_round(tmp_path):
    files = []
    for folder in ["第一次开标", "第二次开标"]:
        path = tmp_path / folder / "招标文件.pdf"
        path.parent.mkdir()
        path.write_bytes(folder.encode())
        files.append(path)
    manifest = build_manifest(tmp_path, files)
    assert "每轮均提供独立招标文件" in manifest["scope_note"]
    assert "缺少" not in manifest["scope_note"]


def test_profile_preserves_native_explicit_tool_selection(scoped_profile):
    from deerflow.sp.subagents import SPSubagentTask
    from deerflow.subagents.config import SubagentConfig

    task = SPSubagentTask("a", "coder", "计算", "implementation", metadata={"tool_names": ["audit_m01"]})
    task.allowed_tools = ["audit_m01"]
    scoped_profile.prepare_task(task)
    configured = scoped_profile.configure_subagent(task, SubagentConfig(name="sp-coder", description="test", internal=True, skills=[]))
    assert task.metadata["tool_names"] == ["audit_m01"]
    assert task.allowed_tools == ["audit_m01"]
    assert "project_read" in configured.tools
    assert task.subagent_type == "coder"


def test_profile_keeps_native_reporting_skill(scoped_profile):
    from deerflow.sp.subagents import SPSubagentTask
    from deerflow.subagents.config import SubagentConfig

    task = SPSubagentTask("a", "reporter", "报告", "report")
    scoped_profile.prepare_task(task)
    config = SubagentConfig(name="sp-reporter", description="test", internal=True, skills=["stackplanner-reporting"])
    assert scoped_profile.configure_subagent(task, config).skills == ["stackplanner-reporting"]


def test_prompt_distinguishes_stage_completion_from_final_delivery():
    from app.experiments.collusion_audit.profile import SKILL

    assert "阶段产物" in SKILL
    assert "required_artifact_type" in SKILL
    assert "tool_output_report" in SKILL
    assert "不要为了准备返回结果而再次调用工具" in SKILL


def test_delegated_stage_context_does_not_unconditionally_require_report(scoped_profile):
    from deerflow.sp.subagents import SPSubagentTask
    from deerflow.subagents.config import SubagentConfig

    task = SPSubagentTask("a", "coder", "只计算并返回阶段结果，不写报告", "calculation")
    scoped_profile.prepare_task(task)
    assert "audit_report_instructions" not in task.context_refs
    context = task.context_refs["audit_task_context"]
    assert context["rule"]["合并规则ID"] == "M01"
    assert "domain_guidance" in context
    cfg = scoped_profile.configure_subagent(task, SubagentConfig(name="sp-coder", description="test", internal=True, skills=[]))
    assert "当前委派task/expected_output" in cfg.system_prompt
    assert "sp_finish" not in cfg.system_prompt
    assert cfg.max_turns >= 160


@pytest.mark.parametrize("file_path", ["0.txt", "/project/0.txt"])
def test_native_read_file_can_read_only_manifest_originals(scoped_profile, file_path):
    import asyncio

    reader = next(t for t in scoped_profile.tools if t.name == "read_file")
    result = json.loads(asyncio.run(reader.ainvoke({"file_path": file_path, "offset": 0, "limit": 40})))
    assert result["document_id"] == "d0"
    assert result["source_pages"] == [None]
    assert "自行编写" in result["text"]
    assert result["next_offset"] == 40


def test_native_read_file_rejects_unlisted_project_reports(scoped_profile):
    import asyncio

    (scoped_profile.project / "分析报告.txt").write_text("HELD_OUT_HISTORY")
    reader = next(t for t in scoped_profile.tools if t.name == "read_file")
    result = json.loads(asyncio.run(reader.ainvoke({"file_path": "/project/分析报告.txt"})))
    assert result["status"] == "error"
    assert "HELD_OUT_HISTORY" not in json.dumps(result)


def test_report_contract_matches_native_current_action_provenance(scoped_profile):
    from deerflow.sp.actions.handlers.delegate import _report_created_path_matches_action
    from deerflow.sp.subagents import SPSubagentTask

    task = SPSubagentTask("spact_chatcmpl-tool-0123456789ab", "reporter", "交付报告", "reporting")
    scoped_profile.prepare_task(task)
    contract = task.context_refs["audit_report_contract"]
    assert _report_created_path_matches_action(contract["path"], task.action_id)
    assert contract["artifact_type"] == "report"
    assert contract["path"].endswith(".md")
    other = SPSubagentTask("spact_chatcmpl-tool-9876543210ab", "reporter", "修订", "revision")
    scoped_profile.prepare_task(other)
    assert contract["path"] != other.context_refs["audit_report_contract"]["path"]


def test_disclosed_data_limitations_do_not_claim_unmet_report_work():
    from types import SimpleNamespace

    from deerflow.sp.subagents import SPSubagentTask
    from deerflow.sp.subagents.dr2_adapter import normalize_dr2_subagent_result

    task = SPSubagentTask("a", "reporter", "报告如实披露数据缺口", "report")
    raw = SimpleNamespace(
        status="completed",
        result=json.dumps(
            {
                "summary": "已交付报告并披露扫描局限",
                "artifact_content": "# 报告\n\n扫描未OCR，披露覆盖限制。",
                "artifact_type": "report",
                "artifact_metadata": {"completion_status": "complete", "evidence_gaps": [], "data_limitations": ["扫描未OCR"]},
            }
        ),
        error=None,
        stop_reason=None,
        ai_messages=[],
    )
    result = normalize_dr2_subagent_result(raw, task=task)
    assert result.artifact_metadata["completion_status"] == "complete"
    assert result.artifact_metadata["data_limitations"] == ["扫描未OCR"]


def test_artifact_writes_alone_are_not_computation_receipts(scoped_profile):
    from types import SimpleNamespace

    from deerflow.sp.subagents import SPSubagentTask
    from deerflow.sp.subagents.dr2_adapter import normalize_dr2_subagent_result

    task = SPSubagentTask("a", "coder", "读取计算证据", "evidence")
    scoped_profile.prepare_task(task)
    assert "write_file" not in task.tool_execution_contract.execution_tools
    assert "write_file" in task.tool_execution_contract.artifact_write_tools
    messages = [
        {"type": "ai", "tool_calls": [{"id": "n", "name": "write_file", "args": {"path": "/mnt/user-data/outputs/_noop.txt", "content": "done"}}]},
        {"type": "tool", "tool_call_id": "n", "name": "write_file", "content": json.dumps({"created": "/mnt/user-data/outputs/_noop.txt"})},
    ]
    raw = SimpleNamespace(
        status="completed",
        result=json.dumps({"summary": "done", "artifact_content": "stage", "artifact_type": "tool_output_report", "artifact_metadata": {"completion_status": "complete"}}),
        ai_messages=messages,
        stop_reason=None,
        error=None,
    )
    result = normalize_dr2_subagent_result(raw, task=task)
    assert not result.artifact_metadata["execution_verification"]["passed"]
    assert result.artifact_metadata["completion_status"] == "partial"


def test_rejected_revision_token_is_not_both_required_and_forbidden(scoped_profile):
    from deerflow.sp.actions.handlers.delegate import _report_literal_provenance_gaps
    from deerflow.sp.subagents import SPSubagentTask

    task = SPSubagentTask("a", "reporter", "删除无来源BAD-1，保留SOURCE-1", "revision")
    task.context_refs = {
        "latest_user_input": scoped_profile.user_input(),
        "report_requirements": {"required_literal_tokens": ["SOURCE-1", "BAD-1"]},
        "artifact_refs": {
            "report": {"artifact_id": "previous", "type": "report", "metadata": {"target_agent": "reporter", "quality_checks": {"literal_token_provenance": {"allowed_tokens": ["SOURCE-1"], "introduced_tokens": ["BAD-1"], "passed": False}}}}
        },
    }
    scoped_profile.prepare_task(task)
    required = task.context_refs["report_requirements"]["required_literal_tokens"]
    assert required == ["SOURCE-1"]
    assert _report_literal_provenance_gaps("SOURCE-1", required_tokens=required)[0] == []
    assert _report_literal_provenance_gaps("SOURCE-1 BAD-1", required_tokens=required)[0] == ["BAD-1"]


def test_report_projection_does_not_override_a_new_human_instruction(scoped_profile):
    from deerflow.sp.subagents import SPSubagentTask

    task = SPSubagentTask("a", "reporter", "修订", "revision")
    task.context_refs = {
        "latest_user_input": "用户现在明确要求保留BAD-1",
        "report_requirements": {"required_literal_tokens": ["BAD-1"]},
        "artifact_refs": {"report": {"type": "report", "metadata": {"target_agent": "reporter", "quality_checks": {"literal_token_provenance": {"introduced_tokens": ["BAD-1"], "passed": False}}}}},
    }
    scoped_profile.prepare_task(task)
    assert task.context_refs["report_requirements"]["required_literal_tokens"] == ["BAD-1"]


def test_m04_requires_complete_available_semantic_review_without_seeding_case_counts(scoped_profile, tmp_path):
    rule = {**scoped_profile.rule, "合并规则ID": "M04"}
    profile = CollusionProfile(rule, scoped_profile.project, scoped_profile.outputs, tmp_path / "m04-cache", scoped_profile.trace)
    text = profile.user_input()
    assert "全部可读review_rows" in text
    assert "不能仅凭自报的无偏离" in text
    assert "未阅读的可读行属于未完成工作" in text
    assert "453条" not in text
    assert profile.rule == rule


def test_delegate_receives_only_actual_current_audit_run_ids(scoped_profile):
    from deerflow.sp.subagents import SPSubagentTask

    run_id = "M01_20261106T120000_0123456789"
    scoped_profile.adapters.runs.append({"run_id": run_id, "status": "partial", "result_directory": "/mnt/user-data/outputs/bid_audit/" + run_id, "collections": {"findings": 1}})
    task = SPSubagentTask("a", "coder", "review", "review", run_id="sp-execution-run")
    scoped_profile.prepare_task(task)
    refs = task.context_refs["audit_calculation_runs"]
    assert refs[0]["audit_run_id"] == run_id
    assert refs[0]["status"] == "partial"
    assert "sp-execution-run" not in str(refs)
    scoped_profile.adapters.runs.clear()
    assert refs[0]["audit_run_id"] == run_id


def test_report_run_id_constraint_uses_real_calculation_not_sp_execution(scoped_profile):
    from deerflow.sp.subagents import SPSubagentTask

    audit_id = "M01_20261106T120000_0123456789"
    scoped_profile.adapters.runs.append({"run_id": audit_id, "status": "ok", "collections": {}})
    task = SPSubagentTask("a", "reporter", "report", "report", run_id="sp-execution-run")
    task.context_refs["latest_user_input"] = scoped_profile.user_input()
    task.context_refs["report_requirements"] = {"required_literal_tokens": ["M01", "sp-execution-run", "OTHER-FACT"]}
    scoped_profile.prepare_task(task)
    assert task.context_refs["report_requirements"]["required_literal_tokens"] == ["M01", "OTHER-FACT", audit_id]


@pytest.mark.asyncio
async def test_wrong_result_run_id_returns_scoped_recovery_hint(scoped_profile):
    run_id = "M01_20261106T120000_0123456789"
    scoped_profile.adapters.runs.append({"run_id": run_id, "status": "ok"})
    tool = next(t for t in scoped_profile.tools if t.name == "audit_read_result")
    result = json.loads(await tool.ainvoke({"run_id": "sp-execution-run"}))
    assert result["status"] == "error"
    assert run_id in result["error"]
    assert "SP" in result["error"]


def test_read_file_pages_preserve_raw_rows_and_support_native_readonly_roles(scoped_profile):
    run_id = "M01_20261106T120000_0123456789"
    root = scoped_profile.outputs / "bid_audit" / run_id
    root.mkdir(parents=True)
    original = [{"row": i, "raw_text": "完整原始内容" + str(i)} for i in range(23)]
    source = root / "review_rows.jsonl"
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in original)
    source.write_text(text)
    result = {"run_id": run_id, "collections": {"review_rows": {"total": 23}}}
    pages = scoped_profile.adapters.materialize_read_pages(result)
    index = json.loads(scoped_profile.output_path(pages["index_path"]).read_text())
    recovered = []
    for page in index["collections"]["review_rows"]["pages"]:
        body = json.loads(scoped_profile.output_path(page["file_path"]).read_text())
        assert body["offset"] == len(recovered)
        recovered.extend(body["items"])
    assert recovered == original
    assert source.read_text() == text
    assert index["collections"]["review_rows"]["total"] == 23


def test_native_readonly_roles_require_terminal_protocol_without_writer(scoped_profile):
    from deerflow.sp.subagents import SPSubagentTask
    from deerflow.subagents.config import SubagentConfig

    task = SPSubagentTask("a", "perception", "inspect local result pages", "inspect")
    scoped_profile.prepare_task(task)
    assert task.tool_execution_contract.require_result_protocol
    assert not task.tool_execution_contract.artifact_write_tools
    cfg = scoped_profile.configure_subagent(task, SubagentConfig(name="sp-perception", description="test", disallowed_tools=["write_file"]))
    assert "read_file" in cfg.tools and "write_file" not in cfg.tools


def test_computation_scope_preserves_native_execution_intent():
    from app.experiments.collusion_audit.profile import COMPUTATION_STAGE_SCOPE
    from deerflow.sp.agent_tools import _action_payload

    description = COMPUTATION_STAGE_SCOPE.replace("RULE_ID", "M04")
    action = _action_payload("sp_delegate", {"target_agent": "coder", "stage": "research", "task": description, "reason": "按任务要求调用现成计算能力", "expected_output": "返回计算编号、状态、统计和原始集合位置"}, tool_call_id="scope")
    assert action["target_agent"] == "coder"
    assert action["stage"] == "research"


def test_real_mixed_case_audit_id_is_not_rejected_as_invented_prefix(scoped_profile):
    from deerflow.sp.actions.handlers.delegate import _report_literal_provenance_gaps
    from deerflow.sp.subagents import SPSubagentTask

    audit_id = "M01_20261106T213413_3593f606d0"
    scoped_profile.adapters.runs.append({"run_id": audit_id, "status": "ok", "collections": {}})
    task = SPSubagentTask("a", "reporter", "返回真实计算编号", "source", run_id="sp-run")
    task.context_refs["latest_user_input"] = scoped_profile.user_input()
    scoped_profile.prepare_task(task)
    required = task.context_refs["report_requirements"]["required_literal_tokens"]
    assert audit_id in required
    assert _report_literal_provenance_gaps(audit_id, required_tokens=required)[0] == []
    assert _report_literal_provenance_gaps(audit_id + " FAKE-999", required_tokens=required)[0] == ["FAKE-999"]
    # Native acceptance must still reject a report that contains only the prefix.
    from deerflow.sp.actions.handlers.delegate import _apply_report_acceptance_checks
    from deerflow.sp.subagents.adapter import SPSubagentResult, SPSubagentStatus

    short = SPSubagentResult(status=SPSubagentStatus.COMPLETED, artifact_content="M01_20261106T213413_3593", artifact_metadata={"completion_status": "complete"})
    _apply_report_acceptance_checks(short, task=task, context=None, artifact_adapter=None)
    assert audit_id in short.artifact_metadata["quality_checks"]["required_literal_tokens"]["missing"]
    assert short.artifact_metadata["completion_status"] == "partial"


def test_validly_shaped_but_unexecuted_calculation_id_is_rejected(scoped_profile):
    import asyncio

    tool = next(t for t in scoped_profile.tools if t.name == "audit_read_result")
    result = json.loads(asyncio.run(tool.ainvoke({"run_id": "M01_20261107T010203_abcdef1234"})))
    assert result["status"] == "error" and "registered" in result["error"]
