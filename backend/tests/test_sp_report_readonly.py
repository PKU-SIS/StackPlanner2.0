import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from test_sp_report_p3 import _finish, _seed, _send_review
from test_sp_report_turns import accepted, followup

from deerflow.sp.actions.handlers.replan import ReplanHandler
from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.report import store as native
from deerflow.sp.report.turns import adapter
from deerflow.sp.report.turns.store import file_manifest


@pytest.mark.parametrize("long_line", [False, True])
def test_compact_provenance_uses_saved_locator_not_material_prefix(tmp_path, long_line):
    ctx, ledger = _seed(tmp_path, body="Report conclusion [S1].\n", with_sources=True, node_evidence=[{"material_id": "m1", "source_ids": ["S1"], "locator": {"lines": [35, 35]}, "use": "Report interpretation, not a source quote"}])
    material = ledger.payload["materials"][0]
    passage = ("long introduction " * 300 if long_line else "") + "Actual supporting passage at saved locator."
    text = "\n".join(["Unrelated introduction"] * 34 + [passage] + ["Other text"] * 112) + "\n"
    Path(material["path"]).write_text(text)
    material.update(sha256=native.sha256_text(text), chars=len(text), lines=147)
    for item in ledger.payload["materials"]:
        data = native.read_text(Path(item["path"]))
        path = ledger.paths.material_file(item["source_id"], item["sha256"])
        native.write_text_atomic(path, data)
        item.update(path=str(path), virtual_path=ledger.paths.virtual(path))
    ledger.save()
    assert not _send_review(ctx, status="ready", outcome="deliver").error
    assert not _finish(ctx).error
    followup(ctx, text="Explain the source, without changing the report")
    before = file_manifest(tmp_path / "report/r1")
    action = SPAction.create(ActionType.REPLAN, task="Read evidence", reason="readonly", metadata={"report_action": "inspect_version", "view": "provenance:n2"})
    result = ReplanHandler().handle(action, ctx)
    assert not result.error
    payload = result.tool_observation
    assert payload["sections"][0]["title"] == "指标"
    item = payload["sections"][0]["evidence"][0]
    assert item["saved_locator"] == {"lines": [35, 35]}
    assert item["saved_line_range"] == [1, 147]
    assert item["returned_line_range"] == [35, 35]
    assert item["saved_file_available"] is True
    assert payload["material_boundaries"][0]["saved_total_lines"] == 147
    assert payload["material_boundaries"][0]["returned_line_ranges"] == [[35, 35]]
    assert "未展示不等于未保存" in item["reading_boundary"]
    assert "Actual supporting passage" in item["content"]
    assert item["source"]["source_id"] == "S1"
    if long_line:
        assert item["truncated"] and "[Middle omitted]" in item["content"]
        assert len(item["numbered_excerpt_char_ranges"]) == 2
    assert "Report interpretation" not in item["content"]
    assert item["report_use"].startswith("Report interpretation")
    assert len(json.dumps(payload, ensure_ascii=False)) < 10000
    assert file_manifest(tmp_path / "report/r1") == before


@pytest.mark.parametrize("finish_reason,body", [("length", ""), ("stop", ""), ("length", "Partial, incomplete answer")])
def test_empty_or_truncated_answer_is_failed_with_notice_and_unchanged_head(tmp_path, finish_reason, body):
    ctx, store = accepted(tmp_path)
    followup(ctx, text="Read only")
    store, _, head = adapter.session(ctx.state, thread_id="t1")
    before = file_manifest(tmp_path / "report/r1")
    ctx.state["messages"].append(AIMessage(id="answer", content=body, response_metadata={"finish_reason": finish_reason}))
    runtime = SimpleNamespace(context={"thread_id": "t1", "run_id": "run-m2"})
    update = adapter.finalize_run(ctx.state, runtime)
    turn = store.inspect(store.index()["requests"]["m2"])
    assert turn["status"] == "failed"
    assert turn["diagnostic"]["phase"] == "dialogue_completion"
    assert turn["diagnostic"]["finish_reason"] == finish_reason
    assert store.index()["head"] == head["turn_id"]
    assert update["messages"][0].additional_kwargs["deerflow_error_fallback"]
    assert "未能完成" in update["messages"][0].content
    assert (store.directory(turn["turn_id"]) / "response.md").exists()
    store.verify(turn["turn_id"])
    ctx.state["messages"][-1] = update["messages"][0]
    assert adapter.finalize_run(ctx.state, runtime) is None
    assert file_manifest(tmp_path / "report/r1") == before


