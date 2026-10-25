"""Report role prompts encode the write and tool boundaries the host enforces."""

from deerflow.sp.report.prompts import REPORT_CENTRAL_PROMPT, REPORT_OUTLINE_PROMPT, REPORT_WRITER_PROMPT
from deerflow.sp.report.store import ReportPaths
from deerflow.sp.report.tools import build_register_research_request_tool


def test_outline_prompt_has_directory_grep_and_structured_tool_examples():
    assert "grep` takes a **directory**" in REPORT_OUTLINE_PROMPT
    assert "must be a JSON object" in REPORT_OUTLINE_PROMPT
    assert '{"node_id": "n2", "type": "supports"}' in REPORT_OUTLINE_PROMPT
    assert "declared status is `ready`" in REPORT_OUTLINE_PROMPT
    assert '{"input_id":"u1","quote":"..."}' in REPORT_OUTLINE_PROMPT
    assert '{"material_id":"m1","lines":[10,32]}' in REPORT_OUTLINE_PROMPT
    assert '"evidence_material_ids":["m1"]' in REPORT_OUTLINE_PROMPT


def test_research_request_tool_description_exposes_nested_contract(tmp_path):
    tool = build_register_research_request_tool(
        paths=ReportPaths(tmp_path, "r1"),
        action_id="a-tool",
    )

    assert "requirement_refs" in tool.description
    assert "materials_checked" in tool.description
    assert "input_id" in tool.description
    assert "material_id" in tool.description


def test_central_prompt_keeps_gap_closure_and_recovery_with_outline():
    assert "Central does not decide whether a research gap is resolved" in REPORT_CENTRAL_PROMPT
    assert 'use `report_task="revise"`' in REPORT_CENTRAL_PROMPT
    assert "administrative" in REPORT_CENTRAL_PROMPT


def test_writer_prompt_uses_host_node_level_for_internal_headings():
    assert "node.level" in REPORT_WRITER_PROMPT
    assert "strictly below that level" in REPORT_WRITER_PROMPT
