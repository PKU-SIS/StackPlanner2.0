"""Pre-API preparation is offline, reproducible and excludes credentials/gold."""

import json
from pathlib import Path

import pytest
from report_multiturn_preflight import build_packet, verify_packet
from report_pilot_audit import AuditError, BudgetGuard, PilotUsageAudit, RateCard

ROOT = Path(__file__).resolve().parents[2]


def usage(inp=100, out=20, cache=40):
    result = {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}
    if cache is not None:
        result["input_token_details"] = {"cache_read": cache}
    return result


def test_all_native_roles_and_cache_are_counted_once():
    audit = PilotUsageAudit(RateCard())
    for role in ("central", "git_agent", "outline", "researcher", "writer", "memory_recaller"):
        audit.start(role, turn=1, role=role, model="deepseek-flash")
        audit.end(role, usage(), reported_model="deepseek-flash")
        audit.end(role, usage(), reported_model="deepseek-flash")
    audit.reconcile_model_requests(["central", "git_agent", "outline", "researcher", "writer", "memory_recaller"])
    data = audit.snapshot()
    assert data["billing_complete"]
    assert data["usage_complete"]
    assert data["input_tokens"] == 600 and data["output_tokens"] == 120
    assert data["total_tokens"] == 720  # cache 240 is already included in input
    assert data["cache_read_tokens_observed"] == 240
    assert set(data["by_role"]) == {"central", "git_agent", "outline", "researcher", "writer", "memory_recaller"}
    assert data["logical_model_calls"] == 6


def test_missing_usage_keeps_totals_unknown_and_stops():
    audit = PilotUsageAudit(RateCard())
    audit.start("known", turn=1, role="central", model="deepseek-flash")
    audit.end("known", usage(), reported_model="deepseek-flash")
    audit.start("missing", turn=1, role="writer", model="deepseek-flash")
    audit.end("missing", None, reported_model="deepseek-flash")
    data = audit.snapshot()
    assert not data["usage_complete"] and audit.abort_event.is_set()
    assert data["total_tokens"] is None and data["estimated_cost_cny_upper"] is None
    assert data["observed_total_tokens"] == 120
    assert data["missing_usage_sources"] == ["missing"]


def test_conflicting_duplicate_is_not_silently_deduplicated():
    audit = PilotUsageAudit(RateCard())
    audit.start("x", turn=1, role="git_agent", model="deepseek-flash")
    audit.end("x", usage(), reported_model="deepseek-flash")
    with pytest.raises(AuditError, match="conflict"):
        audit.end("x", usage(out=40), reported_model="deepseek-flash")
    assert audit.abort_event.is_set()


def test_direct_tools_success_empty_and_unknown_are_separate():
    audit = PilotUsageAudit(RateCard())
    audit.tool_attempt("search-1", turn=1, tool="search", status=200, has_result=True)
    audit.tool_attempt("search-2", turn=1, tool="search", status=200, has_result=False)
    audit.tool_attempt("fetch-1", turn=1, tool="fetch", status=404, has_result=False)
    assert audit.snapshot()["direct_tool_cost_cny_observed"] == "0.005"
    audit.tool_attempt("fetch-2", turn=1, tool="fetch", status=None, has_result=None)
    data = audit.snapshot()
    assert data["http_tool_attempts"] == 4 and data["direct_tool_successes"] == 1
    assert data["estimated_cost_cny_upper"] is None and not data["billing_complete"]


def test_budget_reserves_inflight_and_unknown_outcomes():
    guard = BudgetGuard(max_cost_cny="0.02", max_input_tokens=1000, max_output_tokens=1000)
    guard.reserve("a", input_tokens=100, output_tokens=100, cost_cny="0.015")
    with pytest.raises(AuditError, match="budget"):
        guard.reserve("b", input_tokens=100, output_tokens=100, cost_cny="0.01")
    guard.unknown("a")
    assert guard.snapshot()["reserved_cost_cny"] == "0.015"
    assert guard.abort_event.is_set()


def test_budget_success_releases_only_its_own_reservation():
    guard = BudgetGuard(max_cost_cny="1", max_input_tokens=1000, max_output_tokens=1000)
    guard.reserve("a", input_tokens=100, output_tokens=100, cost_cny="0.5")
    guard.reserve("b", input_tokens=100, output_tokens=100, cost_cny="0.4")
    guard.settle("a", input_tokens=40, output_tokens=20, cost_cny="0.1")
    data = guard.snapshot()
    assert data["reserved_cost_cny"] == "0.4" and data["spent_cost_cny"] == "0.1"
    assert data["input_tokens"] == 40 and data["inflight"] == ["b"]


def test_unapproved_budget_cannot_admit_a_request():
    guard = BudgetGuard(max_cost_cny=None, max_input_tokens=1000, max_output_tokens=1000)
    with pytest.raises(AuditError, match="unconfirmed"):
        guard.reserve("a", input_tokens=10, output_tokens=10, cost_cny="0.01")


