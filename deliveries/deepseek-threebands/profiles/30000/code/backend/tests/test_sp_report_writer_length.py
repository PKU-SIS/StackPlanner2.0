"""Generation-first length control must not add model calls at completion."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from test_sp_report_length import _budgeted_tree, _enabled_context
from test_sp_report_writing import _outline, _writer_action

from deerflow.sp.report import hooks, store
from deerflow.sp.report.ledger import ReportLedger
from deerflow.subagents.config import SubagentConfig


def _writer(tmp_path):
    from deerflow.sp.report.writer_length import ReportWriterLengthMiddleware

    ctx = _enabled_context(tmp_path)
    assert _outline(ctx, _budgeted_tree()).error is None
    action, task, path = _writer_action(ctx, "w1")
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    binding = dict(outputs_path=str(ledger.paths.outputs_path), report_id=ledger.paths.report_id, node_id="n2", write_key="w1", draft_id=task.metadata["report_draft_id"], action_id=action.action_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("证" * 100)
    return ReportWriterLengthMiddleware(binding=binding), path, ledger, task, ctx


def test_host_records_actual_completion_without_reopening_the_model(tmp_path):
    middleware, path, ledger, _, _ = _writer(tmp_path)
    original = path.read_bytes()
    reply = AIMessage(content='{"summary":"30 words, done"}')
    assert middleware.after_model({"messages": [reply]}, None) is None
    attempt = ReportLedger.load(ledger.paths).payload["write_requests"][0]["attempts"][0]
    measurement = attempt["length_completion_measurement"]
    assert measurement["estimated_words"] == 100
    assert measurement["target_words"] == 30
    assert measurement["over_node_max"] is True
    assert measurement["draft_sha256"] == store.sha256_text(path.read_text())
    assert path.read_bytes() == original
    assert not attempt.get("length_corrections")
    assert not (ledger.paths.directory / "diagnostics" / "writer-length").exists()
    assert middleware.consume_stop_reason() is None


def test_historical_correction_records_survive_observation(tmp_path):
    middleware, _, ledger, _, _ = _writer(tmp_path)
    history = [{"correction": 1, "draft_sha256": "historical"}]
    ledger.payload["write_requests"][0]["attempts"][0]["length_corrections"] = history
    ledger.save()
    assert middleware.after_model({"messages": [AIMessage(content="complete")]}, None) is None
    attempt = ReportLedger.load(ledger.paths).payload["write_requests"][0]["attempts"][0]
    assert attempt["length_corrections"] == history
    assert attempt["length_completion_measurement"]["estimated_words"] == 100


@pytest.mark.parametrize("failure", ["cancel", "output", "guard", "invalid", "tool_calls", "in_flight", "symlink"])
def test_completion_observation_respects_guards_identity_and_paths(tmp_path, failure):
    middleware, path, ledger, _, _ = _writer(tmp_path)
    reply = AIMessage(content="complete")
    if failure == "cancel":
        middleware.aborted = lambda: True
    elif failure == "output":
        reply.response_metadata["finish_reason"] = "length"
    elif failure == "guard":
        reply.content = "[TOKEN BUDGET EXCEEDED]"
    elif failure == "invalid":
        reply.invalid_tool_calls = [{"name": "write_file", "args": "broken", "id": "bad", "error": "invalid"}]
    elif failure == "tool_calls":
        reply.tool_calls = [{"name": "read_file", "args": {"path": str(path)}, "id": "read"}]
    elif failure == "in_flight":
        ledger.payload["write_requests"][0]["status"] = "completed"
        ledger.save()
    elif failure == "symlink":
        outside = tmp_path / "outside"
        outside.write_text("证" * 100)
        path.unlink()
        path.symlink_to(outside)
    assert middleware.after_model({"messages": [reply]}, None) is None
    attempt = ReportLedger.load(ledger.paths).payload["write_requests"][0]["attempts"][0]
    assert "length_completion_measurement" not in attempt


@pytest.mark.asyncio
async def test_async_completion_measurement_is_offloaded(tmp_path, monkeypatch):
    import asyncio

    middleware, _, ledger, _, _ = _writer(tmp_path)
    calls = []
    real = asyncio.to_thread

    async def tracked(function, *args, **kwargs):
        calls.append(function)
        return await real(function, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", tracked)
    assert await middleware.aafter_model({"messages": [AIMessage(content="complete")]}, None) is None
    assert calls
    attempt = ReportLedger.load(ledger.paths).payload["write_requests"][0]["attempts"][0]
    assert attempt["length_completion_measurement"]["estimated_words"] == 100


def test_generation_guidance_and_measurement_are_scoped_to_enabled_writer(tmp_path):
    _, _, ledger, task, ctx = _writer(tmp_path)
    tools = [SimpleNamespace(name=name) for name in ["read_file", "write_file"]]
    config, _, _ = hooks.configure_report_subagent(task, state=ctx.state, available_tools=tools, subagent_config=SubagentConfig(name="sp-reporter", description="write"))
    assert config.report_writer_length["node_id"] == "n2"
    assert config.report_writer_length["action_id"] == task.action_id
    assert "one substantive chapter" in config.system_prompt
    assert "Do not add a node-level rewrite solely for length" in " ".join(config.system_prompt.split())
    assert "return the normal Writer envelope directly" in " ".join(config.system_prompt.split())
    assert SubagentConfig(name="ordinary", description="default").report_writer_length is None
    del ledger.payload["length_contract"]
    ledger.save()
    disabled, _, _ = hooks.configure_report_subagent(task, state=ctx.state, available_tools=tools, subagent_config=SubagentConfig(name="sp-reporter", description="write"))
    assert disabled.report_writer_length is None
    assert "<report-length-control>" not in disabled.system_prompt


def test_native_agent_finishes_overbudget_node_without_extra_model_calls(tmp_path):
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    middleware, path, ledger, _, _ = _writer(tmp_path)
    model = FakeMessagesListChatModel(responses=[AIMessage(content="complete")])
    agent = create_agent(model=model, tools=[], middleware=[middleware])
    result = agent.invoke({"messages": [HumanMessage(content="Finish the assigned draft")]}, config={"recursion_limit": 30})
    assert path.read_text() == "证" * 100
    assert sum(isinstance(message, AIMessage) for message in result["messages"]) == 1
    assert sum(isinstance(message, HumanMessage) for message in result["messages"]) == 1
    attempt = ReportLedger.load(ledger.paths).payload["write_requests"][0]["attempts"][0]
    assert attempt["length_completion_measurement"]["over_node_max"] is True
