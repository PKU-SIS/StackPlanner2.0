"""P2 writer request, draft acceptance, feedback, and disposition transitions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.artifacts import SPArtifactAdapter
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.report import hooks, packets, writing
from deerflow.sp.report import store as report_store
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError
from deerflow.sp.report.tools import build_register_research_request_tool
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus, SPSubagentTask


def _context(tmp_path: Path) -> HandlerContext:
    state = {
        "thread_data": {"outputs_path": str(tmp_path)},
        "messages": [HumanMessage(content="请写一份关于示例公司的报告。", id="m1")],
    }
    ctx = HandlerContext(state=state, stack=TaskMemoryStack(), thread_id="t1", run_id="run1")
    hooks.start_report(ctx, SPAction.create(ActionType.REPLAN, reason="start", task="start", metadata={"report_action": "start"}))
    return ctx


def _paths(ctx: HandlerContext) -> report_store.ReportPaths:
    context = hooks.report_context(ctx.state)
    assert context.outputs_path and context.report_id
    return report_store.ReportPaths(context.outputs_path, context.report_id)


def _tree(
    *,
    write_requests: list[dict] | None = None,
    question: str = "示例公司 FY2024 偿付能力如何？",
    requirements: list[dict] | None = None,
    draft_dispositions: list[dict] | None = None,
    feedback_dispositions: list[dict] | None = None,
) -> dict:
    return {
        "report_title": "示例公司报告",
        "requirements": requirements or [{"id": "R1", "input_id": "u1", "text": "示例公司 FY2024 偿付能力", "hard": True}],
        "nodes": [
            {"node_id": "n1", "parent_id": None, "kind": "section", "title": "偿付能力"},
            {
                "node_id": "n2",
                "parent_id": "n1",
                "kind": "writable",
                "title": "FY2024 指标",
                "question": question,
                "argument": ["读取官方附注"],
                "conclusion": "待证据支持",
                "evidence": [],
                "constraints": {"period": "FY2024"},
                "requirement_ids": ["R1"],
                "relations": [],
                "gap_ids": [],
                "status": "planned",
            },
        ],
        "removed_node_ids": [],
        "write_requests": write_requests if write_requests is not None else [],
        "gap_outcomes": [],
        "feedback_dispositions": feedback_dispositions or [],
        "draft_dispositions": draft_dispositions or [],
        "user_decisions": [],
    }


def _delegate(ctx: HandlerContext, action_id: str, target: str, metadata: dict | None = None) -> tuple[SPAction, SPSubagentTask]:
    action = SPAction.create(
        ActionType.DELEGATE,
        action_id=action_id,
        reason=f"delegate {target}",
        task=f"{target} task",
        target_agent=target,
        stage="reporting" if target == "reporter" else "planning",
        metadata=metadata or {},
    )
    task_meta: dict = {"stage": action.stage}
    refs: dict = {}
    hooks.prepare_delegation(action, ctx, task_meta, refs)
    task = SPSubagentTask(
        action_id=action_id,
        subagent_type=target,
        task=f"{target} task",
        description="test",
        metadata=task_meta,
        context_refs=refs,
        stage=action.stage,
    )
    return action, task


def _outline(ctx: HandlerContext, tree: dict, *, action_id: str = "a-outline") -> object:
    action, task = _delegate(ctx, action_id, "outline", {"report_task": "plan"})
    entry = ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning")
    result = SPSubagentResult(
        status=SPSubagentStatus.COMPLETED,
        result="tree",
        artifact_content=json.dumps(tree, ensure_ascii=False),
        artifact_type="outline",
        artifact_metadata={"completion_status": "complete"},
    )
    return hooks.handle_success(action, ctx, result, task, artifact_adapter=SPArtifactAdapter(), delegate_entry=entry)


def _writer_action(ctx: HandlerContext, key: str, *, action_id: str = "a-writer") -> tuple[SPAction, SPSubagentTask, Path]:
    action, task = _delegate(ctx, action_id, "reporter", {"report_write_key": key})
    assert task.metadata["report_role"] == "writer"
    draft_id = task.metadata["report_draft_id"]
    host = _paths(ctx).draft_file(task.metadata["report_node_id"], draft_id)
    return action, task, host


def _writer_result(content: str | None, task: SPSubagentTask, *, feedback: list[dict] | None = None) -> SPSubagentResult:
    metadata = {
        "node_id": task.metadata["report_node_id"],
        "draft_path": task.metadata["report_draft_path"],
        "completion_status": "blocked" if feedback else "complete",
    }
    if feedback:
        metadata["node_feedback"] = feedback
    return SPSubagentResult(
        status=SPSubagentStatus.COMPLETED,
        result="writer",
        artifact_content=None,
        artifact_type="node_draft",
        artifact_metadata=metadata,
    )


def _accept_writer(ctx: HandlerContext, action: SPAction, task: SPSubagentTask, result: SPSubagentResult, *, action_id: str = "a-writer") -> object:
    entry = ctx.stack.append_delegate("writer", thread_id="t1", run_id="run1", stage="reporting")
    return hooks.handle_success(action, ctx, result, task, artifact_adapter=SPArtifactAdapter(), delegate_entry=entry)


def test_feedback_wanted_success_creates_review_and_completed_request_does_not_requeue(tmp_path):
    ctx = _context(tmp_path)
    tree = _tree(write_requests=[{"key": "w1", "node_ids": ["n2"], "feedback_wanted": True}])
    assert _outline(ctx, tree).next_step == "continue"
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.payload["write_requests"][0]["status"] == "pending"

    action, task, host = _writer_action(ctx, "w1")
    report_store.write_text_atomic(host, "Node two body.\n")
    accepted = _accept_writer(ctx, action, task, _writer_result("Node two body.\n", task))
    assert accepted.next_step == "continue", accepted.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    draft = writing.current_draft(ledger, "n2")
    assert draft and draft["review_status"] == "open"
    assert ledger.payload["write_requests"][0]["status"] == "completed"
    assert ledger.payload["writes_pending"] == []
    assert ledger.payload["draft_review_due"] == [draft["draft_id"]]
    assert "draft review due" in (hooks.finish_block_reason(ctx) or "")
    packet = packets.build_outline_packet(ledger)
    assert packet["drafts"][0]["content"] == "Node two body.\n"
    assert packet["drafts"][0]["path"] == task.metadata["report_draft_path"]

    fingerprint = packet["drafts"][0]["generated_fingerprint"]
    kept = _outline(
        ctx,
        _tree(
            write_requests=[],
            draft_dispositions=[{"draft_id": draft["draft_id"], "node_id": "n2", "action": "keep", "reason": "new spec applies"}],
        ),
        action_id="a-outline-keep",
    )
    assert kept.next_step == "continue", kept.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    draft = writing.current_draft(ledger, "n2")
    assert draft["review_status"] == "none"
    assert draft["confirmed_fingerprint"] == fingerprint

    # A complete tree repeating the same completed key must not enqueue a new write.
    again = _outline(
        ctx,
        _tree(write_requests=[{"key": "w1", "node_ids": ["n2"], "feedback_wanted": True}]),
        action_id="a-outline-again",
    )
    assert again.next_step == "continue", again.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.payload["write_requests"][0]["status"] == "completed"
    assert ledger.payload["writes_pending"] == []
    assert len([item for item in writing.drafts(ledger) if item.get("node_id") == "n2"]) == 1


def test_feedback_only_result_does_not_create_draft_and_needs_research_blocks_resume(tmp_path):
    ctx = _context(tmp_path)
    tree = _tree(write_requests=[{"key": "w2", "node_ids": ["n2"], "feedback_wanted": False}])
    _outline(ctx, tree)
    action, task, _host = _writer_action(ctx, "w2")
    feedback = [{"node_id": "n2", "kind": "material_missing", "message": "缺少比较对象", "material_ids": [], "requirement_ids": ["R1"]}]
    result = _accept_writer(ctx, action, task, _writer_result(None, task, feedback=feedback))
    assert result.next_step == "continue", result.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert writing.drafts(ledger) == []
    assert ledger.payload["write_requests"][0]["status"] == "feedback_pending"
    assert ledger.payload["draft_review_due"] == []
    feedback_record = writing.feedback_entries(ledger)[0]
    assert feedback_record["status"] == "open"

    register = build_register_research_request_tool(paths=_paths(ctx), action_id="a-outline-gap")
    registered = json.loads(
        register.func(
            questions=[
                {
                    "mode": "new",
                    "question": "补充比较对象",
                    "constraints": {},
                    "why_insufficient": "node feedback",
                    "stop_condition": "comparison evidence",
                    "requirement_refs": [{"input_id": "u1", "quote": "示例公司"}],
                    "node_ids": [],
                    "materials_checked": [],
                }
            ],
            tool_call_id="tc-gap",
        )
    )
    gap_id = registered["registered"][0]["gap_id"]
    disposed = _outline(
        ctx,
        _tree(
            write_requests=[],
            feedback_dispositions=[{"feedback_id": feedback_record["feedback_id"], "disposition": "needs_research", "reason": "交给检索", "gap_id": gap_id}],
        ),
        action_id="a-outline-dispose",
    )
    assert disposed.next_step == "continue", disposed.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.payload["write_requests"][0]["status"] == "blocked"
    assert writing.feedback_entries(ledger)[0]["status"] == "handed_off"
    assert ledger.payload["writes_pending"] == []

    blocked = _outline(
        ctx,
        _tree(write_requests=[{"key": "w2", "node_ids": ["n2"], "feedback_wanted": False}]),
        action_id="a-outline-blocked",
    )
    assert blocked.next_step == "error_recoverable"
    assert "waiting for research" in blocked.error

    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    ledger.require_gap(gap_id)["status"] = "resolved"
    ledger.save()
    resumed = _outline(
        ctx,
        _tree(write_requests=[{"key": "w2", "node_ids": ["n2"], "feedback_wanted": False}]),
        action_id="a-outline-resume",
    )
    assert resumed.next_step == "continue", resumed.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.payload["write_requests"][0]["status"] == "pending"
    assert writing.feedback_entries(ledger)[0]["status"] == "closed"


def test_requirement_text_change_requires_draft_disposition_and_keep_is_stable(tmp_path):
    ctx = _context(tmp_path)
    tree = _tree(write_requests=[{"key": "w3", "node_ids": ["n2"], "feedback_wanted": False}])
    _outline(ctx, tree)
    action, task, host = _writer_action(ctx, "w3")
    report_store.write_text_atomic(host, "Body under original requirement.\n")
    _accept_writer(ctx, action, task, _writer_result("Body under original requirement.\n", task))
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    draft = writing.current_draft(ledger, "n2")

    changed_requirements = [{"id": "R1", "input_id": "u1", "text": "示例公司 FY2024 母公司口径偿付能力", "hard": True}]
    missing = _outline(ctx, _tree(write_requests=[], requirements=changed_requirements), action_id="a-outline-missing")
    assert missing.next_step == "error_recoverable"
    assert "draft dispositions required" in missing.error

    kept = _outline(
        ctx,
        _tree(
            write_requests=[],
            requirements=changed_requirements,
            draft_dispositions=[{"draft_id": draft["draft_id"], "node_id": "n2", "action": "keep", "reason": "口径适用于新需求正文"}],
        ),
        action_id="a-outline-keep",
    )
    assert kept.next_step == "continue", kept.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    kept_draft = writing.current_draft(ledger, "n2")
    assert kept_draft["confirmed_fingerprint"] == report_tree_fingerprint(ledger, changed_requirements)
    assert kept_draft["generated_basis"]["node_spec"]["requirement_context"][0]["text"] == "示例公司 FY2024 偿付能力"
    assert kept_draft["generated_basis"]["node_spec"]["question"] == "示例公司 FY2024 偿付能力如何？"
    assert ledger.payload["tree"]["last_changes"]["requirements"]["changed"] == ["R1"]

    # Same confirmed spec can be resubmitted without another keep disposition.
    stable = _outline(ctx, _tree(write_requests=[], requirements=changed_requirements), action_id="a-outline-stable")
    assert stable.next_step == "continue", stable.error


def report_tree_fingerprint(ledger: ReportLedger, requirements: list[dict]) -> str:
    from deerflow.sp.report import tree as report_tree

    tree = _tree(write_requests=[], requirements=requirements)
    return report_tree.node_fingerprints(tree)["n2"]


def test_rewrite_disposition_retires_old_draft_and_queues_new_write(tmp_path):
    ctx = _context(tmp_path)
    tree = _tree(write_requests=[{"key": "w4", "node_ids": ["n2"], "feedback_wanted": False}])
    _outline(ctx, tree)
    action, task, host = _writer_action(ctx, "w4")
    report_store.write_text_atomic(host, "Old body.\n")
    _accept_writer(ctx, action, task, _writer_result("Old body.\n", task))
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    draft = writing.current_draft(ledger, "n2")
    assert draft and draft["review_status"] == "none"
    assert ledger.payload["draft_review_due"] == []

    rewritten = _outline(
        ctx,
        _tree(
            write_requests=[],
            question="示例公司 FY2024 母公司口径偿付能力如何？",
            draft_dispositions=[{"draft_id": draft["draft_id"], "node_id": "n2", "action": "rewrite", "reason": "口径变化"}],
        ),
        action_id="a-outline-rewrite",
    )
    assert rewritten.next_step == "continue", rewritten.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert writing.current_draft(ledger, "n2") is None
    assert writing.draft(ledger, draft["draft_id"])["status"] == "rewritten"
    assert ledger.payload["write_requests"][0]["status"] == "completed"
    assert ledger.payload["writes_pending"] == []

    new_round = _outline(
        ctx,
        _tree(
            write_requests=[{"key": "w4b", "node_ids": ["n2"], "feedback_wanted": False}],
            question="示例公司 FY2024 母公司口径偿付能力如何？",
        ),
        action_id="a-outline-newwrite",
    )
    assert new_round.next_step == "continue", new_round.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.payload["write_requests"][-1]["key"] == "w4b"
    assert ledger.payload["writes_pending"] == ["w4b"]

    new_action, new_task, new_host = _writer_action(ctx, "w4b", action_id="a-writer-rewrite")
    packet = new_task.context_refs["report_packet"]
    assert packet["prior_draft"]["draft_id"] == draft["draft_id"]
    assert packet["prior_draft"]["path"] == draft["virtual_path"]
    assert packet["prior_draft"]["content"] == "Old body.\n"
    assert packet["prior_draft"]["reason"] == "口径变化"
    report_store.write_text_atomic(new_host, "New body.\n")
    accepted = _accept_writer(ctx, new_action, new_task, _writer_result("New body.\n", new_task))
    assert accepted.next_step == "continue", accepted.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    replacement = writing.current_draft(ledger, "n2")
    assert replacement["rewrite_of"] == draft["draft_id"]
    assert writing.draft(ledger, draft["draft_id"])["rewrite_source_consumed_by"] == replacement["draft_id"]
    assert writing.draft(ledger, draft["draft_id"])["is_current"] is False


def test_removed_node_retires_blocked_feedback_request_without_closing_gap(tmp_path):
    ctx = _context(tmp_path)
    _outline(ctx, _tree(write_requests=[{"key": "w9", "node_ids": ["n2"], "feedback_wanted": False}]))
    action, task, _host = _writer_action(ctx, "w9")
    feedback = [{"node_id": "n2", "kind": "material_missing", "message": "缺少比较对象", "material_ids": [], "requirement_ids": ["R1"]}]
    accepted = _accept_writer(ctx, action, task, _writer_result(None, task, feedback=feedback))
    assert accepted.next_step == "continue", accepted.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    feedback_record = writing.feedback_entries(ledger)[0]
    request = ledger.payload["write_requests"][0]
    assert request["status"] == "feedback_pending"

    register = build_register_research_request_tool(paths=_paths(ctx), action_id="a-outline-removed-gap")
    registered = json.loads(
        register.func(
            questions=[
                {
                    "mode": "new",
                    "question": "节点被删除前登记的研究问题",
                    "constraints": {},
                    "why_insufficient": "node feedback",
                    "stop_condition": "evidence",
                    "requirement_refs": [{"input_id": "u1", "quote": "示例公司"}],
                    "node_ids": [],
                    "materials_checked": [],
                }
            ],
            tool_call_id="tc-removed-gap",
        )
    )
    gap_id = registered["registered"][0]["gap_id"]
    removed = _tree(
        write_requests=[],
        feedback_dispositions=[{"feedback_id": feedback_record["feedback_id"], "disposition": "needs_research", "reason": "节点删除，保留研究记录", "gap_id": gap_id}],
    )
    removed["nodes"] = [{"node_id": "n1", "parent_id": None, "kind": "section", "title": "偿付能力"}]
    removed["removed_node_ids"] = ["n2"]
    disposed = _outline(ctx, removed, action_id="a-outline-removed")
    assert disposed.next_step == "continue", disposed.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    request = ledger.payload["write_requests"][0]
    assert request["status"] == "superseded"
    assert request["superseded_reason"] == "node removed from the accepted tree"
    assert request["attempts"][0]["status"] == "feedback"
    assert writing.next_action(request) == "none"
    assert writing.feedback_entries(ledger)[0]["status"] == "handed_off"
    assert ledger.require_gap(gap_id)["status"] == "open"
    assert ledger.payload["writes_pending"] == []


def test_parent_context_change_requires_disposition_and_is_visible(tmp_path):
    ctx = _context(tmp_path)
    tree = _tree(write_requests=[{"key": "w6", "node_ids": ["n2"], "feedback_wanted": False}])
    _outline(ctx, tree)
    action, task, host = _writer_action(ctx, "w6")
    report_store.write_text_atomic(host, "Body.\n")
    _accept_writer(ctx, action, task, _writer_result("Body.\n", task))
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    draft = writing.current_draft(ledger, "n2")
    changed = _tree(write_requests=[])
    changed["nodes"][0]["title"] = "偿付能力（修订）"
    missing = _outline(ctx, changed, action_id="a-outline-parent")
    assert missing.next_step == "error_recoverable"
    assert "draft dispositions required" in missing.error

    kept = _outline(
        ctx,
        {**changed, "draft_dispositions": [{"draft_id": draft["draft_id"], "node_id": "n2", "action": "keep", "reason": "父节点标题变化不影响该段"}]},
        action_id="a-outline-parent-keep",
    )
    assert kept.next_step == "continue", kept.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.payload["tree"]["last_changes"]["nodes"]["title_changed"] == ["n1"]


def test_drop_disposition_retires_draft_without_requeueing(tmp_path):
    ctx = _context(tmp_path)
    tree = _tree(write_requests=[{"key": "w7", "node_ids": ["n2"], "feedback_wanted": False}])
    _outline(ctx, tree)
    action, task, host = _writer_action(ctx, "w7")
    report_store.write_text_atomic(host, "Drop me.\n")
    _accept_writer(ctx, action, task, _writer_result("Drop me.\n", task))
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    draft = writing.current_draft(ledger, "n2")
    dropped = _outline(
        ctx,
        _tree(
            write_requests=[],
            draft_dispositions=[{"draft_id": draft["draft_id"], "node_id": "n2", "action": "drop", "reason": "不再使用"}],
        ),
        action_id="a-outline-drop",
    )
    assert dropped.next_step == "continue", dropped.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert writing.current_draft(ledger, "n2") is None
    assert writing.draft(ledger, draft["draft_id"])["status"] == "dropped"
    assert ledger.payload["write_requests"][0]["status"] == "completed"
    assert ledger.payload["writes_pending"] == []


def test_keep_can_be_recorded_even_when_fingerprint_is_unchanged(tmp_path):
    ctx = _context(tmp_path)
    _outline(ctx, _tree(write_requests=[{"key": "w8", "node_ids": ["n2"], "feedback_wanted": False}]))
    action, task, host = _writer_action(ctx, "w8")
    report_store.write_text_atomic(host, "Stable body.\n")
    _accept_writer(ctx, action, task, _writer_result("Stable body.\n", task))
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    draft = writing.current_draft(ledger, "n2")
    proactive = _outline(
        ctx,
        _tree(
            write_requests=[],
            draft_dispositions=[{"draft_id": draft["draft_id"], "node_id": "n2", "action": "keep", "reason": "主动确认当前稿"}],
        ),
        action_id="a-outline-proactive-keep",
    )
    assert proactive.next_step == "continue", proactive.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert writing.current_draft(ledger, "n2")["keep_reason"] == "主动确认当前稿"


def test_writer_role_assembles_read_write_tools_prompt_and_skill(tmp_path):
    from types import SimpleNamespace

    from deerflow.sp.report.hooks import configure_report_subagent
    from deerflow.sp.report.prompts import REPORT_WRITER_PROMPT
    from deerflow.subagents.config import SubagentConfig

    ctx = _context(tmp_path)
    task = SPSubagentTask(
        action_id="a-role",
        subagent_type="reporter",
        task="write",
        description="write",
        metadata={"report_role": "writer", "report_id": "r1"},
    )
    tools = [SimpleNamespace(name=name) for name in ["read_file", "grep", "glob", "ls", "write_file", "web_search"]]
    config, available, role = configure_report_subagent(
        task,
        state=ctx.state,
        available_tools=tools,
        subagent_config=SubagentConfig(name="sp-reporter", description="reporter"),
    )
    assert role == "writer"
    assert config.system_prompt == REPORT_WRITER_PROMPT
    assert config.skills == ["report-node-writing"]
    assert config.tools == ["read_file", "grep", "glob", "ls", "write_file"]
    assert "web_search" not in config.tools
    assert [tool.name for tool in available] == [tool.name for tool in tools]


def test_writer_thinking_is_explicit_false_while_other_roles_keep_setting():
    from deerflow.sp.runtime import _resolve_report_role_thinking

    assert _resolve_report_role_thinking("writer", True, {}, {}) is False
    assert _resolve_report_role_thinking("writer", True, {"sp_report_writer_thinking_enabled": True}, {}) is False
    assert _resolve_report_role_thinking("outline", True, {}, {}) is True
    assert _resolve_report_role_thinking("researcher", False, {}, {}) is False


def test_writer_executor_exception_releases_request_and_allows_retry(tmp_path):
    from deerflow.sp.actions.handlers.delegate import DelegateHandler

    ctx = _context(tmp_path)
    _outline(ctx, _tree(write_requests=[{"key": "w5", "node_ids": ["n2"], "feedback_wanted": False}]))

    class _ExplodingExecutor:
        def execute(self, task):
            raise RuntimeError("writer executor exploded")

    action = SPAction.create(
        ActionType.DELEGATE,
        action_id="a-write-explode",
        reason="write",
        task="write n2",
        target_agent="reporter",
        stage="reporting",
        metadata={"report_write_key": "w5"},
    )
    with pytest.raises(RuntimeError, match="writer executor exploded"):
        DelegateHandler(executor=_ExplodingExecutor()).handle(action, ctx)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    request = ledger.payload["write_requests"][0]
    assert request["status"] == "technical_failed"
    assert "writer executor exploded" in request["last_error"]
    assert len(request["attempts"]) == 1
    assert request["attempts"][0]["action_id"] == "a-write-explode"
    assert request["attempts"][0]["status"] == "technical_failed"
    assert writing.next_action(request) == "delegate_writer"

    retry_action, retry_task, _retry_host = _writer_action(ctx, "w5", action_id="a-write-retry")
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    request = ledger.payload["write_requests"][0]
    assert request["status"] == "in_flight"
    assert len(request["attempts"]) == 2
    assert [item["action_id"] for item in request["attempts"]] == ["a-write-explode", "a-write-retry"]
    assert request["attempts"][1]["status"] == "in_flight"
    del retry_action, retry_task


def test_writer_token_cap_is_technical_and_existing_draft_is_not_accepted(tmp_path):
    ctx = _context(tmp_path)
    assert _outline(ctx, _tree(write_requests=[{"key": "w-tech", "node_ids": ["n2"], "feedback_wanted": False}])).next_step == "continue"
    action, task, host = _writer_action(ctx, "w-tech", action_id="a-writer-tech")

    report_store.write_text_atomic(host, "Draft exists but the writer was capped.\n")
    result = _writer_result(None, task)
    result.stop_reason = "token_capped"
    result.finish_reason = "length"
    result.artifact_metadata["completion_status"] = "partial"

    response = _accept_writer(ctx, action, task, result, action_id="a-writer-tech")

    assert response.next_step == "error_recoverable"
    assert "token_capped" in (response.error or "")
    assert "completion_status=partial" in (response.error or "")
    assert "draft_exists=True" in (response.error or "")
    assert "node_feedback" in (response.error or "")
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert writing.drafts(ledger) == []
    request = ledger.payload["write_requests"][0]
    assert request["status"] == "technical_failed"
    assert "token_capped" in (request.get("last_error") or "")


def test_outline_requires_explicit_report_task_after_initial_plan(tmp_path):
    ctx = _context(tmp_path)
    assert _outline(ctx, _tree()).next_step == "continue"
    with pytest.raises(ReportValidationError, match="report_task metadata is required"):
        _delegate(ctx, "a-outline-missing-task", "outline")


def test_outline_file_delivery_accepts_complete_tree_from_host_path(tmp_path):
    ctx = _context(tmp_path)
    action, task = _delegate(ctx, "a-outline-file", "outline", {"report_task": "plan"})
    tree = _tree(write_requests=[{"key": "w-file", "node_ids": ["n2"], "feedback_wanted": False}])
    delivery_path = Path(task.metadata["outline_delivery_path"])
    encoded = json.dumps(tree, ensure_ascii=False)
    split = len(encoded) // 2
    report_store.write_text_atomic(delivery_path, encoded[:split])
    with delivery_path.open("a", encoding="utf-8") as handle:
        handle.write(encoded[split:])
    entry = ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning")
    result = SPSubagentResult(
        status=SPSubagentStatus.COMPLETED,
        result="tree in host file",
        artifact_content=None,
        artifact_type="outline",
        finish_reason="length",
        artifact_metadata={
            "completion_status": "complete",
            "delivery_mode": "file",
            "tree_path": str(delivery_path),
        },
    )

    accepted = hooks.handle_success(action, ctx, result, task, artifact_adapter=SPArtifactAdapter(), delegate_entry=entry)

    assert accepted.next_step == "continue", accepted.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.payload["tree"]["version"] == 1
    accepted_tree = json.loads(Path(ledger.payload["tree"]["path"]).read_text(encoding="utf-8"))
    assert accepted_tree["write_requests"][0]["key"] == "w-file"
    for key in ("gap_outcomes", "user_decisions", "feedback_dispositions", "draft_dispositions"):
        assert key in accepted_tree
    assert not ledger.payload.get("rejected_outline")


def test_outline_declared_file_failure_does_not_fallback_to_inline(tmp_path):
    ctx = _context(tmp_path)
    action, task = _delegate(ctx, "a-outline-bad-file", "outline", {"report_task": "plan"})
    delivery_path = Path(task.metadata["outline_delivery_path"])
    report_store.write_text_atomic(delivery_path, '{"report_title": "incomplete"')
    entry = ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning")
    result = SPSubagentResult(
        status=SPSubagentStatus.COMPLETED,
        result="inline tree must not be used",
        artifact_content=json.dumps(_tree(), ensure_ascii=False),
        artifact_type="outline",
        finish_reason="length",
        artifact_metadata={
            "completion_status": "complete",
            "delivery_mode": "file",
            "tree_path": str(delivery_path),
        },
    )

    rejected = hooks.handle_success(action, ctx, result, task, artifact_adapter=SPArtifactAdapter(), delegate_entry=entry)

    assert rejected.next_step == "error_recoverable"
    assert "finish_reason=length" in (rejected.error or "")
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.payload.get("tree") is None
    assert ledger.payload.get("rejected_outline")
