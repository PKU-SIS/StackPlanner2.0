"""Natural report declarations must not change the underlying evidence grade."""

import pytest

from app.experiments.collusion_audit.html_report import parse_reader_html
from app.experiments.collusion_audit.report_checks import assertion_errors, report_text_errors

GRADE = "参数响应线索"


@pytest.mark.parametrize(
    "declaration",
    [
        "参数响应线索，仅用于风险筛查。",
        "本项证据等级为参数响应线索，仅用于风险筛查。",
        "本次证据等级为参数响应线索。",
        "该项证据等级是参数响应线索。",
        "本报告的证据等级为“参数响应线索”。",
        "“参数响应线索”，尚需复核。",
        "本项为「参数响应线索」，仅用于筛查。",
    ],
)
def test_reader_grade_accepts_literal_grade_with_natural_declaration(declaration):
    assert report_text_errors("证据等级：" + declaration, {}, expected_grade=GRADE, rule_id="M04") == []


def test_html_inline_emphasis_preserves_natural_grade_declaration():
    text = parse_reader_html("<!doctype html><html><body><p><strong>证据等级：</strong>本项证据等级为<strong>参数响应线索</strong>。</p></body></html>").text
    assert report_text_errors(text, {}, expected_grade=GRADE, rule_id="M04") == []


@pytest.mark.parametrize(
    "declaration",
    [
        "PS-B（参数响应线索）",
        "本项证据等级为PS-B（参数响应线索）",
        "本项证据等级为辅助线索，规则原词是参数响应线索",
        "本项证据等级为高风险参数响应线索",
        "本项证据等级为参数响应",
        "本项为「PS-B」（参数响应线索）",
    ],
)
def test_reader_grade_rejects_changed_or_incomplete_grade_even_if_correct_words_appear_later(declaration):
    assert report_text_errors("证据等级：" + declaration, {}, expected_grade=GRADE, rule_id="M04")


def test_correct_second_declaration_does_not_hide_wrong_first_declaration():
    text = "证据等级：辅助线索\n证据等级：本项证据等级为参数响应线索"
    assert report_text_errors(text, {}, expected_grade=GRADE, rule_id="M04")


def test_metadata_grade_remains_exact_without_natural_language_normalization():
    assert assertion_errors({"evidence_level": "本项证据等级为参数响应线索"}, expected_grade=GRADE, rule_id="M04", displayed_pairs=set())
    assert assertion_errors({"evidence_level": GRADE}, expected_grade=GRADE, rule_id="M04", displayed_pairs=set()) == []
