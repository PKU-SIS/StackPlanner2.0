"""Keep capped draft bytes while excluding their automatic revision baseline."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

from deerflow.sp.actions import SPAction
from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.handlers.delegate import DelegateHandler
from deerflow.sp.artifacts import SPArtifactAdapter
from deerflow.sp.memory import StackMemoryEntry, TaskMemoryStack
from deerflow.sp.recovery import artifact_array_token_cap_instruction
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus
from deerflow.sp.subagents.dr2_adapter import render_sp_subagent_prompt


def _state(tmp_path):
    return {
        "thread_data": {"outputs_path": str(tmp_path / "outputs")},
        "sp_loop_run_id": "run",
        "sp_task_contract": {"delivery_mode": "artifact", "output_schema": {"type": "array"}},
        "messages": [HumanMessage(content="Audit every original source and return the public JSON array.")],
    }


def _write(adapter, state, body, *, status="partial", stop="token_capped", run="run", author="reporter", **kwargs):
    result = adapter.write_text_artifact(
        body,
        artifact_type="report_revision",
        state=state,
        thread_id="thread",
        run_id=run,
        created_by=author,
        metadata={"target_agent": author, "completion_status": status, "stop_reason": stop, **kwargs.pop("metadata", {})},
        **kwargs,
    )
    state.update(result.state_update)
    return result


def _delegate(state, *, refs=(), target="reporter"):
    tasks = []

    def execute(task):
        tasks.append(task)
        return SPSubagentResult(status=SPSubagentStatus.FAILED, error="No model service in this regression")

    action = SPAction.create("DELEGATE", target_agent=target, task="Revise the original audit", reason="Resolve native gaps", stage="revision", input_refs=list(refs), metadata={"revision_reason": "Resolve the remaining native audit gaps."})
    DelegateHandler(executor=SimpleNamespace(execute=execute)).handle(action, HandlerContext(state=state, stack=TaskMemoryStack.from_dict(state.get("sp_task_memory")), thread_id="thread", run_id="run"))
    assert len(tasks) == 1
    return tasks[0]


@pytest.mark.parametrize("status", ["partial", "blocked"])
def test_actual_next_reporter_omits_only_transient_capped_body_and_can_read_original(tmp_path, status):
    adapter = SPArtifactAdapter()
    state = _state(tmp_path)
    body = '[{"text":"EXACT ORIGINAL QUOTE","explanation":"UNVERIFIED-REPEATED-DRAFT"},' * 700 + '"DISTINCT-TAIL-FINDING"'
    written = _write(adapter, state, body, status=status)
    refs_before = deepcopy(state["sp_current_artifact_refs"])
    raw_before = Path(written.metadata.workspace_path).read_bytes()

    task = _delegate(state, refs=[written.metadata.artifact_id])
    item = task.context_refs["artifact_bodies"]["items"][0]
    assert item["content_omitted"] is True
    assert item["canonical"] is False
    assert item["completion_status"] == status
    assert item["stop_reason"] == "token_capped"
    assert "noncanonical" in item["content"]
    assert "original public sources" in item["content"]
    assert item["virtual_path"] == written.metadata.virtual_path
    assert "UNVERIFIED-REPEATED-DRAFT" not in item["content"]
    assert "each distinct supported issue exactly once" in task.task
    assert written.metadata.artifact_id in task.task
    assert written.metadata.virtual_path in render_sp_subagent_prompt(task)
    assert state["sp_current_artifact_refs"] == refs_before
    assert Path(written.metadata.workspace_path).read_bytes() == raw_before
    assert adapter.read_text_artifact(refs_before["report_revision"], state=state, thread_id="thread", max_chars=len(body) + 1) == (body, False)


@pytest.mark.parametrize(
    "status,stop,run,author",
    [("partial", None, "run", "reporter"), ("blocked", "loop_capped", "run", "reporter"), ("complete", None, "run", "reporter"), ("partial", "token_capped", "old-run", "reporter"), ("partial", "token_capped", "run", "researcher")],
)
def test_ordinary_partial_complete_other_run_and_other_author_keep_baseline(tmp_path, status, stop, run, author):
    adapter = SPArtifactAdapter()
    state = _state(tmp_path)
    body = "A valid baseline with a genuinely distinct supported finding and its original quotation."
    written = _write(adapter, state, body, status=status, stop=stop, run=run, author=author)
    task = _delegate(state, refs=[written.metadata.artifact_id])
    item = task.context_refs["artifact_bodies"]["items"][0]
    assert item["content"] == body
    assert not item.get("content_omitted")
    assert "public-array-token-cap-recovery" not in task.task


@pytest.mark.parametrize("interruption", ["loop_capped", "error", "compacted"])
def test_unresolved_artifact_identity_keeps_hint_after_noncap_observation_or_error(tmp_path, interruption):
    adapter = SPArtifactAdapter()
    state = _state(tmp_path)
    capped = _write(adapter, state, "Original incomplete draft")
    stack = TaskMemoryStack()
    if interruption != "compacted":
        stack.append(
            StackMemoryEntry(
                actor="reporter",
                action="error" if interruption == "error" else "observe",
                content="A later native recovery did not complete",
                run_id="run",
                metadata={"completion_status": "partial", "stop_reason": interruption if interruption != "error" else None},
            )
        )
    state["sp_task_memory"] = stack.to_dict()
    instruction = artifact_array_token_cap_instruction(state)
    assert capped.metadata.artifact_id in instruction
    assert "each distinct supported issue exactly once" in instruction


def test_new_valid_partial_keeps_its_body_and_capped_ancestor_hint_until_clean_complete(tmp_path):
    adapter = SPArtifactAdapter()
    state = _state(tmp_path)
    capped = _write(adapter, state, "OLD-CAPPED-DRAFT" * 4000)
    valid_body = "A new distinct verified finding, with the original source quotation; another source is unresolved."
    partial = _write(adapter, state, valid_body, stop=None, parent_artifact_ids=[capped.metadata.artifact_id], metadata={"source_artifact_ids": [capped.metadata.artifact_id]})
    task = _delegate(state, refs=[partial.metadata.artifact_id])
    items = {item["artifact_id"]: item for item in task.context_refs["artifact_bodies"]["items"]}
    assert items[partial.metadata.artifact_id]["content"] == valid_body
    assert items[capped.metadata.artifact_id]["content_omitted"] is True
    assert capped.metadata.artifact_id in task.task

    complete = _write(adapter, state, "A complete newly verified audit.", status="complete", stop=None, parent_artifact_ids=[partial.metadata.artifact_id])
    assert artifact_array_token_cap_instruction(state) == ""
    final_task = _delegate(state, refs=[complete.metadata.artifact_id])
    assert "public-array-token-cap-recovery" not in final_task.task
    assert next(item for item in final_task.context_refs["artifact_bodies"]["items"] if item["artifact_id"] == complete.metadata.artifact_id)["content"] == "A complete newly verified audit."


def test_unrelated_or_previously_completed_capped_artifact_does_not_activate_hint(tmp_path):
    adapter = SPArtifactAdapter()
    state = _state(tmp_path)
    _write(adapter, state, "Old capped work")
    _write(adapter, state, "Completed audit", status="complete", stop=None)
    _write(adapter, state, "A later independent partial audit", stop=None)
    assert artifact_array_token_cap_instruction(state) == ""


def test_new_complete_artifact_does_not_inherit_stale_capped_observation(tmp_path):
    adapter = SPArtifactAdapter()
    state = _state(tmp_path)
    capped = _write(adapter, state, "Old capped work")
    stack = TaskMemoryStack()
    stack.append_observe("Prior native token cap", actor="reporter", run_id="run", result_ref=capped.metadata.artifact_id, metadata={"completion_status": "partial", "stop_reason": "token_capped"})
    state["sp_task_memory"] = stack.to_dict()
    _write(adapter, state, "New complete report", status="complete", stop=None)
    assert artifact_array_token_cap_instruction(state) == ""


def test_native_stop_reason_alias_is_supported_without_changing_other_specialist_preview(tmp_path):
    adapter = SPArtifactAdapter()
    state = _state(tmp_path)
    written = _write(adapter, state, "Unmodified capped original", stop=None, metadata={"subagent_stop_reason": "token_capped"})
    reporter = _delegate(state, refs=[written.metadata.artifact_id])
    assert reporter.context_refs["artifact_bodies"]["items"][0]["content_omitted"] is True
    researcher = _delegate(state, refs=[written.metadata.artifact_id], target="researcher")
    assert researcher.context_refs["artifact_bodies"]["items"][0]["content"] == "Unmodified capped original"


def test_reporter_system_distinguishes_noncanonical_cap_recovery_from_normal_baseline():
    from deerflow.subagents.builtins.sp_specialists import REPORTER_CONFIG

    prompt = REPORTER_CONFIG.system_prompt
    assert "noncanonical" in prompt
    assert "original public sources" in prompt
    assert "overrides the ordinary prior-report baseline" in prompt
