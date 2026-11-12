"""Human HTML delivery: provenance stays internal; native decisions stay native."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from test_collusion_audit import scoped_profile as scoped_profile


def sample_html(extra=""):
    sections = {
        "overview": "<h2>审核意见</h2><p>存在需要进一步核实的文本同源线索；尚未证明由同一人编制。</p>",
        "scope": "<h2>检查什么、检查到哪里</h2><p>对甲公司与乙公司的投标文件和招标原文进行对照，未完成全部页面的图像核对。</p>",
        "findings": "<h2>发现与证据</h2><table><tr><th>甲公司</th><th>乙公司</th></tr><tr><td>项目实施方案</td><td>工程实施方案</td></tr></table><blockquote>完整且可核对的原文证据，型号为VP-7000。</blockquote>",
        "explanations": "<h2>正常解释与排除</h2><p>公共模板是一种尚未核实的解释，不能当作已排除。</p>",
        "limitations": "<h2>局限与未核事项</h2><p>缺少制作环境资料，本次没有核实外部公共来源。</p>",
        "recommendations": "<h2>后续建议</h2><p>请核对原始编辑文件及相应来源。</p>",
        "sources": "<h2>证据定位</h2><p>甲公司《投标文件》，物理第12页；乙公司《投标文件》，物理第18页。证据等级：文本同源线索</p>",
    }
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>投标文件文本同源审核</title><style>body{font:16px sans-serif;max-width:70rem;margin:auto}table{width:100%}</style></head><body><main><h1>投标文件文本同源审核</h1>"
        + "".join(f'<section id="{key}">{value}</section>' for key, value in sections.items())
        + extra
        + "</main></body></html>"
    )


@pytest.fixture
def html_profile(scoped_profile, tmp_path):
    from app.experiments.collusion_audit.profile import CollusionProfile

    return CollusionProfile(scoped_profile.rule, scoped_profile.project, scoped_profile.outputs, tmp_path / "html-cache", scoped_profile.trace, quality_controls=True, rule_level_review=True, report_format="html")


def test_html_mode_is_explicit_and_legacy_prompt_stays_markdown(scoped_profile, html_profile):
    assert scoped_profile.report_format == "markdown"
    assert not scoped_profile.extensions().preserve_registered_tool_intent
    assert html_profile.extensions().preserve_registered_tool_intent
    assert "Markdown" in scoped_profile.user_input()
    assert "HTML" in html_profile.user_input()
    assert "实际.md文件" not in html_profile.user_input()
    from app.experiments.collusion_audit.profile import CollusionProfile

    with pytest.raises(ValueError):
        CollusionProfile(scoped_profile.rule, scoped_profile.project, scoped_profile.outputs, scoped_profile.outputs / "bad", scoped_profile.trace, report_format="pdf")


def test_reader_context_is_generic_and_maps_sources_to_human_names():
    from app.experiments.collusion_audit.html_report import reader_context

    context = reader_context(
        {"合并规则ID": "M01", "规则名称": "文本同源", "通用规则内容": "FULL_GENERAL_RULE", "历史经验": "SECRET_HISTORY"},
        {"project_id": "private-id", "project_name": "采购项目", "documents": [{"id": "d001", "bidder": "甲公司", "role": "bid", "path": "opaque.pdf"}, {"id": "d002", "role": "tender", "path": "tender.docx"}]},
    )
    assert context["document_labels"]["d001"] == "甲公司《投标文件》"
    assert "招标文件" in context["document_labels"]["d002"]
    assert "FULL_GENERAL_RULE" in json.dumps(context)
    assert "SECRET_HISTORY" not in json.dumps(context)
    assert "private-id" not in json.dumps(context)


def test_self_contained_human_html_is_accepted():
    from app.experiments.collusion_audit.html_report import validate_html_report

    assert validate_html_report(sample_html(), expected_grade="文本同源线索") == []


@pytest.mark.parametrize("leak", ["run_id=secret", "code_status=partial", "文档d001", "候选C000001", "规则M001", "E000001", "finding_count=20", "Reporter已完成", "<!-- run_id=secret -->", '<p data-source="d001">引用</p>'])
def test_technical_codes_are_not_leaked_even_in_comments_or_attributes(leak):
    from app.experiments.collusion_audit.html_report import validate_html_report

    assert validate_html_report(sample_html(leak))


@pytest.mark.parametrize(
    "unsafe",
    [
        "<script>alert(1)</script>",
        '<iframe src="https://example.org"></iframe>',
        '<img src="https://example.org/image.png">',
        '<p onclick="alert(1)">click</p>',
        '<a href="file:///data/private.pdf">来源</a>',
        '<link rel="stylesheet" href="https://example.org/style.css">',
    ],
)
def test_final_html_is_portable_and_has_no_active_or_external_resources(unsafe):
    from app.experiments.collusion_audit.html_report import validate_html_report

    assert validate_html_report(sample_html(unsafe))


def test_missing_coverage_limitations_or_real_html_is_not_silently_repaired():
    from app.experiments.collusion_audit.html_report import validate_html_report

    assert validate_html_report("# 原始Markdown报告")
    assert not validate_html_report(sample_html().replace('id="limitations"', 'id="elsewhere"'))
    assert validate_html_report(sample_html().replace("完整且可核对的原文证据", "{{请填入真实证据}}"))


def test_html_reporter_contract_uses_html_and_keeps_internal_binding_out_of_body(html_profile):
    from deerflow.sp.subagents import SPSubagentTask
    from deerflow.subagents.config import SubagentConfig

    task = SPSubagentTask("spact_html_test123456", "reporter", "生成报告", "report", context_refs={"latest_user_input": html_profile.user_input()})
    html_profile.prepare_task(task)
    assert task.context_refs["audit_report_contract"]["path"].endswith(".html")
    assert task.report_reference_contract.required_literals == ()
    assert task.report_reference_contract.content_type == "text/html"
    assert task.context_refs["report_requirements"]["required_literal_tokens"] == []
    assert "reader_report_context" in task.context_refs
    configured = html_profile.configure_subagent(task, SubagentConfig("sp-reporter", "report", system_prompt="Write Markdown to report.md", tools=["write_file"]))
    assert "report.html" in configured.system_prompt
    assert "纯Markdown" not in configured.system_prompt
    assert task.subagent_type == "reporter" and task.task == "生成报告"


def test_html_files_are_real_tool_creations_and_no_other_run_is_readable(html_profile, scoped_profile):
    writer = next(t for t in html_profile.tools if t.name == "write_file")
    path = "/mnt/user-data/outputs/test-action.html"
    created = json.loads(asyncio.run(writer.ainvoke({"path": path, "content": sample_html()})))
    assert created["created"] == path
    reader = next(t for t in html_profile.tools if t.name == "read_file")
    assert json.loads(asyncio.run(reader.ainvoke({"file_path": path})))["text"] == sample_html()
    repeat = json.loads(asyncio.run(writer.ainvoke({"path": path, "content": "overwrite"})))
    assert repeat["status"] == "error"
    assert (html_profile.outputs / "test-action.html").read_text() == sample_html()
    assert json.loads(asyncio.run(reader.ainvoke({"file_path": "/outside/project/.env"})))["status"] == "error"
    old_writer = next(t for t in scoped_profile.tools if t.name == "write_file")
    assert json.loads(asyncio.run(old_writer.ainvoke({"path": "/mnt/user-data/outputs/not-enabled.html", "content": sample_html()})))["status"] == "error"


def test_native_finish_selection_is_format_specific_and_author_independent():
    from app.experiments.collusion_audit.runner import select_markdown_artifact, select_report_artifact

    ref = {"artifact_id": "selected", "run_id": "current", "type": "report", "is_current": True, "virtual_path": "/mnt/user-data/outputs/report-action.html", "metadata": {"target_agent": "coder"}}
    state = {"sp_current_stage": "finished", "sp_last_final_artifact_ref": "selected", "sp_current_artifact_refs": {"report": ref}}
    assert select_report_artifact(state, "current", "html") == ref
    assert select_markdown_artifact(state, "current") is None
    assert select_report_artifact(state, "other", "html") is None
    assert select_report_artifact({**state, "sp_current_stage": "reporting"}, "current", "html") is None


def test_visual_counts_are_human_labels_but_still_bound_to_metadata():
    from app.experiments.collusion_audit.html_report import validate_html_report

    text = sample_html("<table><tr><th>逐对查看图像</th><td>1组</td></tr><tr><th>逐对核对扫描文字</th><td>2组</td></tr><tr><th>涉及不同页面</th><td>8页</td></tr></table>")
    metadata = {"visual_reviewed_candidates": [4], "ocr_reviewed_candidates": [2, 3], "candidate_page_count": 8}
    assert validate_html_report(text, rule_id="M06", metadata=metadata) == []
    from app.experiments.collusion_audit.html_report import html_report_review

    assert html_report_review(text, rule_id="M06", metadata={**metadata, "visual_reviewed_candidates": [4, 5]})["warnings"]


def test_template_is_blank_and_preserves_sources_uncertainty_and_reader_sections():
    from app.experiments.collusion_audit.html_report import template_text

    text = template_text()
    for required in ["overview", "scope", "findings", "explanations", "limitations", "recommendations", "sources"]:
        assert f'id="{required}"' in text
    for forbidden in ["九州通", "天医", "涛瑞", "145倍", "历史分析", "GT答案"]:
        assert forbidden not in text
    assert 'charset="utf-8"' in text


def test_html_instructions_preserve_knowledge_path_and_remove_old_body_obligations(html_profile):
    html_profile.knowledge = SimpleNamespace(context=lambda **kw: {})
    text = html_profile.instructions()
    assert "/knowledge/dXXX/parsed.md" in text
    assert "规则级Markdown报告" not in text
    assert "真实计算run_id和status" not in text
    assert "实际.html文件" in text


def test_reader_validation_never_promotes_partial_and_host_overwrites_spoofed_binding(html_profile):
    from deerflow.sp.subagents import SPSubagentTask
    from deerflow.sp.subagents.adapter import SPSubagentResult, SPSubagentStatus

    virtual = "/mnt/user-data/outputs/current.html"
    actual = html_profile.outputs / "current.html"
    actual.write_text(sample_html())
    task = SPSubagentTask("action", "reporter", "write report", "report", run_id="actual-run")
    result = SPSubagentResult(SPSubagentStatus.COMPLETED, completion_status="partial", artifact_metadata={"created_paths": [virtual], "audit_context_binding": {"host_stamped": True, "sp_execution_run_id": "spoofed"}})
    html_profile.rule["证据等级"] = "文本同源线索"
    html_profile.validate_reader_report(task, result)
    assert result.completion_status == "partial"
    assert result.artifact_metadata["audit_context_binding"]["sp_execution_run_id"] == "actual-run"
    assert result.artifact_metadata["reader_report_checks"]["passed"]
    assert result.artifact_metadata["reader_report_checks"]["json_repair"] is False
    actual.write_text(sample_html("run_id=secret"))
    result.completion_status = "complete"
    html_profile.validate_reader_report(task, result)
    assert result.completion_status == "partial"


def test_finish_requires_real_current_html_and_calculation_without_selecting_role(html_profile):
    html_profile.rule["证据等级"] = "文本同源线索"
    virtual = "/mnt/user-data/outputs/current.html"
    actual = html_profile.outputs / "current.html"
    actual.write_text(sample_html())
    ref = {"artifact_id": "current-artifact", "run_id": "actual-run", "virtual_path": virtual, "is_current": True, "metadata": {"target_agent": "coder"}}
    action = SimpleNamespace(metadata={"final_artifact_ref": "current-artifact"}, input_refs=[])
    context = SimpleNamespace(run_id="actual-run", state={"sp_current_artifact_refs": {"report": ref}})
    assert "代码" in html_profile.reader_finish_error(action, context)
    html_profile.adapters.runs.append({"run_id": "calculation", "status": "ok"})
    assert html_profile.reader_finish_error(action, context) is None
    ref["run_id"] = "other-run"
    assert html_profile.reader_finish_error(action, context)
    ref["run_id"] = "actual-run"
    actual.write_text(sample_html("code_status=ok"))
    assert html_profile.reader_finish_error(action, context)


def test_html_visual_guidance_uses_reader_coverage_labels_without_changing_source_paths(html_profile):
    html_profile.rule_id = "M06"
    text = html_profile.quality_guidance()
    assert "视觉判读候选数：N" not in text
    assert "候选涉及唯一页数：N" not in text
    assert "逐对查看图像" in text
    assert "逐对核对扫描文字" in text
    assert "/knowledge/dXXX/pages/起始页-结束页.md" in text
    assert "visual_reviewed_candidates" in text


def test_reader_stage_handoff_is_natural_and_preserves_complete_sources(html_profile, scoped_profile):
    text = html_profile.instructions()
    assert "不把候选逐条长表" in text
    assert "完整持久化代码与原文保留且可按需读取" in text
    assert "不能省略独立的实质发现" in text
    assert "不把候选逐条长表" not in scoped_profile.instructions()


def test_reader_task_describes_actual_capabilities_without_assigning_next_role(html_profile, scoped_profile):
    text = html_profile.instructions()
    assert "Researcher已注册本地证据研究能力" in text
    assert "不以互联网检索为前提" in text
    assert "控制检查点不会重开已经完成的感知阶段" in text
    assert "角色与先后顺序仍由Central自主选择" in text
    assert "Researcher已注册本地证据研究能力" not in scoped_profile.instructions()
