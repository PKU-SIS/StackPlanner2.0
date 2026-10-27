"""Report role prompts encode the write and tool boundaries the host enforces."""

from pathlib import Path

from deerflow.sp.report.prompts import REPORT_CENTRAL_PROMPT, REPORT_OUTLINE_PROMPT, REPORT_RESEARCH_PROMPT, REPORT_WRITER_PROMPT
from deerflow.sp.report.store import ReportPaths
from deerflow.sp.report.tools import build_register_research_request_tool


def test_outline_prompt_treats_write_requests_as_formal_scheduling():
    normalized_prompt = " ".join(REPORT_OUTLINE_PROMPT.split())
    assert "not a trial-only queue" in normalized_prompt
    assert "every retained writable node" in normalized_prompt
    assert "feedback_wanted" in normalized_prompt
    assert "never determines whether other nodes continue writing" in normalized_prompt
    assert "Each entry must target exactly one writable node" in normalized_prompt
    assert "multiple entries, each with its own `key`" in normalized_prompt


def test_central_prompt_handles_exhausted_write_queue():
    assert "`writes_pending=[]` means only" in REPORT_CENTRAL_PROMPT
    assert "every writable node in the outline has a current draft" in REPORT_CENTRAL_PROMPT
    assert 'report_task="revise"' in REPORT_CENTRAL_PROMPT
    assert "not `integrate_review`" in REPORT_CENTRAL_PROMPT
    assert "Do not use forced" in REPORT_CENTRAL_PROMPT


def test_outline_skill_research_example_links_section_and_child():
    skill = Path(__file__).resolve().parents[2] / "skills/public/report-outline/SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert '"node_ids": ["n6", "n7"]' in text
    assert "_comment" not in text
    normalized_skill = " ".join(text.split())
    assert "section-level depth signal" in normalized_skill
    assert "Do not associate only the child" in normalized_skill
    assert "Each entry must target exactly" in normalized_skill


def test_outline_prompt_has_directory_grep_and_structured_tool_examples():
    assert "grep` takes a **directory**" in REPORT_OUTLINE_PROMPT
    assert "must be a JSON object" in REPORT_OUTLINE_PROMPT
    assert '{"node_id": "n2", "type": "supports"}' in REPORT_OUTLINE_PROMPT
    normalized_prompt = " ".join(REPORT_OUTLINE_PROMPT.split())
    assert "arrange writing for every retained writable node" in normalized_prompt
    assert "`ready` and has no current valid draft" in normalized_prompt
    assert '{"input_id":"u1","quote":"..."}' in REPORT_OUTLINE_PROMPT
    assert '{"material_id":"m1","lines":[10,32]}' in REPORT_OUTLINE_PROMPT
    assert '"evidence_material_ids":["m1"]' in REPORT_OUTLINE_PROMPT
    assert "needs_richer_research" in REPORT_OUTLINE_PROMPT
    assert "needs_richer_research" in REPORT_RESEARCH_PROMPT


def test_research_request_tool_description_exposes_nested_contract(tmp_path):
    tool = build_register_research_request_tool(
        paths=ReportPaths(tmp_path, "r1"),
        action_id="a-tool",
    )

    assert "requirement_refs" in tool.description
    assert "materials_checked" in tool.description
    assert "input_id" in tool.description
    assert "material_id" in tool.description
    normalized_description = " ".join(tool.description.split())
    assert "exactly one writable node" in normalized_description


def test_central_prompt_keeps_gap_closure_and_recovery_with_outline():
    assert "Central does not decide whether a research gap is resolved" in REPORT_CENTRAL_PROMPT
    assert 'use `report_task="revise"`' in REPORT_CENTRAL_PROMPT
    assert "administrative" in REPORT_CENTRAL_PROMPT
    assert "no tree candidate was produced" in REPORT_CENTRAL_PROMPT


def test_writer_prompt_uses_host_node_level_for_internal_headings():
    assert "node.level" in REPORT_WRITER_PROMPT
    assert "strictly below that level" in REPORT_WRITER_PROMPT
