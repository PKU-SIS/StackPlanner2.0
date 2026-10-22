"""E-layer: real DelegateHandler + SubagentExecutor + report tools (fake LLM only)."""

from __future__ import annotations

import importlib
import json
import shutil
import sys
from pathlib import Path

import pytest
from langchain.tools import tool
from langchain_core.messages import AIMessage, HumanMessage

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _agent_e2e_helpers import FakeToolCallingModel

import deerflow.config.paths as paths_module
from deerflow.config.app_config import AppConfig, reset_app_config, set_app_config
from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.sandbox.tools import glob_tool, grep_tool, ls_tool, read_file_tool, write_file_tool
from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.handlers.delegate import DelegateHandler
from deerflow.sp.actions.handlers.finish import FinishHandler
from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.artifacts import SPArtifactAdapter
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.report import hooks
from deerflow.sp.report import store as report_store
from deerflow.sp.report.hooks import configure_report_subagent
from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.report.materials import build_fetch_material_tool
from deerflow.sp.subagents import DR2SubagentExecutorAdapter, SPSubagentResult, SPSubagentStatus, SPSubagentTask
from deerflow.subagents import get_subagent_config


@pytest.fixture()
def real_executor():
    """Load the real executor for this test and restore conftest's module mock."""
    key = "deerflow.subagents.executor"
    original = sys.modules.get(key)
    sys.modules.pop(key, None)
    module = importlib.import_module(key)
    try:
        yield module
    finally:
        if original is not None:
            sys.modules[key] = original
        else:
            sys.modules.pop(key, None)


def _app_config(*, supports_thinking: bool = False) -> AppConfig:
    return AppConfig.model_validate(
        {
            "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
            "models": [{"name": "fake-model", "use": "pkg:Cls", "model": "fake-model", "supports_thinking": supports_thinking}],
            "summarization": {"enabled": False},
            "title": {"enabled": False},
            "memory": {"enabled": False},
            "token_usage": {"enabled": False},
            "loop_detection": {"enabled": False},
            "tool_search": {"enabled": False},
        }
    )


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path):
    previous = paths_module._paths
    paths_module._paths = paths_module.Paths(tmp_path / "deer-flow")
    try:
        yield
    finally:
        paths_module._paths = previous


@pytest.fixture(autouse=True)
def _global_app_config():
    set_app_config(_app_config())
    try:
        yield
    finally:
        reset_app_config()


def _state(tmp_path: Path) -> dict:
    thread_id = "t-e2e"
    base = get_paths().thread_dir(thread_id, user_id=get_effective_user_id())
    if base.exists():
        shutil.rmtree(base)
    outputs = get_paths().sandbox_outputs_dir(thread_id, user_id=get_effective_user_id())
    outputs.mkdir(parents=True, exist_ok=True)
    state = {
        "thread_data": {
            "outputs_path": str(outputs),
            "workspace_path": str(get_paths().sandbox_work_dir(thread_id, user_id=get_effective_user_id())),
            "uploads_path": str(get_paths().sandbox_uploads_dir(thread_id, user_id=get_effective_user_id())),
        },
        "messages": [HumanMessage(content="请写一份关于示例公司 FY2024 偿付能力的报告。" * 60, id="m1")],
    }
    ctx = HandlerContext(state=state, stack=TaskMemoryStack(), thread_id=thread_id, run_id="run-e2e")
    hooks.start_report(ctx, SPAction.create(ActionType.REPLAN, reason="start", task="start", metadata={"report_action": "start"}))
    return state


def _executor_factory(state: dict, app_config: AppConfig, available_tools: list, registry_name: str, executor_module, step_observer=None, thinking_enabled=None):
    base_config = get_subagent_config(registry_name, app_config=app_config)
    assert base_config is not None

    def factory(task: SPSubagentTask):
        report_config, tools, role = configure_report_subagent(
            task,
            state=state,
            available_tools=available_tools,
            subagent_config=base_config,
        )
        assert role in {"outline", "researcher", "writer"}
        return executor_module.SubagentExecutor(
            config=report_config,
            tools=tools,
            app_config=app_config,
            parent_model="fake-model",
            thread_data=state["thread_data"],
            thread_id="t-e2e",
            run_id="run-e2e",
            step_observer=step_observer,
            thinking_enabled=thinking_enabled,
        )

    return factory


