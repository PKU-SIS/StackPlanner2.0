"""Report role prompts encode the write and tool boundaries the host enforces."""

from deerflow.sp.report.prompts import REPORT_OUTLINE_PROMPT, REPORT_WRITER_PROMPT


def test_outline_prompt_has_directory_grep_and_structured_tool_examples():
    assert "grep` takes a **directory**" in REPORT_OUTLINE_PROMPT
    assert "must be a JSON object" in REPORT_OUTLINE_PROMPT
    assert '{"node_id": "n2", "type": "supports"}' in REPORT_OUTLINE_PROMPT
    assert "declared status is `ready`" in REPORT_OUTLINE_PROMPT


def test_writer_prompt_uses_host_node_level_for_internal_headings():
    assert "node.level" in REPORT_WRITER_PROMPT
    assert "strictly below that level" in REPORT_WRITER_PROMPT
