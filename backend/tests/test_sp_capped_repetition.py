"""Observe strong repetition without parsing, repairing or selecting findings."""

import json
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from dcc_harness.data import InputFile, PublicTask
from dcc_harness.preprocessing.output_contracts import public_output_contract
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.sp.actions import SPAction
from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.handlers.delegate import DelegateHandler
from deerflow.sp.capped_repetition import DIAGNOSTIC_KEY, captured_capped_repetition
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.recovery import artifact_array_token_cap_instruction
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus, SPSubagentTask
from deerflow.sp.subagents.dr2_adapter import normalize_dr2_subagent_result

FLAG = "SP_CLAUSE_CAPPED_REPETITION_FEEDBACK"
QUOTE = "The Supplier shall deliver the identified materials on the agreed date, subject to the written acceptance provisions and the separately stated exceptions. " * 5
ENTRY = json.dumps({"text": QUOTE, "explanation": "The stated date conflicts with the specific timing provision elsewhere in this document."})
RAW = "[" + ",".join([ENTRY] * 24) + ',{"text":"An unfinished final passage'


def contract():
    return public_output_contract(PublicTask(benchmark="clause", prompt="Audit all supported issues.", files=(InputFile(name="document.txt", text=QUOTE),)))


def task(*, role="reporter", skills=None, public=None, action_id="audit"):
    return SPSubagentTask(
        action_id=action_id,
        subagent_type=role,
        task="Audit the supplied document.",
        description="Audit",
        context_refs={"task_contract": public or contract()},
        metadata={"skill_names": ["clause"] if skills is None else skills},
        run_id="run",
    )


def result(raw=RAW, *, finish="length", stop=None, messages=None, metadata=None):
    return SimpleNamespace(status="completed", result=raw, stop_reason=stop, ai_messages=messages if messages is not None else [AIMessage(content=raw, response_metadata={"finish_reason": finish}).model_dump()], artifact_metadata=metadata)


def normalize(monkeypatch, *, enabled=True, raw=RAW, current_task=None, **kwargs):
    monkeypatch.setenv(FLAG, "1" if enabled else "0")
    return normalize_dr2_subagent_result(result(raw, **kwargs), task=current_task or task())


def test_real_captured_cap_adds_only_host_facts_and_preserves_every_original_field(monkeypatch):
    original = result()
    before = deepcopy(original.__dict__)
    monkeypatch.delenv(FLAG, raising=False)
    baseline = normalize_dr2_subagent_result(original, task=task())
    assert baseline.capped_output_repetition is None
    monkeypatch.setenv(FLAG, "1")
    observed = normalize_dr2_subagent_result(original, task=task())
    facts = observed.capped_output_repetition
    assert facts is not None and facts["nonoverlapping_occurrences"] >= 8
    offset = facts["first_offsets"][0]
    fragment = RAW[offset : offset + facts["fragment_characters"]]
    assert RAW[: facts["inspected_characters"]].count(fragment) == facts["nonoverlapping_occurrences"]
    for offset in facts["first_offsets"]:
        assert RAW[offset : offset + facts["fragment_characters"]] == fragment
    expected = asdict(baseline)
    actual = asdict(observed)
    actual["capped_output_repetition"] = None
    assert actual == expected
    assert observed.artifact_content == RAW and observed.completion_status == "partial" and observed.stop_reason == "token_capped"
    assert original.__dict__ == before
    assert DIAGNOSTIC_KEY not in observed.artifact_metadata


@pytest.mark.parametrize("finish", ["length", "max_tokens"])
def test_provider_cap_names_are_observed(monkeypatch, finish):
    assert normalize(monkeypatch, finish=finish).capped_output_repetition


