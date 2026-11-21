"""Small Writer scope projected from the native Outline, without a second schema."""

from copy import deepcopy

from deerflow.sp.report.length import node_budget


def build_task_card(node, ledger):
    """Keep required work and budgets; never reject a plan for card formatting.

    Evidence stays in the native Writer packet with original locators and source
    access. Do not duplicate raw passages or ask Outline to select them again.
    The native tree validator owns evidence and requirement coverage checks.
    """
    constraints = node.get("constraints") or {}
    return {
        "node_id": node["node_id"],
        "question": node.get("question"),
        "tasks": deepcopy(node.get("argument") or []),
        "conclusion": node.get("conclusion"),
        "constraints": {key: deepcopy(value) for key, value in constraints.items() if key not in {"writer_tasks", "writer_framework", "paragraph_plan", "target_words", "max_words"}},
        **node_budget(node),
    }
