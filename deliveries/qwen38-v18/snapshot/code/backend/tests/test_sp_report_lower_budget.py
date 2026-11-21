"""Reduced generation guidance and bounded native Outline repair."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_sp_report_acceptance_flows import _FixedExecutor, _invoke, _ledger, _NoCentralModel, _state_without_messages
from test_sp_report_length import _budgeted_tree, _enabled_context, _small_contract
from test_sp_report_writing import _outline, _writer_action

from deerflow.sp.report import hooks
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError
from deerflow.sp.report.length import normalize_contract
from deerflow.subagents.config import SubagentConfig


@pytest.mark.parametrize("value", [0, 101, True, 0.4, "40"])
def test_generation_percent_rejects_invalid_values(value):
    with pytest.raises(ReportValidationError, match="writer_target_percent"):
        normalize_contract({"writer_target_percent": value})


@pytest.mark.parametrize("percent,expected", [(40, (12, 20)), (1, (1, 1))])
def test_lower_generation_packet_preserves_accepted_allocations(tmp_path, percent, expected):
    ctx = _enabled_context(tmp_path)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    ledger.payload["length_contract"] = _small_contract(writer_target_percent=percent)
    ledger.save()
    assert _outline(ctx, _budgeted_tree()).error is None
    _, task, _ = _writer_action(ctx, "w1")
    length = task.context_refs["report_packet"]["length"]
    assert length["node"] == {"target_words": 30, "max_words": 50}
    assert (length["generation"]["target_words"], length["generation"]["max_words"]) == expected
    assert length["generation"]["percent"] == percent
    assert length["contract"]["min_words"] == 5
    assert length["contract"]["max_words"] == 100
    tools = [SimpleNamespace(name=n) for n in ("read_file", "write_file")]
    config, available, _ = hooks.configure_report_subagent(task, state=ctx.state, available_tools=tools, subagent_config=SubagentConfig(name="sp-reporter", description="write"))
    assert "report_measure_draft" not in config.tools
    assert "report_measure_draft" not in [tool.name for tool in available]
    assert config.report_writer_length is not None
    assert config.max_output_tokens is None  # Do not truncate complete drafts.
    assert "length.generation" in config.system_prompt


def test_default_writer_packet_keeps_existing_targets(tmp_path):
    ctx = _enabled_context(tmp_path)
    assert _outline(ctx, _budgeted_tree()).error is None
    _, task, _ = _writer_action(ctx, "w1")
    assert "generation" not in task.context_refs["report_packet"]["length"]


def test_runner_prepares_lower_target_without_api_calls(tmp_path):
    root = Path(__file__).resolve().parents[2]
    destination = tmp_path / "prepared-lower"
    result = subprocess.run(
        [
            sys.executable,
            str(root / "backend/scripts/evaluate_report_full.py"),
            "--ids",
            "31",
            "--output",
            str(destination),
            "--not-before",
            "2026-11-11T00:00:00+00:00",
            "--word-length",
            "10000",
            "12000",
            "15000",
            "--writer-target-percent",
            "40",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    contract = json.loads((destination / "31/query.json").read_text())["report_length"]
    assert contract["writer_target_percent"] == 40
    assert (contract["min_words"], contract["target_words"], contract["max_words"]) == (10000, 12000, 15000)
    assert "no models or paid APIs" in result.stdout
    assert not (destination / "31/metrics.json").exists()


class _MalformedRevisionExecutor(_FixedExecutor):
    def __init__(self, *, persistent):
        super().__init__(["issues", "ready"])
        self.persistent = persistent

    def execute(self, task):
        result = super().execute(task)
        if task.metadata.get("report_task") != "revise":
            return result
        tree = json.loads(result.artifact_content)
        if self.persistent or len(self.revise_action_ids) == 1:
            tree["draft_dispositions"][0].pop("action")
        result.artifact_content = json.dumps(tree, ensure_ascii=False)
        result.result = result.artifact_content
        # Recovery receives a copy of the rejected file. The scoped Outline
        # corrects that same staged tree, without overwriting the old attempt.
        path = Path(task.metadata["outline_delivery_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.artifact_content)
        return result


def test_native_revision_repair_uses_fresh_path_and_delivers(monkeypatch):
    state, _ = _state_without_messages()
    executor = _MalformedRevisionExecutor(persistent=False)
    result = _invoke(monkeypatch, executor=executor, model=_NoCentralModel(), state=state)
    ledger = _ledger(result)
    assert ledger.payload["delivered"] is not None
    assert len(executor.revise_action_ids) == 2
    assert len(set(executor.revise_action_ids)) == 2
    assert ledger.payload["acceptance"]["technical_failure_count"] == 1
    assert ledger.payload["acceptance"]["content_revision_used"] is True
    assert ledger.payload["acceptance"]["review_count"] == 2


def test_persistent_native_revision_failure_stops_after_one_repair(monkeypatch):
    state, _ = _state_without_messages()
    executor = _MalformedRevisionExecutor(persistent=True)
    result = _invoke(monkeypatch, executor=executor, model=_NoCentralModel(), state=state)
    ledger = _ledger(result)
    assert ledger.payload["delivered"] is None
    assert ledger.payload["acceptance"]["status"] == "blocked_user"
    assert ledger.payload["acceptance"]["content_revision_used"] is False
    assert len(executor.revise_action_ids) == 2
    assert "budget exhausted" not in str(result.get("sp_last_handler_result"))
    questions = [call["args"]["question"] for message in result["messages"] for call in getattr(message, "tool_calls", []) if call["name"] == "sp_ask_human"]
    assert questions and "技术" in questions[-1]