def _install_fake_model(monkeypatch, model, executor_module) -> None:
    monkeypatch.setattr(executor_module, "create_chat_model", lambda **kwargs: model)


def _tree() -> dict:
    return {
        "report_title": "示例公司 FY2024 偿付能力报告",
        "requirements": [{"id": "R1", "input_id": "u1", "text": "FY2024 偿付能力", "hard": True}],
        "nodes": [
            {"node_id": "n1", "parent_id": None, "kind": "section", "title": "偿付能力"},
            {
                "node_id": "n2",
                "parent_id": "n1",
                "kind": "writable",
                "title": "指标",
                "question": "FY2024 指标是多少？",
                "argument": ["读取官方年报"],
                "conclusion": "待证据",
                "evidence": [],
                "constraints": {"period": "FY2024"},
                "requirement_ids": ["R1"],
                "relations": [],
                "gap_ids": ["g1"],
                "status": "needs_evidence",
            },
        ],
        "removed_node_ids": [],
        "write_requests": [],
        "gap_outcomes": [],
        "user_decisions": [],
        "feedback_dispositions": [],
        "draft_dispositions": [],
    }


def _outline_model() -> FakeToolCallingModel:
    return FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "report_register_research_request",
                        "args": {
                            "questions": [
                                {
                                    "mode": "new",
                                    "question": "示例公司 FY2024 母公司口径偿付能力指标",
                                    "constraints": {"period": "FY2024"},
                                    "why_insufficient": "现有材料没有指标",
                                    "stop_condition": "官方年报附注",
                                    "requirement_refs": [{"input_id": "u1", "quote": "FY2024 偿付能力"}],
                                    "node_ids": [],
                                    "materials_checked": [],
                                }
                            ]
                        },
                        "id": "call-register",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content=json.dumps(
                    {
                        "summary": "outline ready",
                        "artifact_content": json.dumps(_tree(), ensure_ascii=False),
                        "artifact_type": "outline",
                        "artifact_metadata": {"completion_status": "complete"},
                    },
                    ensure_ascii=False,
                )
            ),
        ]
    )


def test_real_executor_outline_registers_gap_before_tree_is_accepted(tmp_path, monkeypatch, real_executor):
    app_config = _app_config()
    state = _state(tmp_path)
    _install_fake_model(monkeypatch, _outline_model(), real_executor)
    handler = DelegateHandler(executor=DR2SubagentExecutorAdapter(_executor_factory(state, app_config, [read_file_tool, ls_tool, grep_tool, glob_tool, write_file_tool], "sp-outline", real_executor)))
    action = SPAction.create(ActionType.DELEGATE, action_id="a-outline-e2e", reason="plan", task="plan the report", target_agent="outline", stage="planning")
    ctx = HandlerContext(state=state, stack=TaskMemoryStack(), thread_id="t-e2e", run_id="run-e2e")
    result = handler.handle(action, ctx)
    assert result.next_step == "continue", result.error
    outputs = Path(state["thread_data"]["outputs_path"])
    ledger = json.loads((outputs / "report" / "r1" / "ledger.json").read_text(encoding="utf-8"))
    assert ledger["tree"]["version"] == 1
    assert [(gap["gap_id"], gap["status"], gap["node_ids"]) for gap in ledger["gaps"]] == [("g1", "open", ["n2"])]
    assert ledger["tool_calls"]["a-outline-e2e:call-register"]["registered"][0]["gap_id"] == "g1"
    assert (outputs / "report" / "r1" / "tree" / "1.json").exists()


