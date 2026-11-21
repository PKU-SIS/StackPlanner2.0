"""Explicit length is report intent even when the question sounds like Q&A."""

import asyncio
import json
from copy import deepcopy
from threading import Event
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.report import hooks, store
from deerflow.sp.report.context import SPReportContextMiddleware
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError

QUESTION = "如何增强自闭症学生课堂参与度？有哪些有效的策略可供选择？"
CONTRACT = dict(min_words=18000, target_words=20000, max_words=22000, compact_outline=True, writer_task_cards=True, outline_budget_selection=True, length_auto_revision=False)


def state_for(tmp_path, value=CONTRACT):
    return dict(thread_data={"outputs_path": str(tmp_path)}, messages=[HumanMessage(content=QUESTION, id="user-1", additional_kwargs={"report_length": deepcopy(value)})])


def runtime(**kwargs):
    return SimpleNamespace(context={"thread_id": "t1", "run_id": "run1", **kwargs})


@pytest.mark.parametrize("asynchronous", [False, True])
def test_explicit_length_schedules_native_report_research_without_central_classification(tmp_path, asynchronous):
    state = state_for(tmp_path)
    before = deepcopy(state)
    middleware = SPReportContextMiddleware()
    result = asyncio.run(middleware.abefore_model(state, runtime())) if asynchronous else middleware.before_model(state, runtime())
    assert result and result["jump_to"] == "tools"
    call = result["messages"][0].tool_calls[0]
    assert call["name"] == "sp_delegate"
    assert call["args"]["target_agent"] == "researcher"
    assert call["args"]["metadata"]["report_action"] == "start"
    assert call["args"]["task"] == QUESTION
    assert state == before  # Native tools own persistence, not this hook.
    assert not store.pointer_path(tmp_path).exists()


@pytest.mark.parametrize("kwargs", [{}, {"report_length": None}, {"report_length": {"enabled": False}}])
def test_ordinary_or_disabled_length_does_not_schedule_any_action(tmp_path, kwargs):
    state = state_for(tmp_path)
    state["messages"] = [HumanMessage(content=QUESTION, additional_kwargs=kwargs)]
    assert SPReportContextMiddleware().before_model(state, runtime()) is None
    assert not store.pointer_path(tmp_path).exists()


def test_latest_visible_user_message_owns_intent_not_old_or_hidden_metadata(tmp_path):
    state = state_for(tmp_path)
    state["messages"].append(HumanMessage(content="谢谢，解释这个术语即可。", id="user-2"))
    state["messages"].append(HumanMessage(content="hidden", additional_kwargs={"report_length": CONTRACT, "hide_from_ui": True}))
    assert SPReportContextMiddleware().before_model(state, runtime()) is None


@pytest.mark.parametrize("invalid", [False, "20000", {"max_words": -1}, {"target_words": 0}, {"outline_budget_selection": True, "target_words": 25000, "compact_outline": True}])
def test_invalid_length_fails_before_any_dispatch_or_report_creation(tmp_path, invalid):
    with pytest.raises(ReportValidationError):
        SPReportContextMiddleware().before_model(state_for(tmp_path, invalid), runtime())
    assert not store.pointer_path(tmp_path).exists()


def test_new_conversation_preserves_one_native_memory_preflight(tmp_path):
    state = state_for(tmp_path)
    state["sp_new_conversation"] = True
    middleware = SPReportContextMiddleware()
    first = middleware.before_model(state, runtime())["messages"][0].tool_calls[0]
    assert first["name"] == "sp_recall_memory"
    assert QUESTION in first["args"]["query"]
    state["sp_task_memory"] = {"entries": [{"action": "recall_memory", "run_id": "run1"}]}
    second = middleware.before_model(state, runtime())["messages"][0].tool_calls[0]
    assert second["name"] == "sp_delegate"


def test_aborted_run_never_bootstraps_research(tmp_path):
    abort = Event()
    abort.set()
    assert SPReportContextMiddleware().before_model(state_for(tmp_path), runtime(__run_abort_event=abort)) is None
    assert not store.pointer_path(tmp_path).exists()


def start_existing(state):
    ctx = HandlerContext(state=state, stack=TaskMemoryStack(), thread_id="t1", run_id="run1")
    hooks.start_report(ctx, SPAction.create(ActionType.REPLAN, reason="fixture", task="start"))
    return ReportLedger.load_for_context(hooks.report_context(state))


