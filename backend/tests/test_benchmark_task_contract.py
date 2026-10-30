"""Schema/delegation checks only: no SP graph construction or execution."""

from typing import get_type_hints

from langchain_core.messages import HumanMessage

from deerflow.agents.thread_state import ThreadState
from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.actions.handlers.context import build_handler_context_refs
from deerflow.sp.memory import TaskMemoryStack


def test_benchmark_contract_is_a_declared_native_state_channel():
    assert "sp_task_contract" in get_type_hints(ThreadState, include_extras=True)


def test_public_contract_preserves_instructions_beyond_short_user_preview():
    instructions = "Public constraints. " * 200 + "Read /mnt/user-data/uploads/document.txt; return JSON."
    state = {
        "messages": [HumanMessage(content=instructions)],
        "sp_task_contract": {"version": 1, "mode": "benchmark", "authoritative": True, "immutable": True, "original_issue": instructions, "gold": "PRIVATE_LABEL", "metadata": {"category": "PRIVATE_CATEGORY"}},
    }
    refs = build_handler_context_refs(HandlerContext(state=state, stack=TaskMemoryStack(), thread_id="thread", run_id="run"))
    assert len(refs["latest_user_input"]) == 2400
    assert refs["task_contract"]["original_issue"] == instructions
    assert "document.txt" in refs["task_contract"]["original_issue"]
    assert "PRIVATE" not in str(refs)
