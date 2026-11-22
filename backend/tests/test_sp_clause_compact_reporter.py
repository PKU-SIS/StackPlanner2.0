"""Compact CLAUSE policy must not change unrelated prompts or native gates."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from deerflow.sp.runtime import DR2SPExecutorProvider
from deerflow.sp.source_prefetch import SourcePrefetchSettings, SourcePrefetchVisibilityMiddleware
from deerflow.sp.subagents import SPSubagentTask
from deerflow.sp.subagents.dr2_adapter import public_schema_delivery_instruction
from deerflow.subagents.builtins.sp_specialists import REPORTER_CONFIG, clause_public_reporter_system_prompt, reporter_system_prompt


def contract():
    return {
        "delivery_mode": "artifact",
        "output_parse_mode": "json",
        "output_schema": {"type": "array", "items": {"type": "object", "properties": {"text": {"type": "string"}, "explanation": {"type": "string"}}, "required": ["text", "explanation"], "additionalProperties": False}},
        "source_requirements": {"audit_agent": "reporter", "files": [{"path": "/mnt/user-data/uploads/document.txt", "total_lines": 2}], "document_ids": []},
    }


def capture(monkeypatch, flag, scope="clause"):
    if flag is None:
        monkeypatch.delenv("SP_CLAUSE_COMPACT_REPORTER", raising=False)
    else:
        monkeypatch.setenv("SP_CLAUSE_COMPACT_REPORTER", flag)
    public = contract()
    role, skills, config = "reporter", ["clause"], REPORTER_CONFIG
    if scope == "other_skill":
        skills = ["nba"]
    elif scope == "different_source":
        public["source_requirements"]["files"][0]["path"] = "/mnt/user-data/uploads/other.txt"
    elif scope == "inline":
        public["delivery_mode"] = "inline"
    elif scope == "schema":
        public["output_schema"]["items"]["additionalProperties"] = True
    elif scope == "rag":
        public["source_requirements"]["document_ids"] = ["required-external-document"]
    elif scope == "other_role":
        role = "researcher"
    elif scope == "unbound":
        public = {}
    elif scope == "custom_system":
        from dataclasses import replace

        config = replace(config, system_prompt=REPORTER_CONFIG.system_prompt + "\nCaller-owned exact custom instructions.")
    seen = {}

    class Executor:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr("deerflow.subagents.get_subagent_config", lambda *args, **kwargs: config)
    monkeypatch.setattr("deerflow.subagents.SubagentExecutor", Executor)
    task = SPSubagentTask(action_id="audit", subagent_type=role, task="The original whole-document task.", description="Audit", context_refs={"task_contract": public}, metadata={"skill_names": skills})
    before = deepcopy(task.context_refs)
    provider = DR2SPExecutorProvider(app_config=SimpleNamespace(), parent_model="test", runnable_config={}, available_skill_names=frozenset({"clause", "nba"}))
    provider._tools = []
    provider({}, SimpleNamespace(context={"thread_id": "thread", "run_id": "run"}, stream_writer=None)).adapter._executor_factory(task)
    assert task.task == "The original whole-document task." and task.context_refs == before
    return seen, task


@pytest.mark.parametrize("flag", [None, "0", "true", "yes", "01", "1 "])
def test_compact_is_strictly_opt_in_and_default_prompt_is_byte_identical(monkeypatch, flag):
    seen, task = capture(monkeypatch, flag)
    assert seen["config"].system_prompt == reporter_system_prompt(public_schema=True) + "\n\n" + public_schema_delivery_instruction(task)


@pytest.mark.parametrize("scope", ["other_skill", "different_source", "inline", "schema", "rag", "other_role", "unbound", "custom_system"])
def test_compact_flag_leaves_other_contracts_and_custom_systems_byte_identical(monkeypatch, scope):
    off, _ = capture(monkeypatch, "0", scope)
    on, _ = capture(monkeypatch, "1", scope)
    assert off["config"].system_prompt == on["config"].system_prompt
    assert off["config"].tools == on["config"].tools
    assert off["config"].skills == on["config"].skills
    assert off["config"].max_turns == on["config"].max_turns


def test_owned_compact_template_keeps_execution_public_contract_and_audit_obligations(monkeypatch):
    compact, task = capture(monkeypatch, "1")
    ordinary, _ = capture(monkeypatch, "0")
    prompt = compact["config"].system_prompt
    assert prompt == clause_public_reporter_system_prompt() + "\n\n" + public_schema_delivery_instruction(task)

    def boundary(text):
        return text.split("<stackplanner_boundary>", 1)[1].split("</stackplanner_boundary>", 1)[0]

    assert boundary(prompt) == boundary(ordinary["config"].system_prompt)
    for marker in (
        "ambiguity, inconsistency, misaligned terminology, omission and structural flaws",
        "immutable original task",
        "mandatory user feedback",
        "verbatim",
        "exactly once",
        "native format/semantic/quote feedback",
        "distinct supported finding",
        "partial/blocked",
        "evidence_gaps",
        "successful native write_file",
        "current-action path",
    ):
        assert marker in prompt
    for marker in (
        "required_literal_tokens",
        "required_boundary_adjustments",
        "Markdown tables",
        "H1 title",
        "key findings",
        "Chinese report",
        "percentage-point",
        "threshold",
        "image URL",
        "data-gap assessment",
        'Use artifact_type "report_revision"',
    ):
        assert marker not in prompt
    assert compact["config"].skills == ordinary["config"].skills
    assert compact["config"].tools == ordinary["config"].tools
    assert compact["config"].max_turns == ordinary["config"].max_turns


@pytest.mark.parametrize("damage", [None, "incomplete", "altered", "pending", "over_budget", "no_settings"])
def test_compact_request_preserves_source_ready_and_full_input_budget_authentication(monkeypatch, damage):
    monkeypatch.setenv("SP_CLAUSE_COMPACT_REPORTER", "1")
    monkeypatch.setenv("SP_CLAUSE_SOURCE_READY_NO_REREAD", "1")
    original = 'Original "Information");tion");\nThe full continuation remains visible.'
    source = [
        AIMessage(content="", tool_calls=[{"id": "original", "name": "read_file", "args": {"path": "/mnt/user-data/uploads/document.txt", "start_line": 1, "end_line": 2}}]),
        ToolMessage(content=original, name="read_file", tool_call_id="original"),
    ]
    captured = [message.model_dump() for message in source]
    if damage == "incomplete":
        source = []
    elif damage == "altered":
        source[-1].content = "An untrusted abbreviated replacement."
    elif damage == "pending":
        source.append(AIMessage(content="", tool_calls=[{"id": "pending", "name": "other_tool", "args": {}}]))
    seen = []

    def count(messages, tools, settings, thinking_enabled=None):
        seen.append((messages, tools))
        return 100_000 if damage == "over_budget" else 100

    monkeypatch.setattr("deerflow.sp.source_prefetch.count_source_prompt_tokens", count)
    system = SystemMessage(content=clause_public_reporter_system_prompt())
    request = ModelRequest(
        model=FakeMessagesListChatModel(responses=[AIMessage(content="[]")]), messages=[system, HumanMessage(content="Audit original sources."), *source], tools=[SimpleNamespace(name=name) for name in ("read_file", "grep", "other_tool")]
    )
    before = [message.model_dump() for message in request.messages]
    settings = None if damage == "no_settings" else SourcePrefetchSettings(tokenizer="fixture", context_tokens=8192, response_tokens=1024)
    middleware = SourcePrefetchVisibilityMiddleware(captured, contract=contract(), settings=settings, auditor_role="reporter", skill_names=["clause"])
    result = middleware.wrap_model_call(request, lambda current: current)
    assert [message.model_dump() for message in request.messages] == before
    assert result.messages[0] == system
    assert any(tool.name == "read_file" for tool in result.tools) is (damage is not None)
    if damage is None:
        assert result.messages[-1].name == "sp_clause_source_ready_no_reread"
        assert seen[0][0] == result.messages and seen[0][1] == result.tools
        assert original in [message.content for message in result.messages]
