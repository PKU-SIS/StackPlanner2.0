"""Positive scope selection: exact source passages, immutable host basis."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_sp_report_compact_outline import _compact
from test_sp_report_length import _small_contract
from test_sp_report_writing import _accept_writer, _add_material, _outline, _writer_action, _writer_result

from deerflow.sp.report import store
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError
from deerflow.sp.report.length import normalize_contract, validate_budgets
from deerflow.sp.report.task_card import build_task_card


def _card(tmp_path):
    ctx, ledger, tree = _compact(tmp_path)
    _add_material(ctx, "Core evidence.\nNecessary qualification.\nOptional long discussion.\n")
    ledger = ReportLedger.load(ledger.paths)
    ledger.payload["length_contract"] = _small_contract(compact_outline=True, writer_task_cards=True)
    ledger.payload["sources"] = [{"source_id": "S1", "title": "Primary source", "url": "https://example.test/source"}]
    ledger.save()
    node = tree["nodes"][1]
    node["argument"] = ["One core answer"]
    node["evidence"] = [{"material_id": "m1", "source_ids": ["S1"], "locator": {"lines": [1, 3]}, "use": "Support the selected claim"}]
    node["constraints"]["writer_tasks"] = [{"claim": "One core answer", "facts": [{"statement": "Supported fact", "material_id": "m1", "lines": [1, 2], "qualification": "Keep the source qualification"}]}]
    node["constraints"]["writer_framework"] = None
    return ctx, ledger, tree, node


def test_task_card_flag_is_opt_in_and_requires_compact_mode():
    for value in (1, "true", None):
        with pytest.raises(ReportValidationError):
            normalize_contract({"compact_outline": True, "writer_task_cards": value})
    with pytest.raises(ReportValidationError, match="requires compact_outline"):
        normalize_contract({"writer_task_cards": True})
    assert "writer_task_cards" not in normalize_contract({})


def test_card_is_derived_from_native_outline_without_extra_model_schema(tmp_path):
    _, ledger, _, node = _card(tmp_path)
    node["constraints"].pop("writer_tasks")
    node["constraints"].pop("scope_exclusions")
    node["argument"] = ["证" * 120, "Second required answer", "Third required answer"]
    before = deepcopy(node)
    p = Path(ledger.material("m1")["path"])
    original = p.read_bytes()
    result = build_task_card(node, ledger)
    assert result["tasks"] == node["argument"]
    assert result["question"] == node["question"]
    assert result["conclusion"] == node["conclusion"]
    assert "Core evidence." not in json.dumps(result)
    assert p.read_bytes() == original and node == before


def test_large_passage_does_not_require_outline_resubmission(tmp_path):
    _, ledger, tree, node = _card(tmp_path)
    _path = Path(ledger.material("m1")["path"])
    content = "Very long source paragraph. " * 1000
    _path.write_text(content)
    ledger.payload["materials"][0]["sha256"] = store.sha256_text(content)
    node["constraints"]["writer_tasks"] = "obsolete schema is ignored"
    validate_budgets(tree, ledger)
    assert content not in json.dumps(build_task_card(node, ledger))


def test_task_card_replaces_expansion_instructions_in_writer_payload_only(tmp_path):
    ctx, ledger, tree, node = _card(tmp_path)
    node["question"] = "Required chapter question"
    node["conclusion"] = "Required chapter answer"
    node["constraints"]["writer_tasks"] = "UNSELECTED_OBSOLETE_TASK_SCHEMA"
    node["constraints"]["period"] = "FY2024"
    assert not _outline(ctx, tree).error
    action, task, path = _writer_action(ctx, "w1")
    packet = task.context_refs["report_packet"]
    encoded = json.dumps(packet)
    assert "task_card" in packet and "node_spec" not in packet["basis"]
    assert "UNSELECTED_" not in encoded
    assert packet["request"]["text"] and packet["requirements"]
    assert packet["evidence"][0]["lines"] == [1, 3]
    assert packet["task_card"]["tasks"] == node["argument"]
    assert packet["task_card"]["constraints"]["period"] == "FY2024"
    current = ReportLedger.load(ledger.paths)
    assert "UNSELECTED_" in json.dumps(task.metadata["report_draft_basis"])
    from deerflow.sp.subagents.dr2_adapter import render_sp_subagent_prompt

    assert "UNSELECTED_" not in render_sp_subagent_prompt(task)
    assert current.payload["length_contract"]["writer_task_cards"] is True
    store.write_text_atomic(path, "Supported fact [S1].\n")
    accepted = _accept_writer(ctx, action, task, _writer_result(None, task))
    assert not accepted.error
    recorded = ReportLedger.load(ledger.paths).payload["drafts"][0]
    assert "UNSELECTED_" in json.dumps(recorded["generated_basis"])


@pytest.mark.parametrize("problem", ["requirement", "material", "locator", "budget"])
def test_simplified_cards_keep_native_requirement_evidence_and_budget_gates(tmp_path, problem):
    ctx, _, tree, node = _card(tmp_path)
    if problem == "requirement":
        node["requirement_ids"] = ["missing_requirement"]
    elif problem == "material":
        node["evidence"][0]["material_id"] = "missing_material"
    elif problem == "locator":
        node["evidence"][0]["locator"]["lines"] = [1, 999]
    else:
        node["constraints"]["max_words"] = 99999
    assert _outline(ctx, tree).error


def test_compact_prompt_has_no_second_card_schema_or_hard_scope_quotas():
    from deerflow.sp.report.prompts import REPORT_COMPACT_OUTLINE_PROMPT

    assert "constraints.writer_tasks=" not in REPORT_COMPACT_OUTLINE_PROMPT
    assert "HARD MAXIMUM" not in REPORT_COMPACT_OUTLINE_PROMPT
    assert "60 estimated words" not in REPORT_COMPACT_OUTLINE_PROMPT
    assert "original user request" in REPORT_COMPACT_OUTLINE_PROMPT