@pytest.mark.parametrize("damage", ["disabled", "researcher", "skill", "source", "schema", "no_task", "no_messages", "mismatch", "earlier_cap", "stop", "loop_cap", "tool_call", "file_priority"])
def test_scope_and_actual_final_provenance_negatives(monkeypatch, damage):
    current_task = task()
    raw = result()
    monkeypatch.setenv(FLAG, "0" if damage == "disabled" else "1")
    if damage == "researcher":
        current_task = task(role="researcher")
    elif damage == "skill":
        current_task = task(skills=["other"])
    elif damage in {"source", "schema"}:
        public = contract()
        if damage == "source":
            public["source_requirements"]["files"][0]["path"] = "/mnt/user-data/uploads/other.txt"
        else:
            public["output_schema"]["items"]["additionalProperties"] = True
        current_task = task(public=public)
    elif damage == "no_task":
        current_task = None
    elif damage == "no_messages":
        raw.ai_messages = []
    elif damage == "mismatch":
        raw.ai_messages[-1]["content"] += "different"
    elif damage == "earlier_cap":
        raw.ai_messages.append(AIMessage(content=RAW, response_metadata={"finish_reason": "stop"}).model_dump())
    elif damage == "stop":
        raw.ai_messages[-1]["response_metadata"]["finish_reason"] = "stop"
        raw.stop_reason = "token_capped"
    elif damage == "loop_cap":
        raw.stop_reason = "loop_capped"
    elif damage == "tool_call":
        raw.ai_messages[-1]["tool_calls"] = [{"name": "read_file", "args": {}, "id": "call"}]
    elif damage == "file_priority":
        raw.artifact_metadata = {"created_paths": ["/mnt/user-data/outputs/draft.json"]}
    assert normalize_dr2_subagent_result(raw, task=current_task).capped_output_repetition is None


@pytest.mark.parametrize("raw", ["[" + " " * 20000, "[" + "repeated-short-motif" * 1000, "[" + ",".join(json.dumps({"text": f"Distinct source passage {index} " + QUOTE[:90], "explanation": str(index)}) for index in range(90)), RAW[:4000]])
def test_no_strong_long_fragment_repetition_is_noop(monkeypatch, raw):
    monkeypatch.setenv(FLAG, "1")
    captured = result(raw)
    assert captured_capped_repetition(raw, captured.ai_messages, contract=contract(), skill_names=["clause"], auditor_role="reporter", run_id="run", action_id="audit") is None


def test_model_metadata_cannot_supply_host_facts(monkeypatch):
    fake = {"version": 1, "run_id": "run", "action_id": "audit", "nonoverlapping_occurrences": 9999}
    observed = normalize(monkeypatch, raw="[]", metadata={DIAGNOSTIC_KEY: fake, "capped_output_repetition": fake})
    assert observed.capped_output_repetition is None
    assert DIAGNOSTIC_KEY not in observed.artifact_metadata


def test_shared_quotation_for_different_issues_is_never_host_deduplicated(monkeypatch):
    raw = "[" + ",".join(json.dumps({"text": QUOTE, "explanation": f"A distinct issue with its own supported analysis, index {index}."}) for index in range(24))
    observed = normalize(monkeypatch, raw=raw)
    assert observed.capped_output_repetition
    assert observed.artifact_content == raw
    assert observed.artifact_content.count('"explanation"') == 24


