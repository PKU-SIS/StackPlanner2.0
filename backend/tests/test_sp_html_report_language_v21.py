"""Opt-in HTML reports are judged by visible language, never CSS or script text."""

import pytest

from deerflow.sp.actions.handlers.delegate import _report_language_alignment_gaps
from deerflow.sp.subagents import SPSubagentTask
from deerflow.sp.subagents.adapter import SPReportReferenceContract


def task(content_type=None, metadata=None):
    result = SPSubagentTask("current-action", "reporter", "生成中文报告", "report", context_refs={"latest_user_input": "请生成简明中文采购审核报告。"}, metadata=metadata or {})
    if content_type:
        result.report_reference_contract = SPReportReferenceContract(content_type=content_type)
    return result


def page(visible, style="color:blue;" * 1000):
    return '<!doctype html><html lang="zh"><head><title>English template title</title><style>' + style + "</style></head><body>" + visible + "</body></html>"


def test_css_heavy_short_chinese_report_passes_only_with_trusted_html_contract():
    text = page("<h1>审核意见</h1><p>" + "本次检查存在需要核实的文本同源线索，仍应考虑正常解释与资料局限。" * 4 + "</p>")
    assert _report_language_alignment_gaps(text, task=task())
    assert _report_language_alignment_gaps(text, task=task("text/html")) == []


def test_english_body_cannot_pass_using_chinese_css_script_or_head():
    text = page("<p>" + "The evidence remains inconclusive and needs independent verification. " * 20 + "</p>", style="/*" + "审核意见" * 1000 + "*/")
    text = text.replace("</head>", "<script>" + "中文说明" * 1000 + "</script></head>")
    assert _report_language_alignment_gaps(text, task=task("text/html"))


def test_model_metadata_cannot_enable_html_language_projection():
    text = page("<p>" + "存在需核实的采购风险，但目前证据不能认定共同编制。" * 4 + "</p>")
    assert _report_language_alignment_gaps(text, task=task(metadata={"content_type": "text/html"}))


def test_markdown_and_plain_chinese_keep_native_language_check():
    assert _report_language_alignment_gaps("审核意见：" + ("存在需核实的采购风险，但不能认定违法。" * 20), task=task()) == []
    assert _report_language_alignment_gaps("English report. " * 30, task=task())


@pytest.mark.parametrize("value", ["html", "application/json", 1])
def test_invalid_host_report_content_type_is_rejected(value):
    with pytest.raises(ValueError):
        SPReportReferenceContract(content_type=value)