def test_real_executor_outline_file_toolchain_and_acceptance(tmp_path, monkeypatch, real_executor):
    app_config = _app_config()
    state = _state(tmp_path)
    tree = _tree()
    tree["nodes"][1]["gap_ids"] = []
    tree["write_requests"] = [{"key": "w-file-e2e", "node_ids": ["n2"], "feedback_wanted": False}]
    tree["user_decisions"] = [{"input_id": "u1", "gap_ids": [], "action": "apply_to_plan", "reason": "file delivery decision"}]
    encoded = json.dumps(tree, ensure_ascii=False)
    split = len(encoded) // 2
    path = "/mnt/user-data/outputs/report/r1/outline/delivery-a-outline-file-e2e.json"
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"description": "write first outline chunk", "path": path, "content": encoded[:split]},
                        "id": "call-write-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"description": "read current outline file", "path": path},
                        "id": "call-read-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"description": "append complete outline", "path": path, "content": encoded[split:], "append": True},
                        "id": "call-write-2",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"description": "verify complete outline file", "path": path},
                        "id": "call-read-2",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content=json.dumps(
                    {
                        "summary": "outline delivered in host file",
                        "artifact_content": None,
                        "artifact_type": "outline",
                        "artifact_metadata": {
                            "completion_status": "complete",
                            "delivery_mode": "file",
                            "tree_path": path,
                        },
                    },
                    ensure_ascii=False,
                )
            ),
        ]
    )
    _install_fake_model(monkeypatch, model, real_executor)
    handler = DelegateHandler(
        executor=DR2SubagentExecutorAdapter(
            _executor_factory(
                state,
                app_config,
                [read_file_tool, ls_tool, grep_tool, glob_tool, write_file_tool],
                "sp-outline",
                real_executor,
            )
        )
    )
    action = SPAction.create(
        ActionType.DELEGATE,
        action_id="a-outline-file-e2e",
        reason="plan in file",
        task="plan the report",
        target_agent="outline",
        stage="planning",
        metadata={"report_task": "plan"},
    )
    ctx = HandlerContext(state=state, stack=TaskMemoryStack(), thread_id="t-e2e", run_id="run-e2e")
    result = handler.handle(action, ctx)

    assert result.next_step == "continue", result.error
    ledger = ReportLedger.load_for_context(hooks.report_context(state))
    assert ledger.payload["tree"]["version"] == 1
    assert ledger.payload["write_requests"][0]["key"] == "w-file-e2e"
    assert ledger.payload["user_decisions"][0]["input_id"] == "u1"
    stored = json.loads(Path(ledger.payload["tree"]["path"]).read_text(encoding="utf-8"))
    assert stored["report_title"] == tree["report_title"]
    assert stored["write_requests"][0]["key"] == "w-file-e2e"
    assert stored["user_decisions"][0]["input_id"] == "u1"


