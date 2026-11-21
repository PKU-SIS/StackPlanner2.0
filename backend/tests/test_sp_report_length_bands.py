"""One runtime must apply the selected band's contract to native role prompts."""

import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_sp_report_length import _budgeted_tree, _enabled_context
from test_sp_report_writing import _outline, _writer_action

from deerflow.sp.report import hooks, length
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError
from deerflow.sp.report.packets import build_outline_packet
from deerflow.sp.subagents import SPSubagentTask
from deerflow.subagents.config import SubagentConfig


@pytest.mark.parametrize("band", [10000, 20000, 30000])
def test_selected_prompts_preserve_archived_guidance_except_wrong_20k_label(band):
    from deerflow.sp.report.prompts import compact_outline_prompt, compact_writer_prompt

    root = Path(__file__).parents[2]
    archived = runpy.run_path(str(root / f"deliveries/deepseek-threebands/profiles/{band}/code/backend/packages/harness/deerflow/sp/report/prompts.py"))
    contract = length.contract_for_band(band)
    assert compact_outline_prompt(contract) == archived["REPORT_COMPACT_OUTLINE_PROMPT"]
    expected_writer = archived["REPORT_COMPACT_WRITER_PROMPT"]
    if band == 20000:
        expected_writer = expected_writer.replace("approximately 30000-word variant", "approximately 20000-word variant")
    assert compact_writer_prompt(contract) == expected_writer


@pytest.mark.parametrize("band, expected", [(10000, (3000, 3500, 4000)), (20000, (8000, 9500, 11500)), (30000, (13000, 15000, 16500))])
def test_band_reaches_native_outline_writer_and_budget_gate(tmp_path, band, expected):
    contract = length.contract_for_band(band)
    assert (contract["min_words"], contract["target_words"], contract["max_words"]) == (band * 9 // 10, band, band * 11 // 10)
    assert contract["length_auto_revision"] is False
    ctx = _enabled_context(tmp_path)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    ledger.payload["length_contract"] = contract
    ledger.save()
    packet = build_outline_packet(ledger)
    rec = packet["length"]["body_budget_recommendation"]
    assert (rec["min_words"], rec["default_words"], rec["max_words"]) == expected
    task = SPSubagentTask(
        action_id="band-outline",
        subagent_type="outline",
        task="plan",
        description="plan",
        metadata={"report_role": "outline", "report_id": ledger.paths.report_id},
        context_refs={"report_packet": packet},
    )
    tools = [SimpleNamespace(name=n) for n in ("read_file", "write_file")]
    cfg, _, _ = hooks.configure_report_subagent(task, state=ctx.state, available_tools=tools, subagent_config=SubagentConfig(name="sp-outline", description="plan"))
    assert ("at least 4 distinct arguments" in cfg.system_prompt) is (band == 30000)
    assert ("1-2 concise core arguments" in cfg.system_prompt) is (band != 30000)
    assert "report_submit_outline" in cfg.tools
    tree = _budgeted_tree()
    tree["nodes"][1]["constraints"].update(target_words=expected[1], max_words=expected[2])
    assert _outline(ctx, tree).error is None
    _, writer, _ = _writer_action(ctx, "w1")
    cfg, _, _ = hooks.configure_report_subagent(writer, state=ctx.state, available_tools=tools, subagent_config=SubagentConfig(name="sp-reporter", description="write"))
    assert ("develop every argument already selected" in cfg.system_prompt) is (band != 10000)
    if band != 10000:
        assert f"approximately {band}-word variant" in cfg.system_prompt
    if band == 20000:
        assert "approximately 30000-word variant" not in cfg.system_prompt
    assert writer.context_refs["report_packet"]["length"]["node"]["target_words"] == expected[1]
    assert "report_measure_draft" not in cfg.tools
    assert cfg.max_output_tokens is None


@pytest.mark.parametrize("band", [True, "10000", 10000.0, 25000, None])
def test_invalid_band_is_rejected(band):
    with pytest.raises(ReportValidationError):
        length.contract_for_band(band)


@pytest.mark.parametrize("band", [10000, 20000, 30000])
def test_cli_prepares_band_without_calling_models(tmp_path, monkeypatch, band):
    scripts = Path(__file__).parents[1] / "scripts"
    output = tmp_path / str(band)
    monkeypatch.syspath_prepend(str(scripts))
    monkeypatch.setattr(sys, "argv", ["evaluate_report_full.py", "--output", str(output), "--ids", "3", "--not-before", "2099-01-01T00:00:00+00:00", "--length-band", str(band)])
    runpy.run_path(str(scripts / "evaluate_report_full.py"), run_name="__main__")
    manifest = json.loads((output / "manifest.json").read_text())
    query = json.loads((output / "3/query.json").read_text())
    assert query["report_length"] == manifest["report_length"] == length.contract_for_band(band)
    assert manifest["status"] == "prepared_only"
    assert not (output / "3/checkpoints.sqlite").exists()
    assert not (output / "3/runtime").exists()


def test_cli_rejects_mixed_band_and_manual_contract_before_creating_output(tmp_path, monkeypatch):
    scripts = Path(__file__).parents[1] / "scripts"
    output = tmp_path / "bad"
    monkeypatch.syspath_prepend(str(scripts))
    monkeypatch.setattr(sys, "argv", ["evaluate_report_full.py", "--output", str(output), "--not-before", "2099-01-01T00:00:00+00:00", "--length-band", "10000", "--word-length", "18000", "20000", "22000"])
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(scripts / "evaluate_report_full.py"), run_name="__main__")
    assert error.value.code == 2
    assert not output.exists()
