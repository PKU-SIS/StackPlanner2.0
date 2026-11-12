"""Project rounds are evidence from the current manifest, never a fixed scenario."""

import json

import pytest

from app.experiments.collusion_audit.profile import CollusionProfile


@pytest.mark.parametrize("rounds", [("1",), ("1", "2")])
@pytest.mark.parametrize("rule_level", [False, True])
def test_project_prompt_preserves_manifest_without_inventing_rounds(rounds, rule_level):
    profile = object.__new__(CollusionProfile)
    profile.rule = {"合并规则ID": "M14", "规则名称": "跨轮报价", "通用规则内容": "完整通用判断标准"}
    profile.rule_id = "M14"
    profile.rule_level_review = rule_level
    profile.knowledge = None
    profile.manifest = {"project_id": "current_project", "documents": [{"id": f"d{i:03d}", "round": value, "lot": "1"} for i, value in enumerate(rounds, 1)]}
    prompt = profile.user_input()
    assert "原始数据有两轮" not in prompt
    assert "轮次和包号以本次原始项目清单为准" in prompt
    assert "只有一轮时不得构造第二轮或跨轮比较" in prompt
    assert json.dumps(profile.rule, ensure_ascii=False, indent=2) in prompt
    assert json.dumps(profile.manifest, ensure_ascii=False, indent=2) in prompt
    assert "不能仅凭同名比较" in prompt
    assert "请先调用现成代码审核工具audit_m14" in prompt