def test_real_executor_researcher_saves_tool_return_and_charges_one_round(tmp_path, monkeypatch, real_executor):
    app_config = _app_config()
    state = _state(tmp_path)
    _install_fake_model(monkeypatch, _outline_model(), real_executor)
    outline_handler = DelegateHandler(executor=DR2SubagentExecutorAdapter(_executor_factory(state, app_config, [read_file_tool, ls_tool, grep_tool, glob_tool, write_file_tool], "sp-outline", real_executor)))
    outline_action = SPAction.create(ActionType.DELEGATE, action_id="a-outline-e2e", reason="plan", task="plan", target_agent="outline", stage="planning")
    ctx = HandlerContext(state=state, stack=TaskMemoryStack(), thread_id="t-e2e", run_id="run-e2e")
    assert outline_handler.handle(outline_action, ctx).error is None

    @tool("web_fetch")
    async def controlled_fetch(url: str) -> str:
        """Fetch one controlled URL asynchronously."""
        return "Official annual report note: FY2024 parent solvency ratio 123.4%."

    @tool("web_search")
    def controlled_search(query: str) -> str:
        """Search one controlled query."""
        return "result: official annual report"

    _install_fake_model(
        monkeypatch,
        FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "report_fetch_material",
                            "args": {"url": "https://example.com/annual-report", "title": "Annual report"},
                            "id": "call-material",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "summary": "found metric",
                            "artifact_content": "# Research\n\nFY2024 parent solvency ratio 123.4% [S1].",
                            "artifact_type": "research_observation",
                            "artifact_metadata": {
                                "completion_status": "complete",
                                "gap_attempts": [{"gap_id": "g1", "execution": "searched", "material_ids": ["m1"], "note": "official note"}],
                            },
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
        ),
        real_executor,
    )
    researcher_handler = DelegateHandler(executor=DR2SubagentExecutorAdapter(_executor_factory(state, app_config, [read_file_tool, ls_tool, grep_tool, glob_tool, controlled_search, controlled_fetch], "sp-researcher", real_executor)))
    researcher_action = SPAction.create(
        ActionType.DELEGATE,
        action_id="a-research-e2e",
        reason="research",
        task="find the metric",
        target_agent="researcher",
        stage="research",
        metadata={"report_gap_ids": ["g1"]},
    )
    result = researcher_handler.handle(researcher_action, ctx)
    assert result.next_step == "continue", result.error
    ledger = ReportLedger.load_for_context(hooks.report_context(state))
    gap = ledger.require_gap("g1")
    assert (gap["status"], gap["rounds"]) == ("awaiting_outline", 1)
    material = ledger.materials()[0]
    assert material["material_id"] == "m1"
    saved = Path(material["path"]).read_text(encoding="utf-8")
    assert saved == "Official annual report note: FY2024 parent solvency ratio 123.4%."
    assert ledger.payload["summaries"][0]["action_id"] == "a-research-e2e"


def test_real_executor_outline_reads_saved_material_with_read_file(tmp_path, monkeypatch, real_executor):
    app_config = _app_config()
    state = _state(tmp_path)
    context = hooks.report_context(state)
    assert context.outputs_path and context.report_id
    paths = report_store.ReportPaths(context.outputs_path, context.report_id)
    ledger = ReportLedger.load_for_context(context)
    ledger.begin_research_action("a-seed", [], allow_empty=True, kind="pre_research")
    ledger.save()

    @tool("web_fetch")
    async def seed_fetch(url: str) -> str:
        """Fetch the controlled material asynchronously."""
        return "Official annual report note: FY2024 parent solvency ratio 123.4%."

    material = json.loads(build_fetch_material_tool(paths=paths, action_id="a-seed", gap_ids=[], fetch_tool=seed_fetch).func(url="https://example.com/annual-report"))
    ledger = ReportLedger.load_for_context(context)
    ledger.finish_research_action("a-seed", gap_attempts=[], summary_path=None, artifact_id=None)
    ledger.save()

    tree = _tree()
    tree["nodes"][1]["evidence"] = [
        {
            "material_id": material["material_id"],
            "source_ids": [material["source_id"]],
            "locator": {"lines": [1, 1]},
            "use": "official solvency metric",
        }
    ]
    observer_messages: list[dict] = []
    _install_fake_model(
        monkeypatch,
        FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {"description": "read the saved material", "path": material["path"]},
                            "id": "call-read",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "report_register_research_request",
                            "args": {
                                "questions": [
                                    {
                                        "mode": "new",
                                        "question": "Confirm the FY2024 metric",
                                        "constraints": {"period": "FY2024"},
                                        "why_insufficient": "summary lacks the full note",
                                        "stop_condition": "read the saved note",
                                        "requirement_refs": [{"input_id": "u1", "quote": "FY2024 偿付能力"}],
                                        "node_ids": [],
                                        "materials_checked": [{"material_id": material["material_id"], "lines": [1, 1]}],
                                    }
                                ]
                            },
                            "id": "call-register-after-read",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "summary": "outline ready after reading material",
                            "artifact_content": json.dumps(tree, ensure_ascii=False),
                            "artifact_type": "outline",
                            "artifact_metadata": {"completion_status": "complete"},
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
        ),
        real_executor,
    )
    handler = DelegateHandler(
        executor=DR2SubagentExecutorAdapter(
            _executor_factory(
                state,
                app_config,
                [read_file_tool, ls_tool, grep_tool, glob_tool, write_file_tool],
                "sp-outline",
                real_executor,
                step_observer=lambda message, index: observer_messages.append(message),
            )
        )
    )
    action = SPAction.create(ActionType.DELEGATE, action_id="a-outline-read", reason="plan", task="plan", target_agent="outline", stage="planning")
    result = handler.handle(action, HandlerContext(state=state, stack=TaskMemoryStack(), thread_id="t-e2e", run_id="run-e2e"))
    assert result.next_step == "continue", result.error
    read_results = [message for message in observer_messages if isinstance(message, dict) and message.get("type") == "tool" and message.get("name") == "read_file"]
    assert read_results
    assert "FY2024 parent solvency ratio 123.4%" in str(read_results[-1].get("content"))
    ledger = ReportLedger.load_for_context(hooks.report_context(state))
    assert ledger.payload["tree"]["version"] == 1
    assert ledger.materials()[0]["material_id"] == material["material_id"]


