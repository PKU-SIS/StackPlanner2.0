"""Custom image tools must reach the actual model, not only create PNGs."""

import base64
import hashlib
from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage

from deerflow.agents.middlewares.view_image_middleware import ViewImageMiddleware
from deerflow.sp.extensions import SPExecutionExtensions


def state():
    return {
        "messages": [AIMessage(content="", tool_calls=[{"name": "view_image_pair", "args": {}, "id": "c", "type": "tool_call"}]), ToolMessage("two original sources", tool_call_id="c")],
        "viewed_images": {"a.png": {"base64": base64.b64encode(b"left").decode(), "mime_type": "image/png"}, "b.png": {"base64": base64.b64encode(b"right").decode(), "mime_type": "image/png"}},
    }


def test_native_image_recognizer_default_stays_single_image():
    assert ViewImageMiddleware().before_model(state(), None) is None
    assert SPExecutionExtensions().image_tool_names is None


def test_registered_pair_injects_both_images_and_actual_request_receipt(tmp_path):
    from app.experiments.collusion_audit.tools import AuditTools
    from app.experiments.collusion_audit.vision import AuditImageRequestMiddleware
    from app.experiments.tender_audit.observability import AuditTrace

    s = state()
    injected = ViewImageMiddleware(tool_names=("view_image", "view_image_pair")).before_model(s, None)
    assert injected is not None
    trace = AuditTrace(tmp_path / "trace")
    adapter = AuditTools("M06", tmp_path, tmp_path / "outputs", tmp_path / "cache", trace, rule_level_review=True, quality_controls=True)
    for d, data in [("d001", b"left"), ("d002", b"right")]:
        adapter.register_rendered_image({"document_id": d, "physical_page": 1, "image_name": "/A", "display_png_sha256": hashlib.sha256(data).hexdigest()})

    class Request:
        messages = [*s["messages"], *injected["messages"]]

        def override(self, **kw):
            return SimpleNamespace(**kw)

    captured = []
    AuditImageRequestMiddleware(trace, adapter=adapter).wrap_model_call(Request(), lambda r: captured.extend(r.messages))
    urls = [b["image_url"]["url"] for m in captured if isinstance(m.content, list) for b in m.content if b.get("type") == "image_url"]
    assert len(urls) == 2
    assert adapter.submitted_images == {("d001", 1, "/A"), ("d002", 1, "/A")}