def test_successful_response_still_archived_and_no_baseline_has_no_fallback(tmp_path):
    ctx, store = accepted(tmp_path)
    followup(ctx, text="Read only")
    ctx.state["messages"].append(AIMessage(content="This is a limited answer from saved evidence.", response_metadata={"finish_reason": "stop"}))
    assert adapter.finalize_run(ctx.state, SimpleNamespace(context={"thread_id": "t1"})) is None
    turn = store.inspect(store.index()["requests"]["m2"])
    assert turn["status"] == "read_only"
    assert adapter.finalize_run({"messages": [AIMessage(content="")]}, SimpleNamespace(context={"thread_id": "fresh"})) is None


def test_after_agent_propagates_failure_notice_with_no_memory_stack(tmp_path):
    from deerflow.sp.middlewares.task_memory_middleware import TaskMemoryMiddleware

    ctx, store = accepted(tmp_path)
    followup(ctx, text="Read only")
    ctx.state["messages"].append(AIMessage(content="", id="empty", response_metadata={"finish_reason": "length"}))
    update = TaskMemoryMiddleware().after_agent(ctx.state, SimpleNamespace(context={"thread_id": "t1", "run_id": "run-m2"}))
    assert update["messages"][0].id == "empty"
    assert update["messages"][0].additional_kwargs["deerflow_error_fallback"]
    assert store.inspect(store.index()["requests"]["m2"])["status"] == "failed"


def test_central_graph_empty_terminal_response_returns_notice_not_success(tmp_path, monkeypatch):
    from langgraph.graph.message import add_messages
    from test_sp_report_wiring import _app_config, _model

    import deerflow.agents.lead_agent.agent as lead
    import deerflow.sp.runtime as runtime
    from deerflow.agents.middlewares.dynamic_context_middleware import DynamicContextMiddleware
    from deerflow.config import paths as paths_module
    from deerflow.runtime.user_context import get_effective_user_id

    monkeypatch.setattr(paths_module, "_paths", paths_module.Paths(tmp_path / "runtime"))
    out = paths_module.get_paths().sandbox_outputs_dir("t1", user_id=get_effective_user_id())
    out.mkdir(parents=True)
    ctx, store = accepted(out)
    injected = DynamicContextMiddleware(app_config=_app_config(), inject_memory=False).before_agent(ctx.state, None)
    ctx.state["messages"] = add_messages(ctx.state["messages"], injected["messages"])
    followup(ctx, text="只读解释现有资料")
    ctx.state["sp_task_memory"] = ctx.stack.to_dict()
    ctx.state["sp_new_conversation"] = False
    model = _model([AIMessage(content="", response_metadata={"finish_reason": "length"})])
    monkeypatch.setattr(lead, "create_chat_model", lambda **kwargs: model)
    graph = runtime.make_sp_agent({"configurable": {"model_name": "fake-model"}}, app_config=_app_config())
    state = graph.invoke(dict(ctx.state), config={"recursion_limit": 80}, context={"thread_id": "t1", "run_id": "run-m2"})
    assert "未能完成" in state["messages"][-1].content
    assert store.inspect(store.index()["requests"]["m2"])["status"] == "failed"


