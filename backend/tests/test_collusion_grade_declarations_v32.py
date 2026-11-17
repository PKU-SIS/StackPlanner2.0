import pytest

from app.experiments.collusion_audit.report_checks import report_text_errors


@pytest.mark.parametrize("prefix", ["本项规则的证据等级为", "本项规则证据等级为", "该项规则的证据等级是", "本次规则的证据等级为", "本规则证据等级为", "本规则的证据等级为", "该规则证据等级是", "本项全部证据属于"])
def test_rule_grade_declarations_keep_literal_grade(prefix):
    assert report_text_errors("证据等级：" + prefix + "“辅助编辑线索”，仅为辅助事实。", {}, expected_grade="辅助编辑线索", rule_id="M05") == []


@pytest.mark.parametrize("grade", ["辅助线索", "高风险辅助编辑线索", "参数响应线索"])
def test_rule_prefix_does_not_allow_changed_grade(grade):
    assert report_text_errors("证据等级：本项规则的证据等级为" + grade, {}, expected_grade="辅助编辑线索", rule_id="M05")
