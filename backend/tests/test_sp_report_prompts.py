"""Report role prompts encode the write and tool boundaries the host enforces."""

import json
from pathlib import Path

from langchain_core.messages import HumanMessage

from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.report import hooks
from deerflow.sp.report import store as report_store
from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.report.prompts import REPORT_CENTRAL_PROMPT, REPORT_OUTLINE_PROMPT, REPORT_RESEARCH_PROMPT, REPORT_WRITER_PROMPT
from deerflow.sp.report.store import ReportPaths
from deerflow.sp.report.tools import build_register_research_request_tool
from deerflow.sp.report.tree import accept_tree


def _skill_text() -> str:
    skill = Path(__file__).resolve().parents[2] / "skills/public/report-outline/SKILL.md"
    return " ".join(skill.read_text(encoding="utf-8").split())


def test_outline_prompt_treats_write_requests_as_formal_scheduling():
    normalized_prompt = " ".join(REPORT_OUTLINE_PROMPT.split())
    normalized_skill = _skill_text()
    assert "write_requests" in normalized_prompt
    assert "exactly one writable node" in normalized_prompt
    assert "not a trial-only queue" in normalized_skill
    assert "Each entry must target exactly" in normalized_skill
    assert "feedback_wanted" in normalized_skill


def test_central_prompt_handles_exhausted_write_queue():
    assert "`writes_pending=[]` means only" in REPORT_CENTRAL_PROMPT
    assert "every writable node in the outline has a current draft" in REPORT_CENTRAL_PROMPT
    assert 'report_task="revise"' in REPORT_CENTRAL_PROMPT
    assert "not `integrate_review`" in REPORT_CENTRAL_PROMPT
    assert "Do not use forced" in REPORT_CENTRAL_PROMPT


def test_central_prompt_uses_revise_after_accepted_tree():
    normalized = " ".join(REPORT_CENTRAL_PROMPT.split())
    assert "`plan` is only for the initial plan before any tree is accepted" in normalized
    assert 'After the first accepted tree, every Outline call must carry `metadata.report_task="revise"`' in normalized
    assert "Do not use `plan` merely because the dispatch is labelled" in normalized


def test_outline_prompt_keeps_examples_and_source_nature_revisable():
    assert "report-outline skill" in REPORT_OUTLINE_PROMPT
    skill = _skill_text()
    assert "## Boundary" in skill
    assert "## Judge Returned Results" in skill
    assert "## User Inputs" in skill


def test_central_prompt_allows_general_technical_ask_human_without_fake_rounds():
    normalized = " ".join(REPORT_CENTRAL_PROMPT.split())
    assert "**without** `report_gap_ids`" in normalized
    assert "Do not fake success, sufficiency or user authorization" in normalized
    assert "use the existing `sp_ask_human` path" in normalized


def test_research_prompt_distinguishes_searched_from_sufficiency():
    normalized = " ".join(REPORT_RESEARCH_PROMPT.split())
    assert "one fixed attempt" in normalized
    assert "Success, partial evidence" in normalized
    assert "do not ask for an automatic second round" in normalized
    assert "`searched` means (a), not (c)" in normalized
    assert "A valid search that finds only" in normalized
    assert "After a forced stop this agent cannot repair the result" in normalized
    assert "searched` records an executed attempt even when evidence is partial" in normalized


def test_central_prompt_keeps_content_design_with_outline():
    normalized = " ".join(REPORT_CENTRAL_PROMPT.split())
    assert "Central does not design report content" in normalized
    assert "lightweight orientation" in normalized
    assert "must faithfully carry the registered question, constraints, and stop condition" in normalized
    assert "not a user requirement or an exclusive source/delivery threshold" in normalized
    assert '"recently released" does not become "determine the exact day' in normalized
    assert "make the field valid" in normalized


def test_outline_prompt_defaults_fixed_research_round():
    normalized = " ".join(REPORT_OUTLINE_PROMPT.split())
    assert "normally register one fixed" in normalized
    assert "supplementary-research target" in normalized
    assert "Skip this only when the user explicitly restricts" in normalized


def test_outline_prompt_limits_scope_and_action_lists():
    skill = _skill_text()
    assert "## Judge Returned Results" in skill
    assert "## User Inputs" in skill
    assert "optional" in skill


