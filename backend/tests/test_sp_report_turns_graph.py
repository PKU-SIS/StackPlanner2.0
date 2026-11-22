"""Production Central graph probes with fake models and isolated thread storage."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from test_sp_report_turns import accepted, followup
from test_sp_report_wiring import _CAPTURED_REQUESTS, _CAPTURED_TOOLS, CapturingModel, _app_config, _model

from deerflow.config import paths as paths_module
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.sp.report.turns import adapter
from deerflow.sp.report.turns.store import file_manifest
from deerflow.sp.report.turns.tools import build_tools
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus


class ProbeStop(BaseException):
    pass


class FirstRequestProbe(CapturingModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        _CAPTURED_REQUESTS.append(list(messages))
        raise ProbeStop("Stop at first Central request; no provider or executor")


def test_first_central_request_matches_with_optin_without_baseline(tmp_path, monkeypatch):
    import deerflow.agents.lead_agent.agent as lead
    import deerflow.sp.runtime as runtime

    monkeypatch.setattr(paths_module, "_paths", paths_module.Paths(tmp_path / "runtime"))
    from uuid import UUID

    import deerflow.sp.memory.entry as memory_entry

    # Freeze only the unrelated memory identity; compare prompt text and tools exactly.
    monkeypatch.setattr(memory_entry, "uuid4", lambda: UUID(int=1))
    snapshots = []
    for enabled in (False, True):
        _CAPTURED_REQUESTS.clear()
        _CAPTURED_TOOLS.clear()
        probe = FirstRequestProbe(responses=[AIMessage(content="unused")])
        monkeypatch.setattr(lead, "create_chat_model", lambda **kwargs: probe)
        graph = runtime.make_sp_agent({"configurable": {"model_name": "fake-model"}}, app_config=_app_config())
        extra = {"report_dialogue": {"enabled": True, "report_id": "r1"}} if enabled else {}
        with pytest.raises(ProbeStop):
            graph.invoke({"messages": [HumanMessage(content="请撰写高校图书馆知识服务的研究报告。", id="first", additional_kwargs=extra)]}, context={"thread_id": "first-probe", "run_id": "first-probe"})
        assert len(_CAPTURED_REQUESTS) == 1
        snapshots.append(([(m.type, m.name, m.content) for m in _CAPTURED_REQUESTS[0]], list(_CAPTURED_TOOLS)))
    import difflib

    left = "\n".join(str(item[2]) for item in snapshots[0][0])
    right = "\n".join(str(item[2]) for item in snapshots[1][0])
    assert snapshots[0] == snapshots[1], "\n".join(difflib.unified_diff(left.splitlines(), right.splitlines()))
    outputs = paths_module.get_paths().sandbox_outputs_dir("first-probe", user_id=get_effective_user_id())
    assert not (outputs / "report_dialogue").exists()


@pytest.mark.parametrize("mode", ["read_only", "exact_reuse"])
def test_central_selects_git_then_finishes_archive_without_writers(tmp_path, monkeypatch, mode):
    import deerflow.agents.lead_agent.agent as lead
    import deerflow.sp.runtime as runtime

    monkeypatch.setattr(paths_module, "_paths", paths_module.Paths(tmp_path / "runtime"))
    outputs = paths_module.get_paths().sandbox_outputs_dir("t1", user_id=get_effective_user_id())
    outputs.mkdir(parents=True)
    ctx, _ = accepted(outputs)
    before = file_manifest(outputs / "report/r1")
    from langgraph.graph.message import add_messages

    from deerflow.agents.middlewares.dynamic_context_middleware import DynamicContextMiddleware

    # Reproduce the checkpoint shape from the first native run, including its
    # persisted date reminder and normalized user identity, before appending t2.
    injected = DynamicContextMiddleware(app_config=_app_config(), inject_memory=False).before_agent(ctx.state, None)
    ctx.state["messages"] = add_messages(ctx.state["messages"], injected["messages"])
    followup(ctx, text="只读查看或复用已经接受的报告")
    ctx.state["sp_task_memory"] = ctx.stack.to_dict()
    ctx.state["sp_new_conversation"] = False
    calls = []
    session_probes = []
    original_session = adapter.session

    def capture_session(state, *, thread_id):
        value = original_session(state, thread_id=thread_id)
        if value is None:
            session_probes.append({"thread_id": thread_id, "outputs": state.get("thread_data"), "humans": [(m.id, m.name, m.additional_kwargs) for m in state.get("messages", []) if isinstance(m, HumanMessage)]})
        return value

    monkeypatch.setattr(adapter, "session", capture_session)

    class ArchiveExecutor:
        def __init__(self, state):
            self.state = state

        def execute(self, task):
            calls.append(task.subagent_type)
            assert task.metadata["report_role"] == "git_agent"
            store, message, head = adapter.session(self.state, thread_id="t1")
            build_tools(store, message)[2].invoke({"version_id": head["turn_id"], "mode": mode, "response": "Archived source answer"})
            return SPSubagentResult(status=SPSubagentStatus.COMPLETED, result="Stored handoff")

    model = _model(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "sp_delegate",
                        "args": {"target_agent": "perception", "task": "Inspect and archive the current report request", "reason": "Report follow-up", "stage": "perception", "metadata": {"report_profile": "git_agent"}},
                        "id": "git",
                    }
                ],
            ),
            AIMessage(content="", tool_calls=[{"name": "sp_finish", "args": {"summary": "Return archived answer"}, "id": "finish"}]),
        ]
    )
    monkeypatch.setattr(lead, "create_chat_model", lambda **kwargs: model)
    monkeypatch.setattr(runtime.DR2SPExecutorProvider, "__call__", lambda self, state, runtime: ArchiveExecutor(state))
    graph = runtime.make_sp_agent({"configurable": {"model_name": "fake-model"}}, app_config=_app_config())
    from langgraph.errors import GraphRecursionError

    result = None
    try:
        for state in graph.stream(dict(ctx.state), config={"recursion_limit": 150}, context={"thread_id": "t1", "run_id": "run-m2"}, stream_mode="values"):
            result = state
    except GraphRecursionError:
        tools = [(m.name, str(m.content)[:700]) for m in result["messages"] if m.type == "tool"]
        pytest.fail(f"Central loop did not finish; executor calls={calls}; model calls={len(_CAPTURED_REQUESTS)}; tool observations={tools[:8]}; unresolved sessions={session_probes[:3]}")
    assert calls == ["perception"]
    assert len(_CAPTURED_REQUESTS) == 2
    assert any("report-dialogue-routing" in str(m.content) for m in _CAPTURED_REQUESTS[0])
    assert "Archived source answer" in str(result["messages"][-1].content)
    assert file_manifest(outputs / "report/r1") == before
