"""Run-scoped resource separation, delta handoff, and minimal HTML acceptance."""

import pytest
from test_collusion_audit import scoped_profile as scoped_profile
from test_collusion_html_reports_v21 import sample_html

from app.experiments.collusion_audit.html_report import html_report_review, parse_reader_html, validate_html_report
from deerflow.sp.subagents import SPSubagentTask
from deerflow.subagents.config import SubagentConfig


def test_resources_are_isolated_and_false_thinking_is_explicit(scoped_profile, tmp_path):
    p = type(scoped_profile)(
        scoped_profile.rule,
        scoped_profile.project,
        scoped_profile.outputs,
        tmp_path / "cache22",
        scoped_profile.trace,
        quality_controls=True,
        rule_level_review=True,
        report_format="html",
        reporter_max_tokens=32768,
        reporter_request_timeout=1920,
        evidence_role_capacity=True,
        evidence_max_tokens=16384,
        evidence_request_timeout=960,
        researcher_no_thinking=True,
    )
    for role in ["coder", "researcher", "reporter", "perception", "memory_recaller"]:
        task = SPSubagentTask("action", role, "验证本次新证据", "真实受托目标", metadata={"model_thinking_enabled": True})
        p.prepare_task(task)
        cfg = p.configure_subagent(task, SubagentConfig("sp-" + role, "native"))
        assert cfg.model_max_tokens == ({"coder": 16384, "researcher": 16384, "reporter": 32768}.get(role))
        assert cfg.model_timeout_seconds == ({"coder": 960, "researcher": 960, "reporter": 1920}.get(role))
        assert cfg.model_thinking_enabled == (False if role == "researcher" else None)
        assert task.task == "验证本次新证据" and task.subagent_type == role
        if role == "researcher":
            assert "本轮新增" in cfg.system_prompt and "判断修正" in cfg.system_prompt
            assert "不重复展开" in cfg.system_prompt
    assert SubagentConfig("default", "native").model_thinking_enabled is None


@pytest.mark.parametrize("bad", [0, 1, "false", []])
def test_thinking_override_requires_trusted_boolean(bad):
    with pytest.raises(ValueError):
        SubagentConfig("x", "native", model_thinking_enabled=bad)


def test_html5_optional_cell_and_list_endings_preserve_real_text():
    html = sample_html().replace("<td>项目实施方案</td><td>工程实施方案</td>", "<td>项目实施方案<td>工程实施方案")
    html = html.replace("</main>", "<ul><li>第一项<li>第二项</ul></main>")
    assert validate_html_report(html, expected_grade="文本同源线索") == []
    assert ["项目实施方案", "工程实施方案"] in parse_reader_html(html).rows


def test_cosmetic_preferences_are_warnings_and_never_evidence_gaps():
    html = sample_html().replace('id="limitations"', 'id="different"').replace(' lang="zh-CN"', "")
    html = html.replace('<meta name="viewport" content="width=device-width,initial-scale=1">', "")
    review = html_report_review(html, expected_grade="文本同源线索")
    assert review["errors"] == [] and review["warnings"]
    assert validate_html_report(html, expected_grade="文本同源线索") == []
    assert validate_html_report(sample_html("<p>设备标记 device_model 为原件引用。</p>")) == []


@pytest.mark.parametrize("html", ["# Markdown", "<!doctype html><html><head><title>空壳</title></head><body></body></html>", sample_html()[:-7], sample_html("<p>{{未填入的内容}}</p>"), sample_html("run_id=secret")])
def test_real_delivery_errors_still_block(html):
    assert validate_html_report(html)


@pytest.mark.parametrize("host_override,expected", [(None, True), (False, False), (True, True)])
def test_trusted_thinking_override_reaches_actual_executor(monkeypatch, host_override, expected):
    from dataclasses import replace
    from types import SimpleNamespace

    from deerflow.sp.extensions import SPExecutionExtensions
    from deerflow.sp.runtime import DR2SPExecutorProvider

    captured = {}
    original = SubagentConfig("sp-researcher", "native", internal=True, skills=[])

    class Executor:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def execute(self, prompt):
            return SimpleNamespace(
                status="completed",
                result='{"summary":"done","artifact_type":"research_observation","artifact_content":"new evidence","artifact_metadata":{"completion_status":"complete"}}',
                error=None,
                stop_reason=None,
                token_usage_records=[],
            )

    monkeypatch.setattr("deerflow.subagents.get_subagent_config", lambda *a, **kw: original)
    monkeypatch.setattr("deerflow.subagents.SubagentExecutor", Executor)
    extensions = SPExecutionExtensions(configure_subagent=lambda task, cfg: replace(cfg, model_thinking_enabled=host_override))
    provider = DR2SPExecutorProvider(app_config=SimpleNamespace(), parent_model="qwen", runnable_config={}, extensions=extensions)
    provider._tools = []
    task = SPSubagentTask("a", "researcher", "核对新来源", "check", metadata={"model_thinking_enabled": not expected})
    result = provider({}, SimpleNamespace(context={"thread_id": "t", "run_id": "r", "sp_subagent_thinking_enabled": True}, stream_writer=None)).execute(task)
    assert result.is_success and captured["thinking_enabled"] is expected
    assert original.model_thinking_enabled is None