def test_report_tool_reply_preserves_data_beyond_short_task_memory(tmp_path, monkeypatch):
    from langgraph.graph.message import add_messages
    from test_sp_report_wiring import _CAPTURED_REQUESTS, _app_config, _model

    import deerflow.agents.lead_agent.agent as lead
    import deerflow.sp.runtime as runtime
    from deerflow.agents.middlewares.dynamic_context_middleware import DynamicContextMiddleware
    from deerflow.config import paths as paths_module
    from deerflow.runtime.user_context import get_effective_user_id

    monkeypatch.setattr(paths_module, "_paths", paths_module.Paths(tmp_path / "runtime"))
    out = paths_module.get_paths().sandbox_outputs_dir("t1", user_id=get_effective_user_id())
    out.mkdir(parents=True)
    ctx, ledger = _seed(out, body="Long existing report passage. " * 150 + "Source conclusion at end.\n")
    assert not _send_review(ctx, status="ready", outcome="deliver").error
    assert not _finish(ctx).error
    injected = DynamicContextMiddleware(app_config=_app_config(), inject_memory=False).before_agent(ctx.state, None)
    ctx.state["messages"] = add_messages(ctx.state["messages"], injected["messages"])
    followup(ctx, text="Read only")
    ctx.state["sp_task_memory"] = ctx.stack.to_dict()
    ctx.state["sp_new_conversation"] = False
    model = _model(
        [
            AIMessage(content="", tool_calls=[{"name": "sp_replan", "args": {"task": "Read provenance", "reason": "readonly", "metadata": {"report_action": "inspect_version", "view": "provenance:n2"}}, "id": "read"}]),
            AIMessage(content="Limited answer from the saved report; source details are missing."),
        ]
    )
    monkeypatch.setattr(lead, "create_chat_model", lambda **kwargs: model)
    graph = runtime.make_sp_agent({"configurable": {"model_name": "fake-model"}}, app_config=_app_config())
    state = graph.invoke(dict(ctx.state), config={"recursion_limit": 100}, context={"thread_id": "t1", "run_id": "run-m2"})
    reply = next(m for m in state["messages"] if getattr(m, "name", None) == "sp_replan")
    payload = json.loads(json.loads(reply.content)["result"])
    assert payload["sections"][0]["content"].endswith("Source conclusion at end.\n")
    assert any("Source conclusion at end." in str(m.content) for request in _CAPTURED_REQUESTS for m in request if getattr(m, "name", None) == "sp_replan")


def test_final_visible_answer_is_kept_separate_from_explicit_readonly_archive(tmp_path):
    ctx, store = accepted(tmp_path)
    followup(ctx, text="Read only")
    store, message, head = adapter.session(ctx.state, thread_id="t1")
    turn = store.reuse(head["turn_id"], message_id="m2", raw_query=message.content, response="Archive answer", mode="read_only")
    before = file_manifest(tmp_path / "report/r1")
    ctx.state["messages"].append(AIMessage(content="Final visible answer with additional caveats."))
    assert adapter.finalize_run(ctx.state, SimpleNamespace(context={"thread_id": "t1"})) is None
    assert (store.directory(turn["turn_id"]) / "response.md").read_text() == "Archive answer"
    assert (store.directory(turn["turn_id"]) / "visible_response.md").read_text() == "Final visible answer with additional caveats."
    assert file_manifest(tmp_path / "report/r1") == before


@pytest.mark.parametrize("view", ["draft:n2", "report"])
def test_body_reads_keep_text_visible_even_with_long_requirement_history(tmp_path, view):
    ctx, store = accepted(tmp_path)
    version = store.freeze("r1", resolved_query="Historical requirement that must not precede a literal quote. " * 1000)
    followup(ctx, text="只读照录正文，不核对资料来源")
    before = file_manifest(tmp_path)
    action = SPAction.create(ActionType.REPLAN, task="Read saved text", reason="literal quote", metadata={"report_action": "inspect_version", "view": view})
    result = ReplanHandler().handle(action, ctx)
    assert not result.error
    payload = result.tool_observation
    assert "Seed body for n2." in payload["content"]
    assert payload["version"]["turn_id"] == version["turn_id"]
    assert len(json.dumps(payload, ensure_ascii=False)) < 2000
    assert "resolved_query" not in payload["version"]
    assert "native_requirement_context" not in payload["version"]
    assert file_manifest(tmp_path) == before


def test_sections_view_only_lists_current_report_sections_without_sources(tmp_path):
    ctx, store = accepted(tmp_path)
    store.freeze("r1", resolved_query="Long prior requirement " * 1000)
    followup(ctx, text="Locate the section to quote")
    before = file_manifest(tmp_path)
    action = SPAction.create(ActionType.REPLAN, task="Find section", reason="readonly", metadata={"report_action": "inspect_version", "view": "sections"})
    result = ReplanHandler().handle(action, ctx)
    assert not result.error
    payload = result.tool_observation
    assert payload["section_catalog"] == [{"node_id": "n2", "title": "指标"}]
    assert "sources" not in payload and "materials" not in payload
    assert len(json.dumps(payload, ensure_ascii=False)) < 1000
    assert file_manifest(tmp_path) == before


