"""P1 material capture: exact tool return, stable identities, honest failures."""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from langchain.tools import tool
from langchain_core.messages import HumanMessage

from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.report import hooks
from deerflow.sp.report import store as report_store
from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.report.materials import build_fetch_material_tool, build_register_user_material_tool
from deerflow.sp.report.tree import validate_tree
from deerflow.sp.subagents import SPSubagentTask
from deerflow.subagents.config import SubagentConfig


def _context(tmp_path: Path) -> HandlerContext:
    state = {
        "thread_data": {"outputs_path": str(tmp_path)},
        "messages": [HumanMessage(content="写一份示例公司研究报告", id="m1")],
    }
    ctx = HandlerContext(state=state, stack=TaskMemoryStack(), thread_id="t1", run_id="run1")
    hooks.start_report(ctx, SPAction.create(ActionType.REPLAN, reason="start", task="start", metadata={"report_action": "start"}))
    return ctx


def _paths(ctx: HandlerContext) -> report_store.ReportPaths:
    context = hooks.report_context(ctx.state)
    assert context.outputs_path and context.report_id
    return report_store.ReportPaths(context.outputs_path, context.report_id)


def _begin(ctx: HandlerContext, action_id: str = "a-material") -> None:
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    ledger.begin_research_action(action_id, [], allow_empty=True, kind="pre_research")
    ledger.save()


def _tool_for(paths: report_store.ReportPaths, fetch_tool, action_id: str = "a-material"):
    return build_fetch_material_tool(paths=paths, action_id=action_id, gap_ids=[], fetch_tool=fetch_tool)


@tool("web_fetch")
def _short_fetch(url: str) -> str:
    """Fetch a URL."""
    return "Short page body."


def test_long_and_short_returns_are_saved_verbatim(tmp_path):
    ctx = _context(tmp_path)
    _begin(ctx)
    long_text = "第 1 行：示例公司年度报告。\n" + "\n".join(f"line {index}" for index in range(500))

    @tool("web_fetch")
    def fetch(url: str) -> str:
        """Fetch a URL."""
        return long_text

    material_tool = _tool_for(_paths(ctx), fetch)
    payload = json.loads(material_tool.func(url="https://example.com/report", title="Annual report"))
    assert payload["saved"] is True
    assert payload["chars"] == len(long_text)
    assert payload["lines"] == long_text.count("\n") + 1
    material_path = Path(payload["path"].replace("/mnt/user-data/outputs", str(tmp_path)))
    assert material_path.read_text(encoding="utf-8") == long_text
    assert payload["source_id"] == "S1" and payload["material_id"] == "m1"
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.materials()[0]["virtual_path"] == payload["path"]

    short = json.loads(build_fetch_material_tool(paths=_paths(ctx), action_id="a-material", gap_ids=[], fetch_tool=_short_fetch).func(url="https://example.com/short"))
    assert short["saved"] is True
    assert short["material_id"] == "m2"


def test_same_url_reuses_source_and_content_identity(tmp_path):
    ctx = _context(tmp_path)
    _begin(ctx)
    material_tool = _tool_for(_paths(ctx), _short_fetch)
    first = json.loads(material_tool.func(url="https://example.com/page#section"))
    second = json.loads(material_tool.func(url="https://example.com/page"))
    assert first["source_id"] == second["source_id"] == "S1"
    assert first["material_id"] == second["material_id"] == "m1"
    assert second["duplicate_of"] == "m1"


def test_same_url_new_content_keeps_source_and_allocates_material(tmp_path):
    ctx = _context(tmp_path)
    _begin(ctx)
    versions = iter(["version one", "version two"])

    @tool("web_fetch")
    def fetch(url: str) -> str:
        """Fetch a URL."""
        return next(versions)

    material_tool = _tool_for(_paths(ctx), fetch)
    first = json.loads(material_tool.func(url="https://example.com/page"))
    second = json.loads(material_tool.func(url="https://example.com/page"))
    assert first["source_id"] == second["source_id"] == "S1"
    assert first["material_id"] == "m1"
    assert second["material_id"] == "m2"
    assert second["duplicate_of"] is None