def test_real_executor_toolnode_concurrent_material_calls_keep_both(tmp_path, monkeypatch, real_executor):
    app_config = _app_config()
    state = _state(tmp_path)

    @tool("web_fetch")
    async def controlled_fetch(url: str) -> str:
        """Fetch one controlled URL asynchronously."""
        return f"Controlled body for {url}"

    @tool("web_search")
    def controlled_search(query: str) -> str:
        """Search one controlled query."""
        return "controlled result"

    _install_fake_model(
        monkeypatch,
        FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "report_fetch_material",
                            "args": {"url": "https://example.com/a", "title": "A"},
                            "id": "call-a",
                            "type": "tool_call",
                        },
                        {
                            "name": "report_fetch_material",
                            "args": {"url": "https://example.com/b", "title": "B"},
                            "id": "call-b",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "summary": "orientation saved",
                            "artifact_content": "# Orientation\n\nTwo controlled materials saved.",
                            "artifact_type": "research_observation",
                            "artifact_metadata": {"completion_status": "complete"},
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
        ),
        real_executor,
    )
    handler = DelegateHandler(
        executor=DR2SubagentExecutorAdapter(
            _executor_factory(
                state,
                app_config,
                [read_file_tool, ls_tool, grep_tool, glob_tool, controlled_search, controlled_fetch],
                "sp-researcher",
                real_executor,
            )
        )
    )
    action = SPAction.create(ActionType.DELEGATE, action_id="a-concurrent-material", reason="pre-research", task="orientation", target_agent="researcher", stage="research")
    result = handler.handle(action, HandlerContext(state=state, stack=TaskMemoryStack(), thread_id="t-e2e", run_id="run-e2e"))
    assert result.next_step == "continue", result.error
    ledger = ReportLedger.load_for_context(hooks.report_context(state))
    assert len(ledger.payload["sources"]) == 2
    assert len(ledger.materials()) == 2
    assert {item["source_id"] for item in ledger.materials()} == {"S1", "S2"}
    assert {item["material_id"] for item in ledger.materials()} == {"m1", "m2"}
    assert ledger.action("a-concurrent-material")["status"] == "completed"