def test_outline_skill_example_is_valid_tree_protocol(tmp_path):
    skill = Path(__file__).resolve().parents[2] / "skills/public/report-outline/SKILL.md"
    text = skill.read_text(encoding="utf-8")
    example_text = text.split("Short example with a complete two-node tree", 1)[1].split("```json", 1)[1].split("```", 1)[0]
    example = json.loads(example_text)

    state = {
        "thread_data": {"outputs_path": str(tmp_path)},
        "messages": [HumanMessage(content="请比较 A2A 与 MCP。", id="m1")],
    }
    ctx = HandlerContext(state=state, stack=TaskMemoryStack(), thread_id="t1", run_id="run1")
    hooks.start_report(ctx, SPAction.create(ActionType.REPLAN, reason="start", task="start", metadata={"report_action": "start"}))
    context = hooks.report_context(ctx.state)
    paths = ReportPaths(context.outputs_path, context.report_id)
    ledger = ReportLedger.load_for_context(context)
    for index in range(3):
        ledger.register_gap(
            mode="new",
            question=f"gap {index}",
            constraints={},
            why_insufficient="not saved",
            stop_condition="a source",
            requirement_refs=[{"input_id": "u1", "quote": "A2A"}],
            node_ids=[],
            materials_checked=[],
        )
    material_text = "MCP 负责工具接入。\nA2A 负责代理协作。\n"
    material_path = paths.material_file("S1", report_store.sha256_text(material_text))
    report_store.write_text_atomic(material_path, material_text)
    ledger.payload["materials"].append(
        {
            "material_id": "m6",
            "source_id": "S1",
            "path": str(material_path),
            "virtual_path": paths.virtual(material_path),
            "chars": len(material_text),
            "lines": 2,
            "sha256": report_store.sha256_text(material_text),
        }
    )
    ledger.require_gap("g2")["status"] = "resolved"
    ledger.require_gap("g3")["status"] = "awaiting_outline"
    ledger.save()

    accepted, outcomes, decisions = accept_tree(example, ledger)

    assert len(accepted["nodes"]) == 2
    assert accepted["nodes"][1]["node_id"] == "n2"
    assert accepted["nodes"][0]["needs_richer_research"] is True
    assert accepted["gap_outcomes"][0]["gap_id"] == "g3"
    assert accepted["user_decisions"] == []
    assert [(item["gap_id"], item["status"]) for item in ledger.gaps()] == [
        ("g1", "open"),
        ("g2", "resolved"),
        ("g3", "resolved"),
    ]
    assert outcomes[0]["gap_id"] == "g3"
    assert decisions == []


def test_outline_skill_research_example_links_section_and_child():
    skill = Path(__file__).resolve().parents[2] / "skills/public/report-outline/SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert '"node_ids": ["n6", "n7"]' in text
    assert "_comment" not in text
    normalized_skill = " ".join(text.split())
    assert "section-level depth signal" in normalized_skill
    assert "Do not associate only the child" in normalized_skill
    assert "Each entry must target exactly" in normalized_skill
    assert "`requirements` records the user's actual request" in normalized_skill
    assert "must not automatically enter the hard-requirement list" in normalized_skill
    assert "Do not add a classification field" in normalized_skill
    assert "do not encode a first-draft country list" in normalized_skill
    assert "If a prior Outline delivery was rejected" in normalized_skill
    assert "Judge source nature before using it" in normalized_skill
    assert "never repeat an already-closed gap" in normalized_skill
    assert '"optional": true' in text


def test_outline_prompt_has_directory_grep_and_structured_tool_examples():
    normalized_prompt = " ".join(REPORT_OUTLINE_PROMPT.split())
    assert "grep" in normalized_prompt
    assert "glob" in normalized_prompt
    assert "report_packet" in normalized_prompt


def test_central_prompt_batches_research_and_centralizes_judgment():
    normalized = " ".join(REPORT_CENTRAL_PROMPT.split())
    assert "whole fixed research batch" in normalized
    assert "Do not revise after each individual result" in normalized
    assert "file-delivery/chunked path" in normalized


def test_outline_prompt_and_skill_require_complete_file_delivery_on_retry():
    normalized_prompt = " ".join(REPORT_OUTLINE_PROMPT.split())
    normalized_skill = _skill_text()
    assert "required_file" in normalized_prompt
    assert "complete tree object" in normalized_prompt
    assert "required_file" in normalized_skill
    assert "Never omit an awaiting gap" in normalized_skill
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