def test_fetch_error_is_not_registered_as_material(tmp_path):
    ctx = _context(tmp_path)
    _begin(ctx)

    @tool("web_fetch")
    def fetch(url: str) -> str:
        """Fetch a URL."""
        return "Error: HTTP 404 while fetching https://example.com/missing"

    material_tool = _tool_for(_paths(ctx), fetch)
    payload = json.loads(material_tool.func(url="https://example.com/missing"))
    assert payload["saved"] is False
    assert "fetch_failed" in payload["error"]
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.materials() == []
    action = ledger.action("a-material")
    assert action["tool_executions"][-1]["ok"] is False


def test_fetch_exception_is_not_registered_as_material(tmp_path):
    ctx = _context(tmp_path)
    _begin(ctx)

    @tool("web_fetch")
    def fetch(url: str) -> str:
        """Fetch a URL."""
        raise TimeoutError("upstream timed out")

    material_tool = _tool_for(_paths(ctx), fetch)
    payload = json.loads(material_tool.func(url="https://example.com/slow"))
    assert payload["saved"] is False
    assert "TimeoutError" in payload["error"]
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.materials() == []


def test_material_write_failure_is_not_registered(tmp_path, monkeypatch):
    ctx = _context(tmp_path)
    _begin(ctx)
    original = report_store.write_text_atomic

    def failing_write(path: Path, text: str) -> None:
        if str(path).endswith(".txt"):
            raise OSError("disk full")
        original(path, text)

    monkeypatch.setattr(report_store, "write_text_atomic", failing_write)
    material_tool = _tool_for(_paths(ctx), _short_fetch)
    payload = json.loads(material_tool.func(url="https://example.com/page"))
    assert payload["saved"] is False
    assert "material_save_failed" in payload["error"]
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.materials() == []


def test_known_truncation_marker_is_reported(tmp_path):
    ctx = _context(tmp_path)
    _begin(ctx)

    @tool("web_fetch")
    def fetch(url: str) -> str:
        """Fetch a URL."""
        return "partial body ...<truncated>"

    material_tool = _tool_for(_paths(ctx), fetch)
    payload = json.loads(material_tool.func(url="https://example.com/partial"))
    assert payload["saved"] is True
    assert payload["upstream_truncation"] is True
    assert payload["content_scope"] == "fetch_tool_return"


def test_async_fetch_tool_is_supported(tmp_path):
    ctx = _context(tmp_path)
    _begin(ctx)

    @tool("web_fetch")
    async def async_fetch(url: str) -> str:
        """Fetch a URL asynchronously."""
        await asyncio.sleep(0)
        return "async page body"

    material_tool = _tool_for(_paths(ctx), async_fetch)
    payload = json.loads(material_tool.func(url="https://example.com/async"))
    assert payload["saved"] is True
    assert Path(payload["path"].replace("/mnt/user-data/outputs", str(tmp_path))).read_text(encoding="utf-8") == "async page body"


def test_async_fetch_tool_works_inside_a_running_loop(tmp_path):
    ctx = _context(tmp_path)
    _begin(ctx)

    @tool("web_fetch")
    async def async_fetch(url: str) -> str:
        """Fetch a URL asynchronously."""
        await asyncio.sleep(0)
        return "loop page body"

    material_tool = _tool_for(_paths(ctx), async_fetch)

    async def call_from_loop() -> dict:
        return json.loads(material_tool.func(url="https://example.com/loop"))

    payload = asyncio.run(call_from_loop())
    assert payload["saved"] is True


def test_async_jina_style_provider_marks_truncation(tmp_path):
    ctx = _context(tmp_path)
    _begin(ctx)

    @tool("web_fetch")
    async def async_fetch(url: str) -> str:
        """Fetch a URL asynchronously."""
        return "jina body"

    async_fetch.coroutine.__module__ = "deerflow.community.jina_ai.tools"
    material_tool = _tool_for(_paths(ctx), async_fetch)
    payload = json.loads(material_tool.func(url="https://example.com/jina"))
    assert payload["saved"] is True
    assert payload["upstream_truncation"] is True


