"""Public benchmark inputs for the native StackPlanner graph.

Task loading and labels remain outside the agent. Only the explicit public
column allowlist is carried from metadata into its prompt.
"""

from __future__ import annotations

import json

from dcc_harness.data import Task, load_tasks, validate_disjoint

__all__ = ["Task", "load_tasks", "prepare_task", "validate_disjoint"]


def prepare_task(task: Task) -> str:
    from .benchmark_plugins import resolve_dataset_plugin

    return resolve_dataset_plugin(task.benchmark).prepare_task(task.public_input())


def _prepare_cuad_task(task: Task) -> str:
    return (
        task.prompt
        + '\n\nSource contract: /mnt/user-data/uploads/contract.txt. Read the uploaded source. Return a JSON array of verbatim answer spans, or objects with "text" and "confidence" (0 to 1). Use [] if no span answers the question.'
    )


def _prepare_widesearch_task(task: Task) -> str:
    columns = task.metadata.get("answer_columns")
    # evaluation.required is the public output schema; never forward evaluation
    # criteria, gold, source manifests, or arbitrary private metadata.
    if not columns:
        columns = task.metadata.get("evaluation", {}).get("required", [])
    if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
        raise ValueError("public answer_columns must be a list of strings")
    return (
        task.prompt
        + "\n\nReturn the complete answer table as Markdown or a JSON array of row objects. Preserve original source values. "
        + (
            "Use exactly these output columns: "
            + json.dumps(columns, ensure_ascii=False)
            + ". "
            "Do not add other answer columns, including a row-number/index column such as #, No., or 序号 unless it is explicitly listed above. "
            if columns
            else ""
        )
        + (
            "Provide source citations for each row separately outside the table, keyed by identifying values. "
            "Do not append citation markers to source field values. "
            "Before FINISH, check the actual final delivery (including any artifact selected by final_artifact_ref): "
            "it must contain the complete filtered answer with exactly the requested columns. "
            "Execute the research and answer delivery in this run; a task brief, scope confirmation, outline, or successful connectivity probe alone does not complete the user's request. "
            "A specialist completing its delegated step does not mean the overall benchmark task is complete. "
            "Raw API responses, research notes, and unfiltered source artifacts are intermediate evidence, not final answers. "
            "If an intermediate artifact uses raw source fields, project/rename those fields to the requested columns and apply the question's filters before selecting the final artifact; preserve source cell values. "
            "For JSON, deliver an array of row objects directly, not an object wrapping rows or an API metadata/results envelope. "
            "For Markdown, deliver one answer table; put auxiliary counts and verification notes in prose or bullet lists, not additional tables. "
            "If the selected artifact does not meet this contract, continue organizing the result through the available native tools before FINISH."
            " Submit the final table through the native FINISH action; merely writing a plain assistant response does not finalize a benchmark delivery. "
            "If evidence remains unavailable, report that failure honestly; do not select a diagnostic probe, challenge page, or planning artifact as if it were the requested answer."
        )
    )