def test_real_executor_integration_review_and_first_finish(tmp_path, monkeypatch, real_executor):
    app_config = _app_config()
    state = _state(tmp_path)
    ctx = HandlerContext(state=state, stack=TaskMemoryStack(), thread_id="t-e2e", run_id="run-e2e")
    report = hooks.report_context(state)
    assert report.outputs_path and report.report_id
    paths = report_store.ReportPaths(report.outputs_path, report.report_id)
    ledger = ReportLedger.load(paths)
    tree = {
        "version": 1,
        "report_title": "示例公司 P3 报告",
        "requirements": [{"id": "R1", "input_id": "u1", "text": "FY2024 偿付能力", "hard": True}],
        "nodes": [
            {"node_id": "n1", "parent_id": None, "kind": "section", "title": "偿付能力"},
            {
                "node_id": "n2",
                "parent_id": "n1",
                "kind": "writable",
                "title": "指标",
                "question": "指标如何？",
                "argument": ["读取材料"],
                "conclusion": "待证据",
                "evidence": [],
                "constraints": {},
                "requirement_ids": ["R1"],
                "relations": [],
                "gap_ids": [],
                "status": "planned",
            },
        ],
    }
    tree_path = paths.tree_file(1)
    report_store.write_json_atomic(tree_path, tree)
    ledger.payload["tree"] = {
        "version": 1,
        "path": str(tree_path),
        "virtual_path": paths.virtual(tree_path),
        "sha256": report_store.sha256_text(tree_path.read_text(encoding="utf-8")),
        "node_ids": ["n1", "n2"],
    }
    draft_path = paths.draft_file("n2", "d1")
    report_store.write_text_atomic(draft_path, "P3 candidate body.\n")
    ledger.payload["drafts"] = [
        {
            "draft_id": "d1",
            "node_id": "n2",
            "write_key": "w1",
            "path": str(draft_path),
            "virtual_path": paths.virtual(draft_path),
            "sha256": report_store.sha256_text("P3 candidate body.\n"),
            "chars": len("P3 candidate body.\n"),
            "is_current": True,
            "review_status": "none",
            "status": "current",
            "applicable_fingerprint": "fp",
        }
    ]
    ledger.save()

    action = SPAction.create(
        ActionType.DELEGATE,
        action_id="a-integrate-e2e",
        reason="review",
        task="review candidate",
        target_agent="outline",
        stage="verification",
        metadata={"report_task": "integrate_review"},
    )
    task_meta: dict = {"stage": "verification"}
    refs: dict = {}
    hooks.prepare_delegation(action, ctx, task_meta, refs)
    packet = refs["report_packet"]
    review_content = json.dumps(
        {
            "inputs_digest": packet["inputs_digest"],
            "reviewed_candidate_sha256": packet["candidate_sha256"],
            "review": {
                "status": "ready",
                "outcome": "deliver",
                "findings": [],
                "covered_requirement_ids": ["R1"],
                "edits": [],
            },
        },
        ensure_ascii=False,
    )
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content=json.dumps(
                    {
                        "summary": "reviewed",
                        "artifact_content": review_content,
                        "artifact_type": "integration_review",
                        "artifact_metadata": {"artifact_type": "integration_review", "report_task": "integrate_review", "status": "ready"},
                    },
                    ensure_ascii=False,
                )
            )
        ]
    )
    _install_fake_model(monkeypatch, model, real_executor)
    handler = DelegateHandler(executor=DR2SubagentExecutorAdapter(_executor_factory(state, app_config, [read_file_tool, ls_tool, grep_tool, glob_tool, write_file_tool], "sp-outline", real_executor)))
    result = handler.handle(action, ctx)
    assert result.next_step == "continue", result.error
    ctx.state.update(result.state_update)
    ledger = ReportLedger.load(paths)
    assert ledger.payload["final_report"] is not None
    final_content = Path(ledger.payload["final_report"]["path"]).read_text(encoding="utf-8")
    assert "P3 candidate body." in final_content
    finish = FinishHandler().handle(SPAction.create(ActionType.FINISH, action_id="a-finish-e2e", reason="done", task="done"), ctx)
    assert finish.next_step == "finish", finish.error
    ledger = ReportLedger.load(paths)
    assert ledger.payload["status"] == "delivered"
    assert ledger.payload["delivered"]["final_path"] == ledger.payload["final_report"]["virtual_path"]


