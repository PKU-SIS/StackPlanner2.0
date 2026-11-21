"""Replay the observed false search outage; keep length calibration explicit."""

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from test_sp_report_length import _budgeted_tree, _enabled_context
from test_sp_report_writing import _outline, _writer_action

from deerflow.agents.middlewares.web_search_budget_middleware import WebSearchBudgetMiddleware
from deerflow.sp.report import hooks, store
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError
from deerflow.sp.report.length import budget_summary, candidate_length, normalize_contract, validate_budgets

EXPERIMENT = Path(__file__).resolve().parents[3]


def _saved_search_history():
    fixture = json.loads((EXPERIMENT / "local-storage/replay/search-guard-history.json").read_text())
    messages = [HumanMessage(content="Research the assigned gaps")]
    for row in fixture["history_before_false_exhaustion"]:
        if row["type"] == "ai":
            messages.append(AIMessage(content="", tool_calls=row["tool_calls"]))
        else:
            messages.append(ToolMessage(content=row["content"], tool_call_id=row["tool_call_id"], name="web_search", status=row["status"]))
    return fixture, messages


@pytest.mark.parametrize("asynchronous", [False, True])
def test_saved_parallel_refusals_do_not_open_provider_failure_circuit(asynchronous):
    fixture, messages = _saved_search_history()
    called = []
    middleware = WebSearchBudgetMiddleware()

    def handler(request):
        called.append(request.tool_call["id"])
        return ToolMessage(content='{"results":[{"title":"ok"}]}', tool_call_id=request.tool_call["id"], name="web_search")

    async def async_handler(request):
        return handler(request)

    for call in messages[-1].tool_calls:
        request = SimpleNamespace(tool_call=call, state={"messages": messages})
        result = asyncio.run(middleware.awrap_tool_call(request, async_handler)) if asynchronous else middleware.wrap_tool_call(request, handler)
        assert result.status == "success"
        assert "error" not in json.loads(result.content)
    assert called == fixture["false_exhaustion_call_ids"]


def test_saved_eight_search_batch_still_admits_only_four_calls():
    _, history = _saved_search_history()
    batch = history[1]
    called = []
    results = []

    def handler(request):
        called.append(request.tool_call["id"])
        return ToolMessage(content="results", tool_call_id=request.tool_call["id"], name="web_search")

    for call in batch.tool_calls:
        result = WebSearchBudgetMiddleware().wrap_tool_call(SimpleNamespace(tool_call=call, state={"messages": history[:2]}), handler)
        results.append(result)
    assert len(called) == 4
    assert [json.loads(row.content)["error"] for row in results[4:]] == ["WEB_SEARCH_PARALLEL_LIMIT"] * 4


@pytest.mark.parametrize("code", ["WEB_SEARCH_PARALLEL_LIMIT", "WEB_SEARCH_DUPLICATE_SKIPPED", "WEB_SEARCH_ATTEMPTS_EXHAUSTED", "WEB_SEARCH_BUDGET_EXHAUSTED"])
def test_local_refusal_does_not_reset_two_real_provider_failures(code):
    messages = [HumanMessage(content="Research")]
    for call_id, payload in [
        ("failed-1", {"error": "WEB_SEARCH_UNAVAILABLE"}),
        ("guard", {"error": code, "retryable": False}),
        ("failed-2", {"error": "WEB_SEARCH_UNAVAILABLE"}),
    ]:
        call = {"id": call_id, "name": "web_search", "args": {"query": call_id}, "type": "tool_call"}
        messages += [AIMessage(content="", tool_calls=[call]), ToolMessage(content=json.dumps(payload), name="web_search", tool_call_id=call_id, status="error")]
    call = {"id": "next", "name": "web_search", "args": {"query": "next"}, "type": "tool_call"}
    messages.append(AIMessage(content="", tool_calls=[call]))
    result = WebSearchBudgetMiddleware().wrap_tool_call(SimpleNamespace(tool_call=call, state={"messages": messages}), lambda _: pytest.fail("provider must remain blocked"))
    assert json.loads(result.content)["error"] == "WEB_SEARCH_ATTEMPTS_EXHAUSTED"


def _contract(**overrides):
    return normalize_contract({
        "min_words": 9000, "target_words": 10000, "max_words": 11000,
        "compact_outline": True, "writer_task_cards": True,
        "length_auto_revision": False, "outline_budget_selection": True,
        "outline_body_budget_range": {"min_words": 5800, "default_words": 6200, "max_words": 6600},
        **overrides,
    })


def test_explicit_qwen_range_reaches_native_outline_and_writer_without_reallocation(tmp_path):
    ctx = _enabled_context(tmp_path)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    ledger.payload["length_contract"] = _contract()
    ledger.save()
    initial = budget_summary({}, ledger)
    assert initial["body_target_words"] == 6200
    assert initial["body_budget_recommendation"]["range_source"] == "explicit_contract"
    assert "observed_total_arguments" not in initial["body_budget_recommendation"]
    tree = _budgeted_tree()
    tree["nodes"][1]["constraints"].update(target_words=6300, max_words=6600)
    before = deepcopy(tree)
    assert _outline(ctx, tree).error is None
    _, task, _ = _writer_action(ctx, "w1")
    packet = task.context_refs["report_packet"]
    assert packet["length"]["selected_body_target_words"] == 6300
    assert packet["task_card"]["target_words"] == 6300
    assert packet["task_card"]["max_words"] == 6600
    assert tree == before


@pytest.mark.parametrize("override", [
    None, {},
    {"min_words": True, "default_words": 6200, "max_words": 6600},
    {"min_words": 6500, "default_words": 6200, "max_words": 6600},
    {"min_words": 5800, "default_words": 6200, "max_words": 10000},
    {"min_words": 5800, "default_words": 6200, "max_words": 6600, "model": "qwen"},
])
def test_invalid_custom_ranges_are_rejected(override):
    with pytest.raises(ReportValidationError):
        _contract(outline_body_budget_range=override)


def test_custom_range_requires_existing_outline_selection_mode():
    with pytest.raises(ReportValidationError, match="requires outline_budget_selection"):
        _contract(outline_budget_selection=False)


@pytest.mark.parametrize("words", [5799, 6601])
def test_custom_range_retains_native_aggregate_budget_rejection(words):
    ledger = SimpleNamespace(payload={"length_contract": _contract()})
    tree = {"nodes": [{"node_id": "n", "kind": "writable", "constraints": {"target_words": words, "max_words": words}}]}
    with pytest.raises(ReportValidationError, match="budget"):
        validate_budgets(tree, ledger)


def test_custom_range_respects_current_source_overhead():
    ledger = SimpleNamespace(payload={"length_contract": _contract(), "sources": [{"source_id": "S1", "title": "证" * 4500}]})
    tree = {"nodes": [{"node_id": "n", "evidence": [{"source_ids": ["S1"]}]}]}
    with pytest.raises(ReportValidationError, match="after current references"):
        budget_summary(tree, ledger)


@pytest.mark.parametrize(("words", "within"), [(6200, False), (9000, True), (10000, True), (11000, True), (11001, False)])
def test_qwen_internal_range_never_replaces_final_length_acceptance(tmp_path, words, within):
    path = tmp_path / "candidate.md"
    text = "证" * words
    path.write_text(text)
    candidate = {"candidate_id": "c1", "path": str(path), "sha256": store.sha256_text(text), "sections": [], "source_table": []}
    result = candidate_length(SimpleNamespace(payload={"length_contract": _contract()}), candidate)
    assert result["in_estimated_range"] is within
    assert result["min_words"] == 9000
    assert result["max_words"] == 11000
