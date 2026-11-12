"""Compact control guidance must preserve all task data and native tool choice."""

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage
from test_collusion_audit import scoped_profile as scoped_profile

from app.experiments.collusion_audit.control_scope import CompactControlScopeMiddleware


class Request:
    def __init__(self, messages):
        self.messages = messages
        self.tools = ["sp_delegate", "sp_think", "sp_finish"]
        self.state = {"native": "unchanged"}

    def override(self, **kw):
        return SimpleNamespace(messages=kw.get("messages", self.messages), tools=kw.get("tools", self.tools), state=self.state)


def test_default_off_is_identity_and_does_not_append_any_instruction():
    req = Request([HumanMessage("full rule and source")])
    seen = []
    result = CompactControlScopeMiddleware().wrap_model_call(req, lambda r: seen.append(r) or r)
    assert result is req and seen == [req]


def test_control_guidance_keeps_full_rule_body_history_and_all_native_tools():
    rule = HumanMessage("完整规则全部字段，全部判断标准。" * 100)
    source = HumanMessage("全部双端原始证据正文，末尾也保留。" * 1000, name="current_code_facts")
    reasoning = AIMessage("原历史reasoning，禁止摘要替代。" * 100)
    req = Request([rule, reasoning, source])
    result = CompactControlScopeMiddleware(enabled=True).project(req)
    assert result.messages[:-1] == req.messages
    assert all(a is b for a, b in zip(result.messages, req.messages))
    assert result.tools == req.tools and result.state is req.state
    assert result.messages[-1].name == "audit_control_scope_notice"


def test_instruction_is_not_appended_again_when_projection_is_replayed():
    req = Request([HumanMessage("full body")])
    guard = CompactControlScopeMiddleware(enabled=True)
    first = guard.project(req)
    second = guard.project(Request(first.messages))
    assert len(second.messages) == len(first.messages)


def test_only_quality_central_enables_compact_control_guidance(scoped_profile):
    from deerflow.sp.subagents import SPSubagentTask

    assert not any(isinstance(m, CompactControlScopeMiddleware) for m in scoped_profile.extensions().central_middlewares)
    scoped_profile.quality_controls = True
    extension = scoped_profile.extensions()
    assert any(isinstance(m, CompactControlScopeMiddleware) and m.enabled for m in extension.central_middlewares)
    task = SPSubagentTask("a", "researcher", "inspect sources", "research")
    assert not any(isinstance(m, CompactControlScopeMiddleware) for m in extension.subagent_middlewares(task))
