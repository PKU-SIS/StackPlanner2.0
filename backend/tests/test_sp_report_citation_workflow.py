"""Keep report research breadth, handoff, and bounded scheduling aligned."""

import json
from pathlib import Path

from deerflow.sp.report.hooks import CONTEXT_INSTRUCTIONS
from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.report.packets import build_pre_research_packet, build_researcher_packet
from deerflow.sp.report.prompts import REPORT_CENTRAL_PROMPT, REPORT_OUTLINE_PROMPT, REPORT_PRE_RESEARCH_PROMPT, REPORT_RESEARCH_PROMPT
from deerflow.sp.report.store import ReportPaths


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _skill(name: str) -> str:
    root = Path(__file__).resolve().parents[2]
    return _normalized((root / "skills/public" / name / "SKILL.md").read_text(encoding="utf-8"))


def test_pre_research_contract_preserves_breadth_without_turning_counts_into_gates():
    for contract in (_normalized(REPORT_RESEARCH_PROMPT), _skill("report-research")):
        assert "10-20 relevant documents" in contract
        assert "coverage goal, not a minimum" in contract
        assert "uncovered themes" in contract
        assert "budget" in contract
    assert "small orientation pass" not in _normalized(REPORT_RESEARCH_PROMPT)
    assert "the first report delegation must target researcher" in _normalized(REPORT_CENTRAL_PROMPT)
    assert "do not skip directly to Outline" in REPORT_CENTRAL_PROMPT


def test_research_handoff_is_cited_prose_with_saved_material_provenance():
    for contract in (_normalized(REPORT_RESEARCH_PROMPT), _skill("report-research")):
        assert "theme-organized research synthesis" in contract
        assert "canonical [S<n>]" in contract
        assert "material ids" in contract
        assert "artifact_content" in contract
        assert "source URLs" in contract
        assert "each relevant saved document" in contract


def test_outline_checks_node_evidence_and_hands_off_one_supplementary_assignment():
    skill = _skill("report-outline")
    assert "3-5 useful sources" in skill
    assert "existing sources count" in skill
    assert "one Researcher delegation" in skill
    assert "before submitting the initial tree" in skill
    assert "evidence[]" in skill
    for contract in (skill, _normalized(REPORT_OUTLINE_PROMPT), _normalized(REPORT_CENTRAL_PROMPT)):
        assert "source-count shortfall alone" in contract


def test_central_batches_multiple_gap_ids_and_keeps_the_existing_single_attempt_boundary():
    central = _normalized(REPORT_CENTRAL_PROMPT)
    assert "one Researcher delegation" in central
    assert 'metadata.report_gap_ids=["g1","g2",...]' in central
    assert "one assignment per node" in central
    assert "do not automatically dispatch a second round" in central
    assert "one fixed attempt" in _normalized(REPORT_RESEARCH_PROMPT)
    assert "one Researcher delegation" in _normalized(CONTEXT_INSTRUCTIONS)
    assert "report_gap_ids" in CONTEXT_INSTRUCTIONS


def test_one_research_packet_preserves_multiple_nodes_questions_and_original_user_scope(tmp_path):
    request = "Compare A2A task execution and MCP authentication."
    ledger = ReportLedger.create(ReportPaths(tmp_path, "r1"), request_text=request, inputs=[{"text": request}])
    questions = [("n2", "Task lifecycle", "What states and result artifacts does A2A define?"), ("n4", "Authentication", "How does MCP authorize access?")]
    ledger.payload["nodes"] = {node_id: {"node_id": node_id, "title": title, "kind": "writable"} for node_id, title, _ in questions}
    gap_ids = []
    for node_id, _, question in questions:
        gap = ledger.register_gap(
            mode="new",
            question=question,
            constraints={},
            why_insufficient="Existing overview does not describe this mechanism.",
            stop_condition="Return relevant saved evidence and its limits in this attempt.",
            requirement_refs=[{"input_id": "u1", "quote": request}],
            node_ids=[node_id],
            materials_checked=[],
        )
        gap_ids.append(gap["gap_id"])

    packet = build_researcher_packet(ledger, gap_ids, action_id="combined-research")

    assert [gap["gap_id"] for gap in packet["gaps"]] == gap_ids
    assert [gap["question"] for gap in packet["gaps"]] == [question for _, _, question in questions]
    assert [gap["nodes"][0]["node_id"] for gap in packet["gaps"]] == ["n2", "n4"]
    assert [gap["nodes"][0]["title"] for gap in packet["gaps"]] == ["Task lifecycle", "Authentication"]
    assert all(gap["requirement_refs"][0]["input_text"].strip() == request for gap in packet["gaps"])
    assert packet["instructions"] == REPORT_RESEARCH_PROMPT
    pre_research = build_pre_research_packet(ledger, action_id="pre-research")
    assert pre_research["pre_research"] is True
    assert pre_research["gaps"] == []
    assert request in pre_research["request"]["text"]
    assert pre_research["instructions"] == REPORT_PRE_RESEARCH_PROMPT


def test_supplement_uses_cited_summary_and_only_gap_related_material_metadata(tmp_path):
    ledger = ReportLedger.create(ReportPaths(tmp_path, "r1"), request_text="Research", inputs=[{"text": "Research"}])
    summary = tmp_path / "summary.md"
    text = "# Existing findings\nUseful evidence [S1] (m1).\n"
    summary.write_text(text)
    ledger.payload["summaries"] = [{"action_id": "pre", "path": str(summary), "artifact_id": "a-pre"}]
    ledger.payload["materials"] = [{"material_id": f"m{i}", "source_id": f"S{i}", "lines": 1, "preview": "irrelevant repeated body " * 1000} for i in range(1, 259)]
    gap = ledger.register_gap(
        mode="new",
        question="Need specific evidence",
        constraints={},
        why_insufficient="Missing detail",
        stop_condition="One attempt",
        requirement_refs=[{"input_id": "u1", "quote": "Research"}],
        node_ids=[],
        materials_checked=[{"material_id": "m1", "lines": [1, 1]}],
    )
    accepted_tree = tmp_path / "tree.json"
    accepted_tree.write_text(json.dumps({"nodes": [{"node_id": "n1", "evidence": [{"material_id": "m2"}]}]}))
    ledger.payload["tree"] = {"path": str(accepted_tree)}
    ledger.payload["gaps"][0]["node_ids"] = ["n1"]
    packet = build_researcher_packet(ledger, [gap["gap_id"]], action_id="supplement")
    assert packet["summaries"][0]["content"] == text
    assert [m["material_id"] for m in packet["materials"]] == ["m1", "m2"]
    assert "preview" not in packet["materials"][0]
    assert packet["material_catalog_path"]
    assert "preview" in build_pre_research_packet(ledger, action_id="pre")["materials"][0]