def test_real_executor_writer_writes_host_path_and_disables_thinking(tmp_path, monkeypatch, real_executor):
    app_config = _app_config(supports_thinking=True)
    state = _state(tmp_path)
    ctx = HandlerContext(state=state, stack=TaskMemoryStack(), thread_id="t-e2e", run_id="run-e2e")
    tree = {
        "report_title": "示例公司报告",
        "requirements": [{"id": "R1", "input_id": "u1", "text": "示例公司 FY2024 偿付能力", "hard": True}],
        "nodes": [
            {"node_id": "n1", "parent_id": None, "kind": "section", "title": "偿付能力"},
            {
                "node_id": "n2",
                "parent_id": "n1",
                "kind": "writable",
                "title": "指标",
                "question": "FY2024 指标如何？",
                "argument": ["读取官方附注"],
                "conclusion": "待证据",
                "evidence": [],
                "constraints": {"period": "FY2024"},
                "requirement_ids": ["R1"],
                "relations": [],
                "gap_ids": [],
                "status": "planned",
            },
        ],
        "removed_node_ids": [],
        "write_requests": [{"key": "w1", "node_ids": ["n2"], "feedback_wanted": True}],
        "gap_outcomes": [],
        "feedback_dispositions": [],
        "draft_dispositions": [],
        "user_decisions": [],
    }
    outline_action = SPAction.create(ActionType.DELEGATE, action_id="a-outline-writer", reason="plan", task="plan", target_agent="outline", stage="planning")
    outline_meta: dict = {"stage": "planning"}
    outline_refs: dict = {}
    hooks.prepare_delegation(outline_action, ctx, outline_meta, outline_refs)
    outline_task = SPSubagentTask(action_id="a-outline-writer", subagent_type="outline", task="plan", description="plan", metadata=outline_meta, context_refs=outline_refs)
    outline_entry = ctx.stack.append_delegate("outline", thread_id="t-e2e", run_id="run-e2e", stage="planning")
    outline_result = SPSubagentResult(
        status=SPSubagentStatus.COMPLETED,
        result="tree",
        artifact_content=json.dumps(tree, ensure_ascii=False),
        artifact_type="outline",
        artifact_metadata={"completion_status": "complete"},
    )
    accepted = hooks.handle_success(outline_action, ctx, outline_result, outline_task, artifact_adapter=SPArtifactAdapter(), delegate_entry=outline_entry)
    assert accepted.next_step == "continue", accepted.error

    captured: list[dict] = []
    writer_model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "description": "write the assigned node draft",
                            "path": "/mnt/user-data/outputs/report/r1/drafts/n2/d1.md",
                            "content": "Node two body from the Writer.\n",
                        },
                        "id": "call-write",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content=json.dumps(
                    {
                        "summary": "draft written",
                        "artifact_content": None,
                        "artifact_type": "node_draft",
                        "artifact_metadata": {
                            "node_id": "n2",
                            "draft_path": "/mnt/user-data/outputs/report/r1/drafts/n2/d1.md",
                            "completion_status": "complete",
                        },
                    },
                    ensure_ascii=False,
                )
            ),
        ]
    )
    monkeypatch.setattr(real_executor, "create_chat_model", lambda **kwargs: (captured.append(kwargs), writer_model)[1])
    from deerflow.sp.runtime import _resolve_report_role_thinking

    writer_thinking = _resolve_report_role_thinking("writer", None, {}, {})
    handler = DelegateHandler(
        executor=DR2SubagentExecutorAdapter(
            _executor_factory(
                state,
                app_config,
                [read_file_tool, ls_tool, grep_tool, glob_tool, write_file_tool],
                "sp-reporter",
                real_executor,
                thinking_enabled=writer_thinking,
            )
        )
    )
    action = SPAction.create(
        ActionType.DELEGATE,
        action_id="a-writer-e2e",
        reason="write node",
        task="write node n2",
        target_agent="reporter",
        stage="reporting",
        metadata={"report_write_key": "w1"},
    )
    result = handler.handle(action, ctx)
    assert result.next_step == "continue", result.error
    assert captured and captured[0]["thinking_enabled"] is False

    ledger = ReportLedger.load_for_context(hooks.report_context(state))
    draft = next(item for item in ledger.payload["drafts"] if item["node_id"] == "n2")
    assert draft["review_status"] == "open"
    assert Path(draft["path"]).read_text(encoding="utf-8") == "Node two body from the Writer.\n"
    assert ledger.payload["write_requests"][0]["status"] == "completed"
    assert ledger.payload["write_requests"][0]["attempts"][0]["action_id"] == "a-writer-e2e"
