"""Repeated actual identical no-new-data receipts close only the local scope."""

import json
from types import SimpleNamespace

from langchain_core.messages import HumanMessage, ToolMessage
from test_collusion_audit import scoped_profile as scoped_profile

from app.experiments.collusion_audit.closing import BudgetClosingMiddleware as Closing


class Request:
    def __init__(self, messages):
        self.messages = messages
        self.tools = ["read_file", "audit_collection_overview"]
        self.state = {"messages": messages}

    def override(self, **kw):
        return SimpleNamespace(messages=kw.get("messages", self.messages), tools=kw.get("tools", self.tools), state=self.state)


def receipt(i, ref="/actual/receipt.json", name="audit_collection_overview", status=None):
    return ToolMessage(content=json.dumps({"status": "already_returned", "result_ref": ref}), name=name, tool_call_id=str(i), **({"status": status} if status else {}))


def test_default_disabled_keeps_existing_tools_and_messages():
    request = Request([receipt(i) for i in range(6)])
    assert Closing().project(request) is request


def test_three_recent_actual_same_receipts_close_without_claiming_completion():
    rule = HumanMessage("Full rule and source body")
    messages = [rule, *[ToolMessage(content="actual body", name="read_file", tool_call_id=str(i)) for i in range(3)], *[receipt(i + 3) for i in range(3)]]
    request = Request(messages)
    result = Closing(close_repeated_reads=True).project(request)
    assert result.tools == []
    assert all(a is b for a, b in zip(messages, result.messages))
    assert "partial" in result.messages[-1].content
    assert result.messages[-1].name == "audit_repeated_read_closing"
    assert request.tools == ["read_file", "audit_collection_overview"]


def test_different_receipts_errors_and_user_echo_do_not_trigger_closing():
    for records in [
        [receipt(i, ref=f"/actual/{i}.json") for i in range(6)],
        [receipt(i, status="error") for i in range(6)],
        [receipt(i, name="write_file") for i in range(6)],
        [HumanMessage(receipt(i).content) for i in range(6)],
    ]:
        request = Request(records)
        assert Closing(close_repeated_reads=True).project(request) is request


def test_one_or_two_repeats_and_interleaved_new_work_remain_available():
    for records in [
        [ToolMessage(content="source", name="read_file", tool_call_id=str(i)) for i in range(4)] + [receipt(4), receipt(5)],
        [receipt(i) for i in range(3)] + [ToolMessage(content="new source", name="read_file", tool_call_id=str(i + 3)) for i in range(3)],
    ]:
        request = Request(records)
        assert Closing(close_repeated_reads=True).project(request) is request


def test_only_quality_scoped_delegations_enable_repeat_closing(scoped_profile):
    from deerflow.sp.subagents import SPSubagentTask

    scoped_profile.knowledge = SimpleNamespace()
    task = SPSubagentTask("a", "researcher", "review", "test")
    baseline = scoped_profile.extensions().subagent_middlewares(task)
    assert all(not m.close_repeated_reads for m in baseline if isinstance(m, Closing))
    scoped_profile.quality_controls = True
    quality = scoped_profile.extensions().subagent_middlewares(task)
    assert any(m.close_repeated_reads for m in quality if isinstance(m, Closing))