def test_actual_next_recovery_gets_bounded_facts_without_changing_artifact_or_budgets(monkeypatch, tmp_path):
    monkeypatch.setenv(FLAG, "1")
    public = contract()
    limits = {"reporter": 3, "delegate": 3, "capped_delegate": 2}
    state = {
        "thread_data": {"outputs_path": str(tmp_path / "outputs")},
        "sp_loop_run_id": "run",
        "sp_non_interactive": True,
        "sp_task_contract": public,
        "sp_recovery_limits": deepcopy(limits),
        "messages": [HumanMessage(content="Audit original public sources.")],
    }
    calls = []

    def execute(current):
        calls.append(current)
        if len(calls) == 1:
            return normalize_dr2_subagent_result(result(), task=current)
        return SPSubagentResult(status=SPSubagentStatus.FAILED, error="No service in this regression")

    handler = DelegateHandler(executor=SimpleNamespace(execute=execute))
    first = SPAction.create("DELEGATE", target_agent="reporter", task="Audit the document", reason="Audit", stage="reporting", metadata={"skill_names": ["clause"], "budgets": {"max_tokens": 1200}})
    context = HandlerContext(state=state, stack=TaskMemoryStack(), thread_id="thread", run_id="run")
    handled = handler.handle(first, context)
    state.update(handled.state_update)
    state["sp_task_memory"] = context.stack.to_dict()
    refs = deepcopy(state["sp_current_artifact_refs"])
    ref = next(value for value in refs.values() if isinstance(value, dict) and value.get("artifact_id"))
    path = next(tmp_path.rglob(Path(ref["virtual_path"]).name))
    original_bytes = path.read_bytes()
    assert original_bytes == RAW.encode()
    previous = context.stack.entries[-1]
    assert previous.metadata["completion_status"] == "partial" and previous.metadata["stop_reason"] == "token_capped"
    assert previous.metadata[DIAGNOSTIC_KEY]["artifact_id"] == ref["artifact_id"]
    feedback = artifact_array_token_cap_instruction(state)
    assert "clause-capped-output-repetition" in feedback
    assert "not a count of findings" in feedback and "different issues sharing the same quotation" in feedback
    assert QUOTE not in feedback and len(feedback) < 4000
    from deerflow.sp.agent_tools import SPFinishAvailabilityMiddleware

    assert "clause-capped-output-repetition" in SPFinishAvailabilityMiddleware._artifact_recovery_instruction(state)
    ordinary_cap = deepcopy(state)
    for current in ordinary_cap["sp_current_artifact_refs"].values():
        if isinstance(current, dict) and current.get("artifact_id") == ref["artifact_id"]:
            current["metadata"]["stop_reason"] = "token_capped"
    monkeypatch.setenv(FLAG, "0")
    original_guidance = artifact_array_token_cap_instruction(ordinary_cap)
    assert "each distinct supported issue exactly once" in original_guidance
    monkeypatch.setenv(FLAG, "1")
    assert artifact_array_token_cap_instruction(ordinary_cap) == original_guidance + feedback
    action = SPAction.create(
        "DELEGATE",
        target_agent="reporter",
        task="Verify all distinct supported issues",
        reason="Recover",
        stage="revision",
        metadata={"revision_reason": "Resolve the existing native gaps", "skill_names": ["clause"], "budgets": {"max_tokens": 1200}},
    )
    handler.handle(action, HandlerContext(state=state, stack=context.stack, thread_id="thread", run_id="run"))
    assert len(calls) == 2 and "clause-capped-output-repetition" in calls[1].task
    assert calls[0].budgets == calls[1].budgets == {"max_tokens": 1200}
    assert state["sp_recovery_limits"] == limits and state["sp_task_contract"] == public
    assert state["sp_current_artifact_refs"] == refs and path.read_bytes() == original_bytes
    for change in ("disabled", "newer_observation", "wrong_action", "wrong_run", "changed_artifact", "bad_count", "completed"):
        changed = deepcopy(state)
        if change == "disabled":
            monkeypatch.setenv(FLAG, "0")
        elif change == "newer_observation":
            stack = TaskMemoryStack.from_dict(changed["sp_task_memory"])
            stack.append_observe("A different incomplete attempt", actor="reporter", run_id="run", metadata={"completion_status": "partial"})
            changed["sp_task_memory"] = stack.to_dict()
        else:
            entry = changed["sp_task_memory"]["entries"][-1]
            facts = entry["metadata"][DIAGNOSTIC_KEY]
            if change == "wrong_action":
                facts["action_id"] = "other"
            elif change == "wrong_run":
                facts["run_id"] = "old"
            elif change == "changed_artifact":
                facts["artifact_id"] = "other"
            elif change == "bad_count":
                facts["nonoverlapping_occurrences"] = 99999
            else:
                entry["metadata"]["completion_status"] = "complete"
        assert "clause-capped-output-repetition" not in artifact_array_token_cap_instruction(changed)
        monkeypatch.setenv(FLAG, "1")
