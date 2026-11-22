"""Offline-only verification of a historical report's freeze/fork/resume.

Use PYTHONPATH=<new checkout>/backend/packages/harness:<new checkout>/backend.
The source is read only. No model, network client, or service is started.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from langchain_core.messages import HumanMessage

from deerflow.runtime.user_context import get_effective_user_id
from deerflow.sp.actions.handlers.base import HandlerContext
from deerflow.sp.memory import TaskMemoryStack
from deerflow.sp.report import hooks
from deerflow.sp.report.assembly import final_binding_error
from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.report.store import ReportPaths, read_text, write_json_atomic
from deerflow.sp.report.turns.adapter import activate
from deerflow.sp.report.turns.store import ReportTurnStore, file_manifest


def validate(source: Path, work: Path, report_id: str) -> dict:
    source, work = source.resolve(), work.absolute()
    if (
        work.exists()
        or work.resolve().is_relative_to(source)
        or source.is_relative_to(work.resolve())
    ):
        raise ValueError("work directory must be new and separate from source")
    source_store = ReportTurnStore(
        source, user_id=get_effective_user_id(), thread_id="offline-validator"
    )
    original = ReportPaths(source, report_id)
    before = file_manifest(original.directory)
    original_ledger = source_store._accepted(original)
    store = ReportTurnStore(
        work, user_id=get_effective_user_id(), thread_id="offline-validator"
    )
    # Import into a fresh archive without writing anything under source.
    with store.locked():
        index = store.index()
        turn, _ = store._allocate(
            index,
            message_id="historical-origin",
            raw_query=read_text(original.request),
            resolved_query=read_text(original.request),
            mode="origin",
        )
        store._snapshot(turn, original)
        turn.update(status="accepted", report_id=report_id)
        store._record(turn)
        index["head"] = turn["turn_id"]
        index["active_version"] = turn["turn_id"]
        index["versions"][turn["turn_id"]] = {
            "report_id": report_id,
            "base_version_id": None,
        }
        store._save(index)
    restored = store.restore(turn["turn_id"])
    restored_ledger = ReportLedger.load(restored)
    error = final_binding_error(restored_ledger)
    if error:
        raise AssertionError(error)
    candidate = store.prepare(
        turn["turn_id"],
        message_id="offline-followup",
        raw_query="Offline lifecycle verification; do not regenerate report",
        resolved_query=read_text(original.request),
    )
    state = {
        "thread_data": {"outputs_path": str(work)},
        "messages": [
            HumanMessage(
                content=candidate["raw_query"],
                id="offline-followup",
                additional_kwargs={
                    "report_dialogue": {"enabled": True, "report_id": report_id}
                },
            )
        ],
    }
    context = HandlerContext(
        state=state,
        stack=TaskMemoryStack(),
        thread_id="offline-validator",
        run_id="offline-validation",
    )
    activate(store, candidate, context)
    copied = ReportLedger.load_for_context(hooks.report_context(state))
    assert copied.payload["status"] == "active"
    assert (
        copied.payload.get("final_report") is None
        and copied.payload.get("delivered") is None
    )
    assert copied.payload.get("length_contract") == original_ledger.payload.get(
        "length_contract"
    )
    canonical = [
        name
        for name in before
        if name.startswith(("drafts/", "materials/", "user_inputs/"))
        and name != "materials/catalog.md"
    ]
    fork_files = file_manifest(copied.paths.directory)
    assert all(fork_files[name] == before[name] for name in canonical)
    store.end(
        candidate["turn_id"],
        status="cancelled",
        diagnostic={"reason": "Offline lifecycle check only; no model invoked"},
    )
    assert store.index()["head"] == turn["turn_id"]
    assert file_manifest(original.directory) == before
    result = {
        "source_outputs": str(source),
        "work_directory": str(work),
        "source_file_count": len(before),
        "preserved_canonical_files": len(canonical),
        "source_sha_unchanged": True,
        "restored_native_final_binding_error": error,
        "forked_report_id": copied.paths.report_id,
        "native_resume_succeeded": True,
        "old_delivery_not_reused_as_new": True,
        "length_contract_inherited": True,
        "cancel_did_not_advance_head": True,
        "model_calls": 0,
    }
    write_json_atomic(work / "validation.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-outputs", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--report-id", default="r1")
    args = parser.parse_args()
    print(
        json.dumps(
            validate(args.source_outputs, args.work_dir, args.report_id),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
