"""Phase selection preserves native contracts and restores rules with state."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_sp_report_executor_e2e as executor_tests
from test_sp_report_compact_outline import _compact
from test_sp_report_writing import _delegate

from deerflow.skills.prompt_sections import select_prompt_sections
from deerflow.skills.types import SkillCategory
from deerflow.sp.report import hooks
from deerflow.sp.report.phase_prompts import compact_outline_prompt, outline_role_prompt, outline_skill_sections
from deerflow.sp.report.prompts import REPORT_COMPACT_OUTLINE_PROMPT, REPORT_INTEGRATE_REVIEW_PROMPT
from deerflow.subagents.builtins.sp_specialists import OUTLINE_CONFIG
from deerflow.subagents.config import SubagentConfig

SKILLS = Path(__file__).resolve().parents[2] / "skills/public"


@pytest.fixture
def phase_executor():
    yield from executor_tests.real_executor.__wrapped__()


def render(selections, name):
    return select_prompt_sections((SKILLS / name / "SKILL.md").read_text().strip(), selections[name])


def test_initial_plan_keeps_full_tree_evidence_and_budget_contracts():
    packet = {"materials": [{"material_id": "m1"}], "inputs": [{"applied": True}]}
    selected = outline_skill_sections(packet, "plan", {"compact_outline": True})
    outline = render(selected, "report-outline")
    budget = render(selected, "report-word-budget")
    original = (SKILLS / "report-outline/SKILL.md").read_text()
    tree_contract = original.split("## Tree Contract\n", 1)[1].split("## Envelope and body\n", 1)[0]
    assert tree_contract in outline  # full source/evidence/scope rules, verbatim
    for field in ("report_title", "requirements", "nodes", "removed_node_ids", "write_requests", "gap_outcomes", "user_decisions", "feedback_dispositions", "draft_dispositions"):
        assert field in outline
    assert "Complete research-request shape" in outline and '"materials_checked": [{' in outline
    assert "source_id" in outline and "locator.lines" in outline
    assert "## Integration Review" not in outline and "## Judge Returned Results" not in outline
    assert "## User Inputs" not in outline and "## Retained candidate recovery" not in outline
    assert "## Writer" not in budget and "## Optional submission check" not in budget
    assert "## Paragraph plan" not in budget and "## Budget patches" not in budget
    assert "## Compact proposal serialization" not in budget
    assert "body_budget_recommendation" in budget and "target_words" in budget
    prompt = outline_role_prompt(packet, "plan")
    assert "<initial-outline-next-action>" in prompt
    assert "before designing the complete chapter structure" in prompt
    assert "redundant read" in prompt
    assert "The final tree must put" in prompt and "existing tools" not in prompt


def test_rules_return_for_research_user_input_drafts_and_recovery():
    packet = {"tree": {"version": 1}, "gaps": [{"status": "awaiting_outline"}],
              "unapplied_input_ids": ["u2"], "drafts": [{"node_id": "n1"}],
              "feedback": [{"kind": "spec_conflict"}], "rejected_outline": {"kind": "tree_candidate"}}
    selected = outline_skill_sections(packet, "revise", {"compact_outline": True})
    outline = render(selected, "report-outline")
    budget = render(selected, "report-word-budget")
    for section in ("gap_outcomes shape", "Judge Returned Results", "User Inputs", "Retained candidate recovery"):
        assert "## " + section in outline
    assert "every gap currently awaiting_outline" in outline
    assert "another automatic round" in outline and "original candidate unchanged" in outline
    for section in ("Budget patches", "Content revision", "Compact proposal serialization"):
        assert "## " + section in budget
    assert compact_outline_prompt(packet) == REPORT_COMPACT_OUTLINE_PROMPT
    prompt = outline_role_prompt(packet, "revise")
    assert "Initial plan:\n" not in prompt and "One fixed research round" in prompt
    assert "<initial-outline-next-action>" not in prompt


def test_review_does_not_load_planning_or_writer_budget_rules():
    selected = outline_skill_sections({}, "integrate_review", {"compact_outline": True})
    outline = render(selected, "report-outline")
    budget = render(selected, "report-word-budget")
    assert "## Integration Review" in outline and "## Tree Contract" in outline
    assert "## Register Research Requests" not in outline
    assert "## Integration Review" in budget and "## Outline\n" not in budget
    assert "normal evidence/coverage acceptance rules" in budget
    assert "## Writer" not in budget and "## Compact first-pass mode" not in budget


def test_optional_paragraph_contract_is_loaded_when_enabled():
    assert "## Paragraph plan" in render(outline_skill_sections({}, "plan", {"paragraph_plan_required": True}), "report-word-budget")
    assert "<initial-outline-next-action>" not in outline_role_prompt({}, "plan")


def test_selector_preserves_full_text_on_document_drift_and_ignores_fenced_headings():
    text = "preamble\n## Core\n```md\n## fake\n```\nexact evidence\n## Later\nreview"
    assert select_prompt_sections(text, ["Core"]) == text.split("## Later", 1)[0].strip()
    assert select_prompt_sections(text, ["Missing"]) == text
    assert select_prompt_sections(text + "\n## Core\nduplicate", ["Core"]) == text + "\n## Core\nduplicate"
    assert select_prompt_sections(text, None) == text


def test_real_executor_applies_host_selection_only_to_bundled_skills(tmp_path, phase_executor):
    ctx, _, _ = _compact(tmp_path)
    _, task = _delegate(ctx, "phase-plan", "outline", {"report_task": "plan"})
    cfg, tools, _ = hooks.configure_report_subagent(task, state=ctx.state,
        available_tools=[SimpleNamespace(name="read_file")], subagent_config=OUTLINE_CONFIG)
    assert cfg.tool_call_limit == 50 and cfg.finalize_after_tool_calls == 30
    assert cfg.bound_outline_source_correction is False
    executor = phase_executor.SubagentExecutor(config=cfg, tools=tools, parent_model="fake")
    bundled = SimpleNamespace(name="report-outline", category=SkillCategory.PUBLIC,
                              skill_file=SKILLS / "report-outline/SKILL.md")
    custom = SimpleNamespace(name="report-outline", category=SkillCategory.CUSTOM, skill_file=bundled.skill_file)
    messages = asyncio.run(executor._load_skill_messages([bundled, custom]))
    assert "## Integration Review" not in messages[0].content
    assert "## Integration Review" in messages[1].content  # custom overrides retain full text
    assert SubagentConfig(name="plain", description="plain").skill_prompt_sections is None
    review_packet = task.context_refs["report_packet"]
    task.metadata["report_task"] = "integrate_review"
    review, _, _ = hooks.configure_report_subagent(task, state=ctx.state,
        available_tools=[SimpleNamespace(name="read_file")], subagent_config=OUTLINE_CONFIG)
    assert REPORT_INTEGRATE_REVIEW_PROMPT in review.system_prompt
    assert "<compact-report-outline>" not in review.system_prompt
    assert "report_submit_outline" not in review.tools
    assert "Integration Review" in review.skill_prompt_sections["report-outline"]
    assert review_packet is task.context_refs["report_packet"]
