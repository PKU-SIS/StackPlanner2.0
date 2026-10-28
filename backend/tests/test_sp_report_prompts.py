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


def test_central_prompt_uses_revise_after_accepted_tree():
    normalized = " ".join(REPORT_CENTRAL_PROMPT.split())
    assert "`plan` is only for the initial plan before any tree is accepted" in normalized
    assert 'After the first accepted tree, every Outline call must carry `metadata.report_task="revise"`' in normalized
    assert "Do not use `plan` merely because the dispatch is labelled" in normalized


def test_outline_prompt_keeps_examples_and_source_nature_revisable():
    normalized = " ".join(REPORT_OUTLINE_PROMPT.split())
    assert "Do not decide from a connector alone" in normalized
    assert "`requirements` records the user's actual request" in normalized
    assert "must not automatically enter the hard-requirement list" in normalized
    assert "You are responsible" in normalized
    assert "Judge source nature when selecting evidence" in normalized
    assert "AI-generated trend article" in normalized
    assert "Do not add a domain whitelist" in normalized


def test_central_prompt_allows_general_technical_ask_human_without_fake_rounds():
    normalized = " ".join(REPORT_CENTRAL_PROMPT.split())
    assert "**without** `report_gap_ids`" in normalized
    assert "Do not fake two effective rounds" in normalized
    assert "Necessary evidence gaps still use the normal research/gap path" in normalized


def test_research_prompt_distinguishes_searched_from_sufficiency():
    normalized = " ".join(REPORT_RESEARCH_PROMPT.split())
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


def test_outline_prompt_limits_scope_and_action_lists():
    normalized = " ".join(REPORT_OUTLINE_PROMPT.split())
    assert "`requirements` records the user's actual request" in normalized
    assert "must not automatically enter the hard-requirement list" in normalized
    assert "Do not add a classification field" in normalized
    assert "saying it is necessary is not enough" in normalized
    assert "do not substitute a fixed quota" in normalized
    assert "`gap_outcomes` is an action list" in normalized
    assert "every gap currently awaiting_outline" in normalized
    assert "If an Outline delivery is rejected" in normalized
    assert "do not re-register merely to reset rounds" in normalized
    assert "`user_decisions` records only decisions actually expressed by a new user input" in normalized
    assert "optional: true" in normalized
    assert "do not invent `user_decisions` or `continue_research`" in normalized


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
