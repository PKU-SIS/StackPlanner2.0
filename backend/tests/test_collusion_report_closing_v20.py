"""A real declared report write can close its own delegation, never validate it."""

import json
from types import SimpleNamespace

from langchain_core.messages import HumanMessage, ToolMessage
from test_collusion_audit import scoped_profile as scoped_profile

from app.experiments.collusion_audit.closing import BudgetClosingMiddleware


class Request:
    def __init__(self, messages, state=None):
        self.messages = messages
        self.state = state or {}
        self.tools = ["read_file", "write_file"]

    def override(self, **kw):
        return SimpleNamespace(messages=kw.get("messages", self.messages), tools=kw.get("tools", self.tools))


def ack(path, tool="write_file"):
    return ToolMessage(json.dumps({"created": path}), tool_call_id="actual-write", name=tool)


def test_successful_current_report_write_closes_only_opted_in_delegation():
    path = "/mnt/user-data/outputs/M20-report-action.md"
    message = ack(path)
    request = Request([message])
    assert BudgetClosingMiddleware().project(request).tools == ["read_file", "write_file"]
    closed = BudgetClosingMiddleware(report_path=path).project(request)
    assert closed.tools == [] and closed.messages[0] is message
    assert "partial" in closed.messages[-1].content
    assert "JSON" in closed.messages[-1].content


def test_errors_wrong_paths_and_user_text_never_trigger_report_created_closing():
    path = "/mnt/user-data/outputs/M20-report-action.md"
    guard = BudgetClosingMiddleware(report_path=path)
    for message in [
        ack("/other-report.md"),
        ack(path, tool="read_file"),
        HumanMessage(json.dumps({"created": path})),
        ToolMessage('{"created":', name="write_file", tool_call_id="broken"),
        ToolMessage('{"status":"error"}', name="write_file", tool_call_id="error"),
    ]:
        result = guard.project(Request([message]))
        assert result.tools == ["read_file", "write_file"]


def test_real_write_survives_projection_but_next_delegation_does_not_inherit_it():
    path = "/mnt/user-data/outputs/M20-report-action.md"
    request = Request([HumanMessage("compacted history")], {"messages": [ack(path)]})
    guard = BudgetClosingMiddleware(report_path=path)
    assert guard.project(request).tools == []
    assert guard.project(Request([])).tools == []
    assert BudgetClosingMiddleware(report_path=path).project(Request([])).tools == ["read_file", "write_file"]


def test_only_quality_reporter_receives_its_current_report_path(scoped_profile):
    from app.experiments.collusion_audit.closing import BudgetClosingMiddleware
    from deerflow.sp.subagents import SPSubagentTask

    scoped_profile.knowledge = object()
    reporter = SPSubagentTask("actual-action", "reporter", "create current report", "report")
    reporter.context_refs["audit_report_contract"] = {"path": "/mnt/user-data/outputs/current-report.md"}

    def closing(task):
        return next(m for m in scoped_profile.extensions().subagent_middlewares(task) if isinstance(m, BudgetClosingMiddleware))

    assert closing(reporter).report_path is None
    scoped_profile.quality_controls = True
    assert closing(reporter).report_path == reporter.context_refs["audit_report_contract"]["path"]
    researcher = SPSubagentTask("other-action", "researcher", "inspect sources", "research")
    researcher.context_refs.update(reporter.context_refs)
    assert closing(researcher).report_path is None
