"""Host serialization of explicit Outline decisions, preserving native acceptance."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from deerflow.sp.report.draft_choices import serialize_draft_choices
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError


def fixture():
    ledger = SimpleNamespace(payload={"drafts": [{"node_id": "n1", "draft_id": "d1", "is_current": True}, {"node_id": "n2", "draft_id": "d2", "is_current": True}]})
    return ledger, {"nodes": [{"node_id": "n1", "kind": "writable"}, {"node_id": "n2", "kind": "writable"}], "draft_dispositions": []}


def test_serializes_exact_actions_ids_and_reason_without_mutation():
    ledger, tree = fixture()
    before = deepcopy(tree)
    result = serialize_draft_choices(tree, ledger, keep_nodes=["n1"], rewrite_nodes=["n2"], drop_nodes=[], reason="Selected scope changed")
    assert result["draft_dispositions"] == [{"node_id": "n1", "draft_id": "d1", "action": "keep", "reason": "Selected scope changed"}, {"node_id": "n2", "draft_id": "d2", "action": "rewrite", "reason": "Selected scope changed"}]
    assert tree == before


@pytest.mark.parametrize("problem", ["missing", "duplicate", "unknown", "blank_reason", "mixed", "drop_present", "rewrite_removed"])
def test_does_not_guess_ambiguous_or_invalid_decisions(problem):
    ledger, tree = fixture()
    args = dict(keep_nodes=["n1"], rewrite_nodes=["n2"], drop_nodes=[], reason="Change scope")
    if problem == "missing":
        args["rewrite_nodes"] = []
    elif problem == "duplicate":
        args["rewrite_nodes"] = ["n1", "n2"]
    elif problem == "unknown":
        args["rewrite_nodes"] = ["n8"]
    elif problem == "blank_reason":
        args["reason"] = " "
    elif problem == "mixed":
        tree["draft_dispositions"] = [{"draft_id": "d2", "action": "keep"}]
    elif problem == "drop_present":
        args.update(rewrite_nodes=[], drop_nodes=["n2"])
    else:
        tree["nodes"] = tree["nodes"][:1]
    with pytest.raises(ReportValidationError):
        serialize_draft_choices(tree, ledger, **args)


def test_drop_only_for_explicitly_removed_node():
    ledger, tree = fixture()
    tree["nodes"] = tree["nodes"][:1]
    result = serialize_draft_choices(tree, ledger, keep_nodes=["n1"], rewrite_nodes=[], drop_nodes=["n2"], reason="Optional topic removed")
    assert result["draft_dispositions"][1]["action"] == "drop"


def test_compact_revision_cannot_bypass_explicit_choices_but_legacy_is_unchanged():
    from deerflow.sp.report.submit_outline import _apply_node_choices

    ledger, tree = fixture()
    assert _apply_node_choices(tree, ledger, None, None, None, None) is tree
    ledger.payload["length_contract"] = {"compact_outline": True}
    with pytest.raises(ReportValidationError, match="requires explicit"):
        _apply_node_choices(tree, ledger, None, None, None, None)


def test_compact_outline_exposes_host_serialization_and_disallows_raw_file_writes(tmp_path):
    from test_sp_report_compact_outline import _compact
    from test_sp_report_writing import _delegate

    from deerflow.sp.report import hooks
    from deerflow.subagents.config import SubagentConfig

    ctx, _, _ = _compact(tmp_path)
    _, task = _delegate(ctx, "outline", "outline")
    cfg, tools, _ = hooks.configure_report_subagent(task, state=ctx.state, available_tools=[SimpleNamespace(name=n) for n in ("read_file", "write_file")], subagent_config=SubagentConfig(name="sp-outline", description="plan"))
    assert "write_file" not in cfg.tools and "write_file" in cfg.disallowed_tools
    assert "write_file" not in cfg.finalize_keep_tools
    submit = next(t for t in tools if t.name == "report_submit_outline")
    assert {"keep_nodes", "rewrite_nodes", "drop_nodes", "decision_reason"} <= set(submit.args)


def test_host_generated_disposition_passes_native_writer_acceptance(tmp_path):
    from test_sp_report_writing import _accept_writer, _context, _outline, _tree, _writer_action, _writer_result

    from deerflow.sp.report import hooks, store, writing
    from deerflow.sp.report.submit_outline import build_submit_outline_tool

    ctx = _context(tmp_path)
    assert not _outline(ctx, _tree(write_requests=[{"key": "w", "node_ids": ["n2"], "feedback_wanted": False}])).error
    action, task, path = _writer_action(ctx, "w")
    store.write_text_atomic(path, "Old supported body.\n")
    assert not _accept_writer(ctx, action, task, _writer_result(None, task)).error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    delivery = ledger.paths.directory / "outline" / "choices.json"
    tool = build_submit_outline_tool(paths=ledger.paths, delivery_path=delivery)
    proposal = _tree(question="Updated supported scope", write_requests=[])
    response = json.loads(tool.func(tree=proposal, keep_nodes=[], rewrite_nodes=["n2"], drop_nodes=[], decision_reason="Scope updated"))
    assert response["validation_ok"]
    staged = json.loads(delivery.read_text())
    assert staged["draft_dispositions"][0]["action"] == "rewrite"
    accepted = _outline(ctx, staged, action_id="new-outline")
    assert not accepted.error
    assert writing.current_draft(ReportLedger.load(ledger.paths), "n2") is None