def test_active_report_is_preserved_instead_of_restarted(tmp_path):
    state = state_for(tmp_path)
    ledger = start_existing(state)
    before = ledger.paths.ledger.read_bytes()
    assert SPReportContextMiddleware().before_model(state, runtime()) is None
    assert ledger.paths.ledger.read_bytes() == before
    assert len(list((tmp_path / "report").glob("r*"))) == 1


@pytest.mark.parametrize("status", ["delivered", "abandoned"])
def test_same_consumed_user_message_never_restarts_an_ended_report(tmp_path, status):
    state = state_for(tmp_path)
    ledger = start_existing(state)
    ledger.payload["status"] = status
    ledger.save()
    before = ledger.paths.ledger.read_bytes()
    assert SPReportContextMiddleware().before_model(state, runtime()) is None
    assert ledger.paths.ledger.read_bytes() == before
    state["messages"].append(HumanMessage(content=QUESTION, id="user-2", additional_kwargs={"report_length": CONTRACT}))
    assert SPReportContextMiddleware().before_model(state, runtime())["messages"][0].tool_calls[0]["name"] == "sp_delegate"


def test_native_entry_failure_is_not_automatically_retried(tmp_path):
    state = state_for(tmp_path)
    middleware = SPReportContextMiddleware()
    call = middleware.before_model(state, runtime())["messages"][0].tool_calls[0]
    state["sp_last_handler_result"] = {"action_id": f"spact_{call['id']}", "error": "could not create report"}
    with pytest.raises(ReportValidationError, match="entry"):
        middleware.before_model(state, runtime())


def test_corrupt_report_pointer_never_falls_back_or_overwrites_storage(tmp_path):
    state = state_for(tmp_path)
    pointer = store.pointer_path(tmp_path)
    pointer.parent.mkdir(parents=True)
    pointer.write_text("not JSON")
    with pytest.raises(ReportValidationError, match="unavailable ledger"):
        SPReportContextMiddleware().before_model(state, runtime())
    assert pointer.read_text() == "not JSON"


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_graph_routes_qa_question_into_bound_report_before_first_central_turn(tmp_path, monkeypatch, asynchronous):
    import test_sp_report_wiring as wiring
    from test_sp_report_entry import _CapturingExecutor

    import deerflow.agents.lead_agent.agent as lead
    import deerflow.config.paths as paths
    import deerflow.sp.runtime as sp_runtime
    from deerflow.runtime.user_context import get_effective_user_id
    from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus

    monkeypatch.setattr(paths, "_paths", paths.Paths(tmp_path / "native"))
    outputs = paths.get_paths().sandbox_outputs_dir("t1", user_id=get_effective_user_id())
    state = {"thread_data": {"outputs_path": str(outputs)}}
    calls = []

    class Executor:
        def execute(self, task):
            calls.append(task)
            if task.subagent_type == "memory_recaller":
                return SPSubagentResult(status=SPSubagentStatus.COMPLETED, result=json.dumps({"summary": "no prior preferences", "items": []}))
            assert task.metadata["report_pre_research"] is True
            assert task.task == QUESTION
            return _CapturingExecutor(state).execute(task)

    model = wiring._model([AIMessage(content="fixture stops after validating entry")])
    monkeypatch.setattr(lead, "create_chat_model", lambda **kwargs: model)
    monkeypatch.setattr(sp_runtime.DR2SPExecutorProvider, "__call__", lambda self, state, runtime: Executor())
    graph = sp_runtime.make_sp_agent({"configurable": {"model_name": "fake-model"}}, app_config=wiring._app_config())
    request = {"messages": [HumanMessage(content=QUESTION, id="user-1", additional_kwargs={"report_length": CONTRACT})]}
    context = {"thread_id": "t1", "run_id": "native-entry"}
    result = asyncio.run(graph.ainvoke(request, context=context)) if asynchronous else graph.invoke(request, context=context)
    ledger = ReportLedger.load_for_context(hooks.report_context(result))
    assert [task.subagent_type for task in calls] == ["memory_recaller", "researcher"]
    assert len(wiring._CAPTURED_REQUESTS) == 1  # No Central classification turns.
    first_request = wiring._CAPTURED_REQUESTS[0]
    assert any(getattr(message, "name", None) == hooks.REPORT_CONTEXT_MESSAGE_NAME for message in first_request)
    assert ledger.payload["length_contract"]["target_words"] == 20000
    assert ledger.payload["length_contract"]["outline_budget_selection"] is True
    assert len(ledger.payload["materials"]) == 1
    assert "native-entry" in ledger.payload["run_ids"]
