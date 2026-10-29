"""Outline token-cap recovery uses the delegation's current delivery file."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from deerflow.agents.middlewares.read_before_write_middleware import ReadBeforeWriteMiddleware
from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.schema import ActionType, SPAction
from deerflow.sp.agent_tools import SPControlActionMiddleware
from deerflow.sp.artifacts import SPArtifactAdapter
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.report import hooks
from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus, SPSubagentTask
from deerflow.sp.subagents.dr2_adapter import normalize_dr2_subagent_result


def _context(tmp_path: Path) -> HandlerContext:
    state = {
        "thread_data": {"outputs_path": str(tmp_path)},
        "messages": [HumanMessage(content="写一份简报", id="m1")],
    }
    ctx = HandlerContext(state=state, stack=TaskMemoryStack(), thread_id="t1", run_id="run1")
    hooks.start_report(ctx, SPAction.create(ActionType.REPLAN, reason="start", task="start", metadata={"report_action": "start"}))
    return ctx


def _tree() -> dict:
    return {
        "report_title": "恢复候选",
        "requirements": [{"id": "R1", "input_id": "u1", "text": "写一份简报", "hard": True}],
        "nodes": [
            {
                "node_id": "n1",
                "parent_id": None,
                "kind": "writable",
                "title": "简报正文",
                "question": "任务要求是什么？",
                "argument": ["覆盖用户要求"],
                "conclusion": "给出简报。",
                "evidence": [],
                "constraints": {"single_source_only": True},
                "requirement_ids": ["R1"],
                "relations": [],
                "gap_ids": [],
                "status": "planned",
            }
        ],
        "removed_node_ids": [],
        "write_requests": [{"key": "w-n1", "node_ids": ["n1"]}],
        "gap_outcomes": [],
        "user_decisions": [],
        "feedback_dispositions": [],
        "draft_dispositions": [],
    }


def _request(name: str, args: dict, messages=()) -> ToolCallRequest:
    runtime = MagicMock()
    runtime.context = {"thread_id": "t1"}
    return ToolCallRequest(
        tool_call={"name": name, "args": args, "id": f"call-{name}"},
        tool=None,
        state={"messages": list(messages)},
        runtime=runtime,
    )


def _outline_delegate(ctx: HandlerContext) -> tuple[SPAction, SPSubagentTask]:
    action = SPAction.create(
        ActionType.DELEGATE,
        action_id="a-outline-recovery",
        reason="plan report",
        task="plan report",
        target_agent="outline",
        stage="planning",
        metadata={"report_task": "plan"},
    )
    task_metadata = {"stage": "planning"}
    refs: dict = {}
    hooks.prepare_delegation(action, ctx, task_metadata, refs)
    task = SPSubagentTask(
        action_id=action.action_id,
        subagent_type="outline",
        task="plan report",
        description="plan",
        metadata=task_metadata,
        context_refs=refs,
        stage="planning",
    )
    return action, task


def _result() -> SPSubagentResult:
    return SPSubagentResult(
        status=SPSubagentStatus.COMPLETED,
        result="candidate",
        stop_reason="token_capped",
        finish_reason="stop",
        artifact_type="outline",
        artifact_metadata={"completion_status": "partial"},
    )


def _empty_length_result() -> SPSubagentResult:
    return SPSubagentResult(
        status=SPSubagentStatus.COMPLETED,
        result="",
        finish_reason="length",
        artifact_type="outline",
        artifact_metadata={"completion_status": "partial"},
    )


def test_real_sp_delegate_observation_includes_stop_json_and_file(tmp_path):
    ctx = _context(tmp_path)
    tool_call = {
        "name": "sp_delegate",
        "id": "call-real-outline",
        "args": {
            "target_agent": "outline",
            "task": "Recover the final outline",
            "reason": "test real delegate return",
            "stage": "planning",
            "metadata": {"report_task": "plan"},
        },
        "type": "tool_call",
    }
    state = dict(ctx.state)
    state["messages"] = [
        HumanMessage(content="写一份简报", id="m1"),
        AIMessage(content="", tool_calls=[tool_call]),
    ]

    class _PartialExecutor:
        def execute(self, task):
            delivery = Path(str(task.metadata["outline_delivery_path"]))
            delivery.parent.mkdir(parents=True, exist_ok=True)
            delivery.write_text('{"report_title":"partial","nodes":[', encoding="utf-8")
            return SPSubagentResult(
                status=SPSubagentStatus.COMPLETED,
                result="partial",
                stop_reason="token_capped",
                artifact_type="outline",
                artifact_metadata={"completion_status": "partial"},
            )

    middleware = SPControlActionMiddleware(executor_provider=lambda _state, _runtime: _PartialExecutor())
    request = SimpleNamespace(
        tool_call=tool_call,
        state=state,
        runtime=SimpleNamespace(context={"thread_id": "t1", "run_id": "run1"}),
    )
    command = middleware.wrap_tool_call(request, lambda _: (_ for _ in ()).throw(AssertionError("native handler must not run")))
    message = command.update["messages"][0]
    payload = json.loads(message.content)
    error = str(payload["error"])

    assert payload["next_step"] == "error_recoverable"
    assert "stop_reason=token_capped" in error
    assert "cumulative report Outline token budget" in error
    assert "Expecting value" in error
    assert "rejected_candidate:" in error
    assert "/outline/rejected-spact_call-real-outline.json" in error


def test_first_outline_guides_file_chunks_before_any_rejection(tmp_path):
    ctx = _context(tmp_path)
    action, task = _outline_delegate(ctx)
    delivery = task.context_refs["report_packet"]["delivery"]
    assert delivery["mode"] == "optional_file"
    assert "first Outline attempt" in delivery["instruction"]
    assert "append=True" in delivery["instruction"]
    assert task.metadata.get("outline_delivery_required") is False


def test_empty_length_result_reports_no_candidate_without_false_retention(tmp_path):
    ctx = _context(tmp_path)
    action, task = _outline_delegate(ctx)
    result = hooks.handle_success(
        action,
        ctx,
        _empty_length_result(),
        task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning"),
    )

    assert result.next_step == "error_recoverable"
    assert "finish_reason=length" in (result.error or "")
    assert "no candidate was retained" in (result.error or "")
    assert "host file/raw candidate was retained" not in (result.error or "")
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert not ledger.payload.get("rejected_outline")


def test_rejected_outline_forces_file_delivery_on_retry_and_accepts_complete_file(tmp_path):
    ctx = _context(tmp_path)
    action, task = _outline_delegate(ctx)
    rejected = hooks.handle_success(
        action,
        ctx,
        _empty_length_result(),
        task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning"),
    )
    ctx.state.update(rejected.state_update or {})

    retry_action, retry_task = _outline_delegate(ctx)
    assert retry_task.metadata.get("outline_delivery_required") is True
    assert retry_task.context_refs["report_packet"]["delivery"]["mode"] == "required_file"

    inline = SPSubagentResult(
        status=SPSubagentStatus.COMPLETED,
        result="inline tree",
        artifact_content=json.dumps(_tree(), ensure_ascii=False),
        artifact_type="outline",
        artifact_metadata={"completion_status": "complete"},
    )
    missing = hooks.handle_success(
        retry_action,
        ctx,
        inline,
        retry_task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning"),
    )
    assert missing.next_step == "error_recoverable"
    assert "requires file delivery" in (missing.error or "")

    delivery = Path(str(retry_task.metadata["outline_delivery_path"]))
    delivery.parent.mkdir(parents=True, exist_ok=True)
    delivery.write_text(json.dumps(_tree(), ensure_ascii=False), encoding="utf-8")
    file_result = SPSubagentResult(
        status=SPSubagentStatus.COMPLETED,
        result="file tree",
        artifact_content=None,
        artifact_type="outline",
        artifact_metadata={"completion_status": "complete", "delivery_mode": "file"},
    )
    accepted = hooks.handle_success(
        retry_action,
        ctx,
        file_result,
        retry_task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning"),
    )
    assert accepted.next_step == "continue", accepted.error


def test_rejected_candidate_is_copied_to_delivery_and_original_preserved(tmp_path):
    ctx = _context(tmp_path)
    action, task = _outline_delegate(ctx)
    accepted = hooks.handle_success(
        action,
        ctx,
        SPSubagentResult(
            status=SPSubagentStatus.COMPLETED,
            result="accepted base",
            artifact_content=json.dumps(_tree(), ensure_ascii=False),
            artifact_type="outline",
            artifact_metadata={"completion_status": "complete"},
        ),
        task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning"),
    )
    assert accepted.next_step == "continue"

    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    candidate = ledger.paths.directory / "outline" / "rejected-prior-outline.json"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    partial = '{"report_title":"partial","requirements":[],"nodes":['
    candidate.write_text(partial, encoding="utf-8")
    ledger.payload["rejected_outline"] = {
        "path": str(candidate),
        "virtual_path": ledger.paths.virtual(candidate),
        "sha256": hooks.report_store.sha256_text(partial),
        "chars": len(partial),
        "action_id": "prior-outline",
        "kind": "tree_candidate",
        "errors": "token_capped",
    }
    ledger.save()

    revise = SPAction.create(
        ActionType.DELEGATE,
        action_id="a-outline-revise",
        reason="recover retained candidate",
        task="revise report",
        target_agent="outline",
        stage="planning",
        metadata={"report_task": "plan"},
    )
    task_metadata = {"stage": "planning"}
    refs: dict = {}
    hooks.prepare_delegation(revise, ctx, task_metadata, refs)
    packet = refs["report_packet"]
    delivery_path = Path(str(task_metadata["outline_delivery_path"]))

    assert delivery_path.read_bytes() == candidate.read_bytes()
    assert candidate.read_text(encoding="utf-8") == partial
    assert "byte-for-byte copy" in packet["delivery"]["instruction"]
    assert "complete accepted baseline" in packet["delivery"]["instruction"]
    assert "retained progress" in packet["delivery"]["instruction"]
    assert "removed_node_ids" in packet["delivery"]["instruction"]
    assert str(packet["delivery"]["path"]) == ledger.paths.virtual(delivery_path)
    assert task_metadata["rejected_candidate_path"] == str(candidate)
    assert packet["tree"]["version"] == 1

    revise_task = SPSubagentTask(
        action_id=revise.action_id,
        subagent_type="outline",
        task="revise report",
        description="revise",
        metadata=task_metadata,
        context_refs=refs,
        stage="planning",
    )
    no_output = hooks.handle_success(
        revise,
        ctx,
        SPSubagentResult(
            status=SPSubagentStatus.COMPLETED,
            result="no output",
            artifact_content=None,
            artifact_type="outline",
            artifact_metadata={"completion_status": "complete"},
        ),
        revise_task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning"),
    )
    assert no_output.next_step == "error_recoverable"
    current_ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert current_ledger.payload["tree"]["version"] == 1
    assert candidate.read_text(encoding="utf-8") == partial


def test_retained_prefix_read_append_and_strict_accept(tmp_path):
    ctx = _context(tmp_path)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    candidate = ledger.paths.directory / "outline" / "rejected-prior-outline.json"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    full = json.dumps(_tree(), ensure_ascii=False)
    partial = full[:-1]
    candidate.write_text(partial, encoding="utf-8")
    ledger.payload["rejected_outline"] = {
        "path": str(candidate),
        "virtual_path": ledger.paths.virtual(candidate),
        "sha256": hooks.report_store.sha256_text(partial),
        "chars": len(partial),
        "action_id": "prior-outline",
        "errors": "token_capped",
    }
    ledger.save()

    action = SPAction.create(
        ActionType.DELEGATE,
        action_id="a-outline-recovery",
        reason="recover",
        task="recover",
        target_agent="outline",
        stage="planning",
        metadata={"report_task": "plan"},
    )
    metadata: dict = {"stage": "planning"}
    refs: dict = {}
    hooks.prepare_delegation(action, ctx, metadata, refs)
    delivery = Path(str(metadata["outline_delivery_path"]))
    assert delivery.read_text(encoding="utf-8") == partial
    assert candidate.read_text(encoding="utf-8") == partial

    middleware = ReadBeforeWriteMiddleware(
        content_reader=lambda _runtime, _path: delivery.read_text(encoding="utf-8")
    )

    def append_file(request: ToolCallRequest) -> ToolMessage:
        args = request.tool_call["args"]
        content = delivery.read_text(encoding="utf-8") + str(args["content"])
        delivery.write_text(content, encoding="utf-8")
        return ToolMessage(content="OK", tool_call_id=str(request.tool_call["id"]), name="write_file")

    blocked = middleware.wrap_tool_call(
        _request("write_file", {"description": "append", "path": refs["report_packet"]["delivery"]["path"], "content": "}", "append": True}),
        append_file,
    )
    assert blocked.status == "error"
    assert delivery.read_text(encoding="utf-8") == partial

    read_result = middleware.wrap_tool_call(
        _request("read_file", {"description": "read tail", "path": refs["report_packet"]["delivery"]["path"]}),
        lambda _request: ToolMessage(content=partial[-80:], tool_call_id="read-1", name="read_file"),
    )
    assert "deerflow_read_mark" in read_result.additional_kwargs
    appended = middleware.wrap_tool_call(
        _request(
            "write_file",
            {"description": "append", "path": refs["report_packet"]["delivery"]["path"], "content": "}", "append": True},
            messages=[read_result],
        ),
        append_file,
    )
    assert appended.status != "error"
    assert delivery.read_text(encoding="utf-8") == full
    assert json.loads(delivery.read_text(encoding="utf-8"))["report_title"] == "恢复候选"
    assert candidate.read_text(encoding="utf-8") == partial

    task = SPSubagentTask(
        action_id=action.action_id,
        subagent_type="outline",
        task="recover",
        description="recover",
        metadata=metadata,
        context_refs=refs,
        stage="planning",
    )
    with pytest.raises(Exception, match="already exists"):
        hooks.prepare_delegation(action, ctx, {"stage": "planning"}, {})
    assert delivery.read_text(encoding="utf-8") == full

    accepted = hooks.handle_success(
        action,
        ctx,
        SPSubagentResult(
            status=SPSubagentStatus.COMPLETED,
            result="file tree",
            artifact_content=None,
            artifact_type="outline",
            artifact_metadata={"completion_status": "complete", "delivery_mode": "file"},
        ),
        task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning"),
    )
    assert accepted.next_step == "continue", accepted.error
    assert ReportLedger.load_for_context(hooks.report_context(ctx.state)).payload["tree"]["version"] == 1


def test_outside_or_untrusted_candidate_has_no_delivery(tmp_path):
    ctx = _context(tmp_path)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    candidate = tmp_path / "outside.json"
    candidate.write_text('{"report_title":"outside","nodes":[', encoding="utf-8")
    ledger.payload["rejected_outline"] = {
        "path": str(candidate),
        "virtual_path": ledger.paths.virtual(candidate),
        "sha256": hooks.report_store.sha256_text(candidate.read_text(encoding="utf-8")),
        "chars": len(candidate.read_text(encoding="utf-8")),
        "action_id": "prior-outline",
        "kind": "tree_candidate",
        "errors": "token_capped",
    }
    ledger.save()
    action = SPAction.create(
        ActionType.DELEGATE,
        action_id="a-outside",
        reason="recover",
        task="recover",
        target_agent="outline",
        stage="planning",
        metadata={"report_task": "plan"},
    )
    with pytest.raises(Exception, match="outside"):
        hooks.prepare_delegation(action, ctx, {"stage": "planning"}, {})
    assert not (ledger.paths.directory / "outline" / "delivery-a-outside.json").exists()


def test_candidate_copy_failure_leaves_no_false_delivery(tmp_path, monkeypatch):
    ctx = _context(tmp_path)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    candidate = ledger.paths.directory / "outline" / "rejected-prior-outline.json"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    partial = '{"report_title":"partial","nodes":['
    candidate.write_text(partial, encoding="utf-8")
    ledger.payload["rejected_outline"] = {
        "path": str(candidate),
        "virtual_path": ledger.paths.virtual(candidate),
        "sha256": hooks.report_store.sha256_text(partial),
        "chars": len(partial),
        "action_id": "prior-outline",
        "kind": "tree_candidate",
        "errors": "token_capped",
    }
    ledger.save()
    action = SPAction.create(
        ActionType.DELEGATE,
        action_id="a-copy-fail",
        reason="recover",
        task="recover",
        target_agent="outline",
        stage="planning",
        metadata={"report_task": "plan"},
    )
    monkeypatch.setattr(hooks.shutil, "copyfile", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("copy failed")))
    with pytest.raises(Exception, match="could not copy"):
        hooks.prepare_delegation(action, ctx, {"stage": "planning"}, {})
    assert not (ledger.paths.directory / "outline" / "delivery-a-copy-fail.json").exists()
    assert candidate.read_text(encoding="utf-8") == partial


def test_mid_file_error_candidate_is_corrected_not_auto_appended(tmp_path):
    ctx = _context(tmp_path)
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    candidate = ledger.paths.directory / "outline" / "rejected-prior-outline.json"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    broken = json.dumps(_tree(), ensure_ascii=False).replace('"report_title": "恢复候选"', '"report_title": BROKEN', 1)
    candidate.write_text(broken, encoding="utf-8")
    ledger.payload["rejected_outline"] = {
        "path": str(candidate),
        "virtual_path": ledger.paths.virtual(candidate),
        "sha256": hooks.report_store.sha256_text(broken),
        "chars": len(broken),
        "action_id": "prior-outline",
        "kind": "tree_candidate",
        "errors": "mid-file syntax error",
    }
    ledger.save()
    action = SPAction.create(
        ActionType.DELEGATE,
        action_id="a-mid-error",
        reason="recover",
        task="recover",
        target_agent="outline",
        stage="planning",
        metadata={"report_task": "plan"},
    )
    metadata: dict = {"stage": "planning"}
    refs: dict = {}
    hooks.prepare_delegation(action, ctx, metadata, refs)
    delivery = Path(str(metadata["outline_delivery_path"]))
    assert delivery.read_text(encoding="utf-8") == broken
    delivery.write_text(json.dumps(_tree(), ensure_ascii=False), encoding="utf-8")

    task = SPSubagentTask(
        action_id=action.action_id,
        subagent_type="outline",
        task="recover",
        description="recover",
        metadata=metadata,
        context_refs=refs,
        stage="planning",
    )
    accepted = hooks.handle_success(
        action,
        ctx,
        SPSubagentResult(
            status=SPSubagentStatus.COMPLETED,
            result="corrected",
            artifact_content=None,
            artifact_type="outline",
            artifact_metadata={"completion_status": "complete", "delivery_mode": "file"},
        ),
        task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning"),
    )
    assert accepted.next_step == "continue", accepted.error
    assert candidate.read_text(encoding="utf-8") == broken


def test_incomplete_candidate_allows_corrected_delivery(tmp_path):
    ctx = _context(tmp_path)
    action, task = _outline_delegate(ctx)
    accepted = hooks.handle_success(
        action,
        ctx,
        SPSubagentResult(
            status=SPSubagentStatus.COMPLETED,
            result="accepted base",
            artifact_content=json.dumps(_tree(), ensure_ascii=False),
            artifact_type="outline",
            artifact_metadata={"completion_status": "complete"},
        ),
        task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning"),
    )
    assert accepted.next_step == "continue"

    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    candidate = ledger.paths.directory / "outline" / "rejected-prior-outline.json"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    partial = '{"report_title":"partial","nodes":['
    candidate.write_text(partial, encoding="utf-8")
    ledger.payload["rejected_outline"] = {
        "path": str(candidate),
        "virtual_path": ledger.paths.virtual(candidate),
        "sha256": hooks.report_store.sha256_text(partial),
        "chars": len(partial),
        "action_id": "prior-outline",
        "kind": "tree_candidate",
        "errors": "token_capped",
    }
    ledger.save()

    revise = SPAction.create(
        ActionType.DELEGATE,
        action_id="a-outline-revise-update",
        reason="recover retained candidate",
        task="revise report",
        target_agent="outline",
        stage="planning",
        metadata={"report_task": "revise"},
    )
    task_metadata = {"stage": "planning"}
    refs: dict = {}
    hooks.prepare_delegation(revise, ctx, task_metadata, refs)
    delivery_path = Path(str(task_metadata["outline_delivery_path"]))
    corrected = _tree()
    corrected["nodes"][0]["question"] = "recovered improved question"
    delivery_path.write_text(json.dumps(corrected, ensure_ascii=False), encoding="utf-8")
    revise_task = SPSubagentTask(
        action_id=revise.action_id,
        subagent_type="outline",
        task="revise report",
        description="revise",
        metadata=task_metadata,
        context_refs=refs,
        stage="planning",
    )
    result = hooks.handle_success(
        revise,
        ctx,
        SPSubagentResult(
            status=SPSubagentStatus.COMPLETED,
            result="corrected candidate",
            artifact_content=None,
            artifact_type="outline",
            artifact_metadata={"completion_status": "complete", "delivery_mode": "file", "tree_path": str(delivery_path)},
        ),
        revise_task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning"),
    )

    assert result.next_step == "continue", result.error
    current = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    accepted_tree = json.loads(Path(current.payload["tree"]["path"]).read_text(encoding="utf-8"))
    assert accepted_tree["nodes"][0]["question"] == "recovered improved question"


def test_recovered_tool_evidence_from_adapter_is_not_an_outline_candidate(tmp_path):
    ctx = _context(tmp_path)
    action, task = _outline_delegate(ctx)
    recovered = json.dumps(
        {
            "summary": "tool output",
            "artifact_content": json.dumps(_tree(), ensure_ascii=False),
            "artifact_type": "outline",
            "artifact_metadata": {"completion_status": "complete"},
        },
        ensure_ascii=False,
    )
    raw = SimpleNamespace(
        status=SPSubagentStatus.COMPLETED,
        result=recovered,
        result_source="recovered_tool_evidence",
        artifact_content=None,
        artifact_type=None,
        artifact_metadata={},
        stop_reason=None,
        ai_messages=[{"response_metadata": {"finish_reason": "length"}}],
    )
    normalized = normalize_dr2_subagent_result(raw, task=task)
    assert normalized.artifact_content is None

    result = hooks.handle_success(
        action,
        ctx,
        normalized,
        task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning"),
    )

    assert result.next_step == "error_recoverable"
    assert "No tree candidate" in (result.error or "")
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert not ledger.payload.get("rejected_outline")


def test_diagnostic_text_is_not_saved_as_rejected_tree(tmp_path):
    ctx = _context(tmp_path)
    action, task = _outline_delegate(ctx)
    entry = ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning")
    result = _result()
    result.result = "[TOKEN BUDGET EXCEEDED] The total token usage has exceeded the safety limit."
    result.artifact_content = None

    rejected = hooks.handle_success(
        action,
        ctx,
        result,
        task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=entry,
    )

    assert rejected.next_step == "error_recoverable"
    assert "No tree candidate" in (rejected.error or "")
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert not ledger.payload.get("rejected_outline")


def test_incomplete_tree_is_retained_as_candidate_with_parse_error(tmp_path):
    ctx = _context(tmp_path)
    action, task = _outline_delegate(ctx)
    delivery = Path(str(task.metadata["outline_delivery_path"]))
    delivery.parent.mkdir(parents=True, exist_ok=True)
    delivery.write_text('{"report_title":"partial","nodes":[', encoding="utf-8")
    entry = ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning")

    rejected = hooks.handle_success(
        action,
        ctx,
        _result(),
        task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=entry,
    )

    assert rejected.next_step == "error_recoverable"
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    record = ledger.payload.get("rejected_outline")
    assert record is not None
    assert record["kind"] == "tree_candidate"
    assert record["parse_error"]


def test_token_capped_current_delivery_file_recovers_through_full_acceptance(tmp_path):
    ctx = _context(tmp_path)
    action, task = _outline_delegate(ctx)
    delivery = Path(str(task.metadata["outline_delivery_path"]))
    delivery.parent.mkdir(parents=True, exist_ok=True)
    delivery.write_text(json.dumps(_tree(), ensure_ascii=False), encoding="utf-8")
    entry = ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning")

    result = hooks.handle_success(
        action,
        ctx,
        _result(),
        task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=entry,
    )

    assert result.next_step == "continue"
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert ledger.payload["tree"]["version"] == 1
    accepted_file = Path(str(ledger.payload["tree"]["path"]))
    accepted = json.loads(accepted_file.read_text(encoding="utf-8"))
    assert accepted["accepted_with_stop_reason"] == "token_capped"
    events = [event for event in result.run_events if event.get("event_type") == "sp.report.outline_accepted"]
    assert events and events[0]["payload"]["accepted_with_stop_reason"] == "token_capped"


def test_token_capped_without_current_file_still_uses_technical_rejection(tmp_path):
    ctx = _context(tmp_path)
    action, task = _outline_delegate(ctx)
    entry = ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning")

    result = hooks.handle_success(
        action,
        ctx,
        _result(),
        task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=entry,
    )

    assert result.error
    assert "token_capped" in result.error
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert not ledger.payload.get("tree")


def test_token_capped_invalid_current_file_is_not_accepted(tmp_path):
    ctx = _context(tmp_path)
    action, task = _outline_delegate(ctx)
    delivery = Path(str(task.metadata["outline_delivery_path"]))
    delivery.parent.mkdir(parents=True, exist_ok=True)
    delivery.write_text("{not valid json", encoding="utf-8")
    entry = ctx.stack.append_delegate("outline", thread_id="t1", run_id="run1", stage="planning")

    result = hooks.handle_success(
        action,
        ctx,
        _result(),
        task,
        artifact_adapter=SPArtifactAdapter(),
        delegate_entry=entry,
    )

    assert "invalid_report_tree" in (result.error or "") or "JSON syntax error" in (result.error or "")
    assert "Execution stop reason: stop_reason=token_capped" in (result.error or "")
    assert "accepted baseline" in (result.error or "")
    assert "rejected_candidate:" in (result.error or "")
    ledger = ReportLedger.load_for_context(hooks.report_context(ctx.state))
    assert not ledger.payload.get("tree")