def test_concurrent_material_calls_keep_both_identities(tmp_path):
    ctx = _context(tmp_path)
    _begin(ctx)
    material_tool = _tool_for(_paths(ctx), _short_fetch)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda url: json.loads(material_tool.func(url=url)),
                ["https://example.com/a", "https://example.com/b"],
            )
        )
    assert all(item["saved"] for item in results)
    assert {item["source_id"] for item in results} == {"S1", "S2"}
    assert {item["material_id"] for item in results} == {"m1", "m2"}
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert len(ledger.payload["sources"]) == 2
    assert len(ledger.materials()) == 2
    catalog = (tmp_path / "report" / "r1" / "materials" / "catalog.md").read_text(encoding="utf-8")
    assert "m1" in catalog and "m2" in catalog


def test_user_input_registration_reuses_saved_original_and_is_citable(tmp_path):
    ctx = _context(tmp_path)
    paths = _paths(ctx)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    input_item = ledger.inputs()[0]
    input_id = str(input_item["input_id"])
    original = Path(str(input_item["path"])).read_text(encoding="utf-8")
    tool = build_register_user_material_tool(paths=paths)

    payload = json.loads(tool.func(input_id=input_id, title="用户提供的示例材料"))
    assert payload["saved"] is True
    assert payload["input_id"] == input_id
    assert payload["source_kind"] == "user_input"
    assert payload["source_id"] == "S1"
    assert payload["material_id"] == "m1"
    assert payload["url"] is None
    material_path = Path(str(payload["path"]).replace("/mnt/user-data/outputs", str(tmp_path)))
    assert material_path.read_text(encoding="utf-8") == original

    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    source = ledger.payload["sources"][0]
    assert source["source_kind"] == "user_input"
    assert source["input_id"] == input_id
    assert not (ledger.payload.get("actions") or {})
    assert ledger.materials()[0]["input_id"] == input_id

    tree = {
        "report_title": "用户材料报告",
        "requirements": [{"id": "R1", "input_id": input_id, "text": "使用用户提供的原文", "hard": True}],
        "nodes": [
            {
                "node_id": "n1",
                "parent_id": None,
                "kind": "writable",
                "title": "用户提供的材料",
                "question": "用户原文说明了什么？",
                "argument": ["读取已登记的用户材料"],
                "conclusion": "用户原文已登记为可引用材料。",
                "constraints": {"must_cite": ["S1"]},
                "requirement_ids": ["R1"],
                "relations": [],
                "gap_ids": [],
                "status": "planned",
                "evidence": [
                    {
                        "material_id": payload["material_id"],
                        "source_ids": [payload["source_id"]],
                        "locator": {"lines": [1, 1]},
                        "use": "提供用户原文",
                    }
                ],
            }
        ],
        "removed_node_ids": [],
        "write_requests": [],
        "gap_outcomes": [],
        "user_decisions": [],
        "feedback_dispositions": [],
        "draft_dispositions": [],
    }
    validate_tree(tree, ledger)

    again = json.loads(tool.func(input_id=input_id, title="用户提供的示例材料"))
    assert again["material_id"] == "m1"
    assert again["duplicate_of"] == "m1"
    assert len(ReportLedger.load_for_context(hooks.report_context(ctx.state)).materials()) == 1


def test_outline_subagent_exposes_user_material_registration_tool(tmp_path):
    ctx = _context(tmp_path)
    report = hooks.report_context(ctx.state)
    task = SPSubagentTask(
        action_id="a-outline-plan",
        subagent_type="outline",
        task="plan the report",
        description="plan",
        metadata={"report_role": "outline", "report_id": report.report_id, "report_task": "plan"},
    )
    tools = [
        SimpleNamespace(name=name)
        for name in ("read_file", "write_file", "grep", "glob", "ls")
    ]
    config = SubagentConfig(
        name="sp-outline",
        description="outline",
        system_prompt="",
        disallowed_tools=["write_file"],
        max_turns=36,
    )

    returned_config, returned_tools, role = hooks.configure_report_subagent(
        task,
        state=ctx.state,
        available_tools=tools,
        subagent_config=config,
    )

    assert role == "outline"
    assert "report_save_user_material" in returned_config.tools
    assert any(getattr(tool, "name", "") == "report_save_user_material" for tool in returned_tools)