@pytest.mark.parametrize("view", ["draft:n2", "report"])
def test_body_reads_honor_line_windows_without_modifying_the_report(tmp_path, view):
    body = "\n".join([f"unchanged line {n}" for n in range(1, 130)] + ["| target row | exact saved cell |", "final line"]) + "\n"
    ctx, ledger = _seed(tmp_path, body=body)
    assert not _send_review(ctx, status="ready", outcome="deliver").error
    assert not _finish(ctx).error
    followup(ctx, text="Quote a late line")
    store, _, head = adapter.session(ctx.state, thread_id="t1")
    before = file_manifest(tmp_path)
    full = ReplanHandler().handle(SPAction.create(ActionType.REPLAN, task="Read body", reason="readonly", metadata={"report_action": "inspect_version", "view": view}), ctx).tool_observation["content"]
    line = next(i for i, text in enumerate(full.splitlines(), 1) if "exact saved cell" in text)
    action = SPAction.create(ActionType.REPLAN, task="Read exact line", reason="readonly", metadata={"report_action": "inspect_version", "view": view, "start_line": line, "end_line": line})
    result = ReplanHandler().handle(action, ctx)
    assert not result.error
    assert result.tool_observation["content"].strip() == "| target row | exact saved cell |"
    assert result.tool_observation["lines"] == [line, line]
    assert result.tool_observation["truncated"] is True
    assert result.tool_observation["last_line_complete"] is True
    assert store.index()["head"] == head["turn_id"]
    assert file_manifest(tmp_path) == before


def test_material_read_is_compact_but_explicit_requirement_read_is_complete(tmp_path):
    ctx, ledger = _seed(tmp_path, body="Saved claim [S1].\n", with_sources=True)
    for item in ledger.payload["materials"]:
        data = native.read_text(Path(item["path"]))
        path = ledger.paths.material_file(item["source_id"], item["sha256"])
        native.write_text_atomic(path, data)
        item.update(path=str(path), virtual_path=ledger.paths.virtual(path))
    ledger.save()
    assert not _send_review(ctx, status="ready", outcome="deliver").error
    assert not _finish(ctx).error
    from deerflow.runtime.user_context import get_effective_user_id
    from deerflow.sp.report.turns.store import ReportTurnStore
    from deerflow.sp.report.turns.tools import build_tools

    store = ReportTurnStore(tmp_path, user_id=get_effective_user_id(), thread_id="t1")
    requirement = "Complete original requirement " * 1000
    version = store.freeze("r1", resolved_query=requirement)
    followup(ctx, text="Read saved source")
    tool = build_tools(store, ctx.state["messages"][-1])[0]
    before = file_manifest(tmp_path)
    material = json.loads(tool.invoke({"version_id": version["turn_id"], "view": "material:m1", "start_line": 1, "end_line": 1}))
    assert "resolved_query" not in material["version"]
    assert len(json.dumps(material, ensure_ascii=False)) < 2000
    assert material["content"]
    original = json.loads(tool.invoke({"version_id": version["turn_id"], "view": "requirements"}))
    assert original["version"]["resolved_query"] == requirement
    assert file_manifest(tmp_path) == before