def _actual_executor_probe(tmp_path, monkeypatch):
    import asyncio
    from typing import Annotated

    from langchain.tools import InjectedToolCallId
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import HumanMessage
    from langchain_core.tools import tool
    from langgraph.types import Command
    from pydantic import PrivateAttr

    from app.experiments.collusion_audit.tools import AuditTools
    from app.experiments.collusion_audit.vision import AuditImageRequestMiddleware
    from app.experiments.tender_audit.observability import AuditTrace
    from deerflow.subagents.executor import SubagentExecutor

    class Model(FakeMessagesListChatModel):
        _seen = PrivateAttr(default_factory=list)

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self._seen.append(messages)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    @tool
    def view_image_pair(tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
        """Display two test originals."""
        return Command(update={"viewed_images": state()["viewed_images"], "messages": [ToolMessage("two real test sources", tool_call_id=tool_call_id)]})

    model = Model(responses=[AIMessage(content="", tool_calls=[{"name": "view_image_pair", "args": {}, "id": "pair", "type": "tool_call"}]), AIMessage(content="complete")])
    import importlib

    executor_module = importlib.import_module("deerflow.subagents.executor")
    monkeypatch.setattr(executor_module, "create_chat_model", lambda **kw: model)
    monkeypatch.setattr("deerflow.agents.middlewares.tool_error_handling_middleware.build_subagent_runtime_middlewares", lambda **kw: [ViewImageMiddleware()])
    trace = AuditTrace(tmp_path / "trace")
    adapter = AuditTools("M06", tmp_path, tmp_path / "outputs", tmp_path / "cache", trace, rule_level_review=True, quality_controls=True)
    for d, data in [("d001", b"left"), ("d002", b"right")]:
        adapter.register_rendered_image({"document_id": d, "physical_page": 1, "image_name": "/A", "display_png_sha256": hashlib.sha256(data).hexdigest()})
    executor = object.__new__(SubagentExecutor)
    executor.app_config = SimpleNamespace()
    executor.model_name = "q"
    executor.max_tokens_per_step = 8192
    executor.thinking_enabled = True
    executor.memory_agent_name = None
    executor.config = SimpleNamespace(name="pair-probe", max_turns=160)
    executor.tools = [view_image_pair]
    executor.tool_call_key_fields = {}
    executor.image_tool_names = ("view_image", "view_image_pair")
    executor.extra_middlewares = [AuditImageRequestMiddleware(trace, adapter=adapter)]
    graph = executor._create_agent(tools=[view_image_pair])
    asyncio.run(graph.ainvoke({"messages": [HumanMessage(content="test image tool roundtrip")]}))
    seen = [b for m in model._seen[-1] if isinstance(m.content, list) for b in m.content if isinstance(b, dict) and b.get("type") == "image_url"]
    assert len(seen) == 2
    assert len(adapter.submitted_images) == 2


def test_actual_executor_factory_delivers_pair_images_to_model(tmp_path):
    # conftest deliberately mocks the executor to break circular imports.
    # Bootstrap the real production runtime in a clean process instead.
    import os
    import subprocess
    import sys
    from pathlib import Path

    source = Path(__file__).resolve()
    code = "import deerflow.sp.runtime; import runpy,pytest,sys; from pathlib import Path; m=runpy.run_path(sys.argv[1]); m['_actual_executor_probe'](Path(sys.argv[2]),pytest.MonkeyPatch())"
    result = subprocess.run([sys.executable, "-c", code, str(source), str(tmp_path)], capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert result.returncode == 0, result.stdout + result.stderr


def test_action_boundary_preserves_role_and_only_trusted_requested_image_tools():
    from deerflow.sp.agent_tools import _action_payload

    args = {
        "target_agent": "coder",
        "stage": "perception",
        "task": "读取本地PDF文件，查看图片与表格并返回事实观察",
        "reason": "检查本地PDF原件",
        "expected_output": "source-backed observation",
        "metadata": {"tool_names": ["read_file", "view_image", "view_image_pair", "write_file"], "perception_image_tool_names": ["write_file"]},
    }
    default = _action_payload("sp_delegate", args, tool_call_id="a", precise_report_intent=True)
    assert default["target_agent"] == "perception"
    assert default["metadata"]["tool_names"] == ["read_file"]
    enabled = _action_payload("sp_delegate", args, tool_call_id="b", precise_report_intent=True, perception_image_tool_names=("view_image", "view_image_pair"))
    assert enabled["target_agent"] == default["target_agent"]
    assert enabled["stage"] == default["stage"]
    assert enabled["metadata"]["tool_names"] == ["read_file", "view_image", "view_image_pair"]


def test_trusted_image_scope_flows_through_native_control_executor(tmp_path):
    from deerflow.sp.agent_tools import SPControlActionMiddleware
    from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus

    tasks = []

    class Executor:
        def execute(self, task):
            tasks.append(task)
            return SPSubagentResult(status=SPSubagentStatus.COMPLETED, result="observed", artifact_type="perception_observation", artifact_content="facts", artifact_metadata={"completion_status": "complete"})

    middleware = SPControlActionMiddleware(executor_provider=lambda s, r: Executor(), precise_report_intent=True, perception_image_tool_names=("view_image", "view_image_pair"))
    request = SimpleNamespace(
        tool_call={"name": "sp_delegate", "id": "image-scope", "args": {"target_agent": "coder", "stage": "perception", "task": "读取本地PDF文件并检查图片，返回事实观察", "metadata": {"tool_names": ["read_file", "view_image_pair"]}}},
        state={"thread_data": {"outputs_path": str(tmp_path)}},
        runtime=SimpleNamespace(context={"thread_id": "t", "run_id": "r"}),
    )
    middleware.wrap_tool_call(request, lambda _: None)
    assert tasks[0].subagent_type == "perception"
    assert list(tasks[0].allowed_tools) == ["read_file", "view_image_pair"]