def test_prepare_does_not_read_env_secrets_or_make_network_calls(tmp_path, monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("No network in preflight")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setenv("LLM_API_KEY", "PRIVATE_SENTINEL_SHOULD_NEVER_APPEAR")
    packet = build_packet(ROOT, tmp_path / "prepared")
    text = json.dumps(packet)
    assert "PRIVATE_SENTINEL" not in text
    assert packet["api_experiments_authorized"] is False
    assert packet["selected_case_id"] == "library-rag"
    assert packet["roles"]["writer"]["model_alias"] == "deepseek-flash"
    assert packet["roles"]["writer"]["thinking"] == {"type": "disabled"}
    assert packet["roles"]["outline"]["thinking"] == {"type": "enabled"}
    assert packet["roles"]["git_agent"]["executor"] == "perception"
    assert "evaluator_checks" not in json.dumps(json.loads((tmp_path / "prepared/agent-input.json").read_text()))
    assert verify_packet(ROOT, tmp_path / "prepared")["provider_calls"] == 0


def test_prepared_packet_refuses_tampering_and_reuse(tmp_path):
    destination = tmp_path / "prepared"
    build_packet(ROOT, destination)
    with pytest.raises(FileExistsError):
        build_packet(ROOT, destination)
    path = destination / "profile.json"
    profile = json.loads(path.read_text())
    profile["model"]["max_tokens"] = 1
    path.write_text(json.dumps(profile))
    with pytest.raises(ValueError, match="checksum"):
        verify_packet(ROOT, destination)


def test_preparation_does_not_modify_frozen_cases(tmp_path):
    suite = ROOT / "docs/research-report/multiturn-evaluation-20261122"
    before = {p.name: p.read_bytes() for p in suite.glob("*.json")}
    build_packet(ROOT, tmp_path / "prepared")
    assert before == {p.name: p.read_bytes() for p in suite.glob("*.json")}


def test_http_inventory_is_required_even_if_all_callbacks_have_usage():
    audit = PilotUsageAudit(RateCard())
    audit.start("observed", turn=1, role="central", model="deepseek-flash")
    audit.end("observed", usage(), reported_model="deepseek-flash")
    assert audit.snapshot()["usage_complete"]
    assert not audit.snapshot()["billing_complete"]
    with pytest.raises(AuditError, match="inventory"):
        audit.reconcile_model_requests(["observed", "unobserved"])
    assert audit.abort_event.is_set()


def test_unknown_reported_model_is_never_priced_as_flash():
    audit = PilotUsageAudit(RateCard())
    audit.start("x", turn=1, role="central", model="deepseek-flash")
    audit.end("x", usage(), reported_model="unapproved-model")
    audit.reconcile_model_requests(["x"])
    data = audit.snapshot()
    assert not data["billing_complete"] and data["estimated_cost_cny_upper"] is None
    from decimal import Decimal

    assert Decimal(data["observed_cost_cny_upper_subtotal"]) == 0


def test_native_callback_bridge_preserves_collector_and_labels_git(tmp_path, monkeypatch):
    import importlib
    import sys

    from _agent_e2e_helpers import FakeToolCallingModel
    from langchain_core.messages import AIMessage
    from report_pilot_audit import observe_native_subagents
    from test_sp_report_executor_e2e import _app_config

    from deerflow.subagents.config import SubagentConfig

    key = "deerflow.subagents.executor"
    previous = sys.modules.pop(key, None)
    module = importlib.import_module(key)
    original_execute = module.SubagentExecutor.execute
    original_collector = module.SubagentTokenCollector
    audit = PilotUsageAudit(RateCard())
    native_records = []
    model = FakeToolCallingModel(responses=[AIMessage(content='{"summary":"offline numeric audit","artifact_content":"fixture"}', usage_metadata=usage(), response_metadata={"model_name": "deepseek-flash"})])
    monkeypatch.setattr(module, "create_chat_model", lambda **kwargs: model)
    try:
        cfg = SubagentConfig(name="sp-perception", description="Offline callback test", tools=["report_inspect_version", "report_prepare_turn", "report_reuse_version"], skills=[])
        executor = module.SubagentExecutor(config=cfg, tools=[], app_config=_app_config(), parent_model="fake-model", thread_id="audit", run_id="audit-run", force_isolated_loop=True, token_usage_observer=native_records.append)
        with observe_native_subagents(module, audit, turn=2):
            result = executor.execute("Return the synthetic audit fixture")
        assert result.status.value == "completed"
        assert module.SubagentExecutor.execute is original_execute
        assert module.SubagentTokenCollector is original_collector
        assert len(native_records) == 1
        assert native_records[0]["source_run_id"] in audit.calls
        audit.reconcile_model_requests([native_records[0]["source_run_id"]])
        assert audit.snapshot()["by_role"] == {"git_agent": 1}
        assert audit.snapshot()["total_tokens"] == native_records[0]["total_tokens"] == 120
        assert cfg.tools == ["report_inspect_version", "report_prepare_turn", "report_reuse_version"]
    finally:
        if previous is not None:
            sys.modules[key] = previous
        else:
            sys.modules.pop(key, None)


def test_reasoning_tokens_are_not_added_twice_to_output():
    audit = PilotUsageAudit(RateCard())
    value = usage()
    value["output_token_details"] = {"reasoning": 15}
    audit.start("x", turn=1, role="outline", model="deepseek-flash")
    audit.end("x", value, reported_model="deepseek-flash")
    assert audit.snapshot()["output_tokens"] == 20


def test_late_settlement_keeps_actual_overrun_and_stops():
    guard = BudgetGuard(max_cost_cny="1", max_input_tokens=1000, max_output_tokens=1000)
    guard.reserve("a", input_tokens=100, output_tokens=100, cost_cny="0.1")
    with pytest.raises(AuditError, match="exceeded reservation"):
        guard.settle("a", input_tokens=110, output_tokens=100, cost_cny="0.2")
    assert guard.snapshot()["spent_cost_cny"] == "0.2"
    assert guard.abort_event.is_set()


def test_readonly_inspect_can_read_saved_material_with_verified_line_identity(tmp_path):
    from test_sp_report_p3 import _finish, _seed, _send_review
    from test_sp_report_turns import followup

    from deerflow.sp.report import store as native
    from deerflow.sp.report.turns import adapter
    from deerflow.sp.report.turns.tools import build_tools

    ctx, ledger = _seed(tmp_path)
    content = "First saved evidence line.\nSecond saved evidence line.\nThird saved line.\n"
    path = ledger.paths.material_file("S1", native.sha256_text(content))
    native.write_text_atomic(path, content)
    ledger.payload["sources"] = [{"source_id": "S1", "url": "https://example.com/evidence", "title": "Saved evidence"}]
    ledger.payload["materials"] = [{"material_id": "m1", "source_id": "S1", "path": str(path), "virtual_path": ledger.paths.virtual(path), "sha256": native.sha256_text(content), "chars": len(content), "lines": 3}]
    ledger.save()
    assert not _send_review(ctx, status="ready", outcome="deliver").error
    assert not _finish(ctx).error
    followup(ctx, text="Only explain saved sources")
    store, message, head = adapter.session(ctx.state, thread_id="t1")
    before = native.read_text(path)
    inspect = build_tools(store, message)[0]
    result = json.loads(inspect.invoke({"version_id": head["turn_id"], "view": "material:m1", "start_line": 2, "end_line": 2}))
    assert result["content"] == "Second saved evidence line."
    assert result["material_id"] == "m1" and result["source_id"] == "S1"
    assert result["lines"] == [2, 2] and result["source"]["url"] == "https://example.com/evidence"
    assert native.read_text(path) == before
    assert len(list((tmp_path / "report").glob("r*"))) == 1
    from deerflow.sp.report.ledger import ReportValidationError

    with pytest.raises(ReportValidationError, match="material|view"):
        inspect.invoke({"version_id": head["turn_id"], "view": "material:../../ledger.json"})


def test_direct_central_readonly_answer_is_archived_without_forcing_git(tmp_path, monkeypatch):
    from langchain_core.messages import AIMessage
    from langgraph.graph.message import add_messages
    from test_sp_report_turns import accepted, followup
    from test_sp_report_wiring import _app_config, _model

    import deerflow.agents.lead_agent.agent as lead
    import deerflow.sp.runtime as runtime
    from deerflow.agents.middlewares.dynamic_context_middleware import DynamicContextMiddleware
    from deerflow.config import paths as paths_module
    from deerflow.runtime.user_context import get_effective_user_id
    from deerflow.sp.report.turns.store import file_manifest

    monkeypatch.setattr(paths_module, "_paths", paths_module.Paths(tmp_path / "runtime"))
    outputs = paths_module.get_paths().sandbox_outputs_dir("t1", user_id=get_effective_user_id())
    outputs.mkdir(parents=True)
    ctx, store = accepted(outputs)
    before = file_manifest(outputs / "report/r1")
    injected = DynamicContextMiddleware(app_config=_app_config(), inject_memory=False).before_agent(ctx.state, None)
    ctx.state["messages"] = add_messages(ctx.state["messages"], injected["messages"])
    followup(ctx, text="只读解释现有资料，不改文件。")
    ctx.state["sp_task_memory"] = ctx.stack.to_dict()
    ctx.state["sp_new_conversation"] = False
    model = _model([AIMessage(content="现有资料只能支持有限说明，本轮未修改报告。")])
    monkeypatch.setattr(lead, "create_chat_model", lambda **kwargs: model)

    class NoExecutor:
        def execute(self, task):
            raise AssertionError("direct readonly answer needs no subagent")

    monkeypatch.setattr(runtime.DR2SPExecutorProvider, "__call__", lambda *args: NoExecutor())
    graph = runtime.make_sp_agent({"configurable": {"model_name": "fake-model"}}, app_config=_app_config())
    graph.invoke(dict(ctx.state), config={"recursion_limit": 150}, context={"thread_id": "t1", "run_id": "run-m2"})
    turn_id = store.index()["requests"]["m2"]
    turn = store.inspect(turn_id)
    assert turn["status"] == "read_only" and turn["response_only"] is True
    assert turn["native_delivery_created"] is False
    assert "未修改报告" in (store.directory(turn_id) / "response.md").read_text()
    store.verify(turn_id)
    assert file_manifest(outputs / "report/r1") == before