def test_central_reads_catalog_and_short_body_without_delegating(tmp_path, monkeypatch):
    from langgraph.graph.message import add_messages
    from test_sp_report_wiring import _CAPTURED_REQUESTS, _app_config, _model

    import deerflow.agents.lead_agent.agent as lead
    import deerflow.sp.runtime as runtime
    from deerflow.agents.middlewares.dynamic_context_middleware import DynamicContextMiddleware
    from deerflow.config import paths as paths_module
    from deerflow.runtime.user_context import get_effective_user_id

    monkeypatch.setattr(paths_module, "_paths", paths_module.Paths(tmp_path / "runtime"))
    outputs = paths_module.get_paths().sandbox_outputs_dir("t1", user_id=get_effective_user_id())
    outputs.mkdir(parents=True)
    ctx, store = accepted(outputs)
    store.freeze("r1", resolved_query="Large requirement metadata " * 1500)
    old = file_manifest(outputs / "report/r1")
    injected = DynamicContextMiddleware(app_config=_app_config(), inject_memory=False).before_agent(ctx.state, None)
    ctx.state["messages"] = add_messages(ctx.state["messages"], injected["messages"])
    followup(ctx, text="Quote the saved indicators section without rewriting")
    ctx.state["sp_task_memory"] = ctx.stack.to_dict()
    ctx.state["sp_new_conversation"] = False
    model = _model(
        [
            AIMessage(content="", tool_calls=[{"name": "sp_replan", "args": {"task": "Locate section", "reason": "readonly", "metadata": {"report_action": "inspect_version", "view": "sections"}}, "id": "catalog"}]),
            AIMessage(content="", tool_calls=[{"name": "sp_replan", "args": {"task": "Quote body", "reason": "readonly", "metadata": {"report_action": "inspect_version", "view": "draft:n2", "start_line": 1, "end_line": 1}}, "id": "body"}]),
            AIMessage(content="The saved text is: Seed body for n2."),
        ]
    )
    monkeypatch.setattr(lead, "create_chat_model", lambda **kwargs: model)

    class NoDelegates:
        def execute(self, *args, **kwargs):
            pytest.fail("Literal report reads do not execute a subagent")

    monkeypatch.setattr(runtime.DR2SPExecutorProvider, "__call__", lambda *args, **kwargs: NoDelegates())
    graph = runtime.make_sp_agent({"configurable": {"model_name": "fake-model"}}, app_config=_app_config())
    result = graph.invoke(dict(ctx.state), config={"recursion_limit": 100}, context={"thread_id": "t1", "run_id": "run-m2"})
    messages = [message for request in _CAPTURED_REQUESTS for message in request if getattr(message, "name", None) == "sp_replan"]
    body = next(message for message in messages if "Seed body for n2." in str(message.content))
    assert len(body.content) < 3000
    assert json.loads(json.loads(body.content)["result"])["content"] == "Seed body for n2.\n"
    assert store.inspect(store.index()["requests"]["m2"])["status"] == "read_only"
    assert file_manifest(outputs / "report/r1") == old
    assert not any(getattr(message, "name", None) == "sp_delegate" for message in result["messages"])


def test_source_id_lookup_avoids_full_catalog_and_preserves_snapshot(tmp_path):
    ctx, ledger = _seed(tmp_path)
    ledger.payload["sources"] = [{"source_id": f"S{i}", "title": f"Source {i}", "url": f"https://source.example/document/{i}"} for i in range(1, 301)]
    ledger.save()
    assert not _send_review(ctx, status="ready", outcome="deliver").error
    assert not _finish(ctx).error
    followup(ctx, text="List the requested saved source metadata, without research")
    store, _, head = adapter.session(ctx.state, thread_id="t1")
    before = file_manifest(tmp_path)
    action = SPAction.create(
        ActionType.REPLAN,
        task="Read selected sources",
        reason="readonly source lookup",
        metadata={
            "report_action": "inspect_version",
            "view": "sources",
            "source_ids": ["S299", "S2", "S299", "S999"],
        },
    )
    result = ReplanHandler().handle(action, ctx)
    assert not result.error
    payload = result.tool_observation
    assert [s["source_id"] for s in payload["sources"]] == ["S299", "S2"]
    assert payload["sources"][0]["url"] == "https://source.example/document/299"
    assert payload["unknown_source_ids"] == ["S999"]
    assert payload["total_sources"] == 300
    assert len(json.dumps(payload)) < 2000
    assert "materials" not in payload
    assert store.index()["head"] == head["turn_id"]
    assert file_manifest(tmp_path) == before


def test_source_id_lookup_reports_unreturned_ids_instead_of_failing_the_call(tmp_path):
    ctx, ledger = _seed(tmp_path)
    ledger.payload["sources"] = [{"source_id": f"S{i}", "url": f"https://source.example/{i}"} for i in range(1, 41)]
    ledger.save()
    assert not _send_review(ctx, status="ready", outcome="deliver").error
    assert not _finish(ctx).error
    followup(ctx, text="Read saved source metadata")
    result = ReplanHandler().handle(
        SPAction.create(
            ActionType.REPLAN,
            task="Read sources",
            reason="readonly source lookup",
            metadata={
                "report_action": "inspect_version",
                "view": "sources",
                "source_ids": [f"S{i}" for i in range(1, 41)],
            },
        ),
        ctx,
    )
    assert not result.error
    assert len(result.tool_observation["sources"]) == 32
    assert result.tool_observation["omitted_requested_source_ids"] == [f"S{i}" for i in range(33, 41)]
