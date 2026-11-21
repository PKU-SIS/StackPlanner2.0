"""Memory prompts must keep one-task constraints out of durable preferences."""

from deerflow.agents.memory.prompt import FACT_EXTRACTION_PROMPT, MEMORY_UPDATE_PROMPT


def test_memory_update_prompt_defines_task_scope():
    assert "Task Scope and Stability" in MEMORY_UPDATE_PROMPT
    assert "Re-running or repeating the same task is not evidence" in MEMORY_UPDATE_PROMPT
    assert "current request's explicit requirements always" in MEMORY_UPDATE_PROMPT


def test_fact_extraction_prompt_does_not_promote_task_constraints():
    assert "task-specific source, length, format" in FACT_EXTRACTION_PROMPT
    assert "do not turn them into general preferences" in FACT_EXTRACTION_PROMPT
