"""Draft regression: precise feedback, unchanged acceptance rules, no rewriting."""

from app.experiments.collusion_audit.html_report import html_report_review


def page(body):
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><h1>审核意见</h1>' + body + "</body></html>"


def test_feedback_locates_rule_marker_and_surrounding_sentence():
    original = page("<p>该差异来自正常响应，不构成M02词级替换异常，应按实际证据判断。</p>")
    errors = html_report_review(original)["errors"]
    message = "\n".join(errors)
    assert "M02" in message
    assert "不构成M02词级替换异常" in message


def test_feedback_locates_internal_field_in_attribute():
    errors = html_report_review(page('<p data-field="run_id">审核意见和来源。</p>'))["errors"]
    assert any("run_id" in error and "data-field" in error for error in errors)


def test_feedback_samples_are_bounded_and_do_not_dump_report():
    text = page("<p>正文定位：M02词级替换异常。</p>" * 1000)
    message = "\n".join(html_report_review(text)["errors"])
    assert "M02" in message
    assert "1000" in message
    assert len(message) < 650


def test_business_wording_and_original_document_identifiers_still_pass():
    text = page("<p>本项检查未发现充分线索。原项目编号为GZ2026-018；依据广州企业投标文件第18页。</p>")
    assert html_report_review(text)["errors"] == []


def test_feedback_locates_count_field_for_business_translation():
    errors = html_report_review(page("<p>统计说明：finding_count=0，只说明计数结果。</p>"))["errors"]
    message = "\n".join(errors)
    assert "finding_count" in message
    assert "统计说明：finding_count=0" in message
