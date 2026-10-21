"""Report material extraction uses HTML readability without changing web_fetch."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import HumanMessage

from deerflow.community.direct_fetch.tools import web_fetch_tool
from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.report import hooks
from deerflow.sp.report import materials as report_materials
from deerflow.sp.report import store as report_store
from deerflow.sp.report.ledger import ReportLedger


def _context(tmp_path: Path) -> HandlerContext:
    state = {
        "thread_data": {"outputs_path": str(tmp_path)},
        "messages": [HumanMessage(content="写一份示例公司研究报告", id="m1")],
    }
    ctx = HandlerContext(state=state, stack=TaskMemoryStack(), thread_id="t1", run_id="run1")
    hooks.start_report(
        ctx,
        SPAction.create(
            ActionType.REPLAN,
            reason="start",
            task="start",
            metadata={"report_action": "start"},
        ),
    )
    return ctx


def _paths(ctx: HandlerContext) -> report_store.ReportPaths:
    context = hooks.report_context(ctx.state)
    assert context.outputs_path and context.report_id
    return report_store.ReportPaths(context.outputs_path, context.report_id)


def test_html_is_extracted_before_material_save(tmp_path, monkeypatch):
    ctx = _context(tmp_path)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    ledger.begin_research_action("a1", [], allow_empty=True, run_id="run1")
    ledger.save()

    async def fake_fetch(url: str, **kwargs):
        return """
        <!doctype html><html><head><title>Example</title></head><body>
        <nav>Home | Login</nav>
        <article><h1>Economic release</h1>
        <p>Consumption rose and investment remained stable.</p>
        <table><tr><th>Metric</th><th>Value</th></tr><tr><td>GDP</td><td>5%</td></tr></table>
        </article><footer>Footer navigation</footer></body></html>
        """

    monkeypatch.setattr(
        "deerflow.community.direct_fetch.tools.fetch_public_url",
        fake_fetch,
    )
    monkeypatch.setattr(
        report_materials,
        "_direct_fetch_options",
        lambda: {
            "timeout": 20.0,
            "max_bytes": 1_000_000,
            "max_chars": 20_000,
            "allow_private_addresses": False,
            "trust_env": False,
        },
    )

    tool = report_materials.build_fetch_material_tool(
        paths=_paths(ctx),
        action_id="a1",
        gap_ids=[],
        fetch_tool=web_fetch_tool,
    )
    payload = json.loads(tool.func(url="https://example.com/release"))
    assert payload["saved"] is True
    assert payload["content_scope"] == "extracted_markdown"
    path = Path(payload["path"].replace("/mnt/user-data/outputs", str(tmp_path)))
    saved = path.read_text(encoding="utf-8")
    assert "Economic release" in saved
    assert "<html" not in saved.lower()
    assert "<nav" not in saved.lower()


def test_json_and_plain_text_keep_their_path(tmp_path, monkeypatch):
    monkeypatch.setattr(
        report_materials,
        "_direct_fetch_options",
        lambda: {
            "timeout": 20.0,
            "max_bytes": 1_000_000,
            "max_chars": 20_000,
            "allow_private_addresses": False,
            "trust_env": False,
        },
    )

    for body in ('{"status":"ok","items":[1,2]}', "plain text body"):
        async def fake_fetch(url: str, **kwargs):
            return body

        monkeypatch.setattr(
            "deerflow.community.direct_fetch.tools.fetch_public_url",
            fake_fetch,
        )
        text, scope, truncated, error = report_materials._fetch_material_text(
            web_fetch_tool,
            "https://example.com/data",
        )
        assert error is None
        assert text == body
        assert scope == "fetch_tool_return"
        assert truncated is False

