import pytest

from app.experiments.collusion_audit.report_checks import report_text_errors


@pytest.mark.parametrize("verb", ["确认", "核实", "查明"])
def test_future_alternative_verification_is_not_a_definite_mechanism(verb):
    text = "后续建议：获取PDF生成工具版本，" + verb + "子集前缀是确定性哈希还是随机生成。"
    assert report_text_errors(text, {}, expected_grade="电子文件辅助线索", rule_id="M11") == []


@pytest.mark.parametrize(
    "text",
    [
        "子集前缀是确定性哈希生成。",
        "已确认子集前缀是确定性哈希生成。",
        "建议确认子集前缀是确定性哈希还是随机生成，子集前缀是确定性生成。",
        "建议确认子集前缀是确定性哈希还是随机生成；字节复用已经排除。",
    ],
)
def test_future_question_does_not_hide_a_separate_unsupported_assertion(text):
    assert report_text_errors(text, {}, expected_grade="电子文件辅助线索", rule_id="M11")
