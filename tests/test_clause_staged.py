import hashlib
from types import SimpleNamespace as NS

import pytest

from dcc_harness.datasets.clause_delivery import FileDeliveryPolicy as ClausePolicy
from dcc_harness.datasets.clause_policy import validated_answer


def record(text="[]", path="review.json"):
    digest = hashlib.sha256(text.encode()).hexdigest()
    return dict(
        status="completed",
        native_returned=True,
        delivery_source="finish_artifact",
        run_id="run",
        thread_id="thread",
        delivery=text,
        delivery_sha256=digest,
        final_artifact=dict(
            run_id="run",
            thread_id="thread",
            is_current=True,
            artifact_id="a",
            virtual_path="/mnt/user-data/outputs/" + path,
            observed_sha256=digest,
            metadata={
                "completion_status": "complete",
                "registered_existing_file": True,
            },
        ),
    )


@pytest.mark.parametrize(
    "path,text",
    [
        ("full_text.txt", "contract"),
        ("revision.md", "[]"),
        ("a.json", "{}"),
        ("../a.json", "[]"),
    ],
)
def test_strict_artifact(path, text):
    with pytest.raises(ValueError):
        validated_answer(record(text, path))


def test_empty_requires_native_identity():
    row = record()
    assert validated_answer(row) == []
    row["final_artifact"]["thread_id"] = "old"
    with pytest.raises(ValueError):
        validated_answer(row)


def test_clause_prefix_comes_from_the_unique_source_passage():
    from dcc_harness.datasets.clause_tools import locate_passages

    src = "See section 8.2 above.   4.7(a) Delivery is due.\n(b) Goods are sealed.\n5.1 Common words.\nCommon words."
    result = locate_passages(
        src,
        [
            "Delivery is due.",
            "4.7(a) Delivery is due.",
            "Goods are sealed.",
            "Common words.",
            "above.",
        ],
    )
    assert [row.get("missing_clause_prefix") for row in result] == [
        "4.7(a) ",
        None,
        "(b) ",
        None,
        None,
    ]


def test_monitor_and_validator_agree_on_wrong_files(tmp_path):
    import json
    from scripts.monitor_clause_run import inspect_run

    rows = [
        dict(record("[]", name), task_id=str(i))
        for i, name in enumerate(("document.txt", "revision.md", "answer.json"))
    ]
    (tmp_path / "predictions.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )
    stats = inspect_run(tmp_path, tmp_path / "exports")
    assert stats["valid"] == 1 and set(stats["invalid"]) == {"0", "1"}
    assert [p.name for p in (tmp_path / "exports").iterdir()] == ["2.json"]


def test_quote_check_long_list_and_missing_candidate():
    from dcc_harness.datasets.clause_tools import locate_passages

    long_quote = "quoted passage " * 800
    out = locate_passages(
        "section 1\n" + long_quote, [long_quote, "missing", "section 1"]
    )
    assert [(r["found"], r["start_line"]) for r in out] == [
        (True, 2),
        (False, None),
        (True, 1),
    ]


def test_provider_encoded_passage_list():
    from dcc_harness.datasets.clause_tools import locate_passages

    assert locate_passages("A.\nB.", '["B.", "absent"]') == locate_passages(
        "A.\nB.", ["B.", "absent"]
    )


def test_file_changed_after_finish_is_rejected():
    row = record()
    row["final_artifact"]["metadata"]["clause_file_sha256"] = hashlib.sha256(
        b"old file"
    ).hexdigest()
    with pytest.raises(ValueError, match="digest"):
        validated_answer(row)


def test_whitespace_difference_is_located_without_rewriting_the_answer():
    from dcc_harness.datasets.clause_tools import locate_passages

    source = "One.\nPayment is\n\ndue tomorrow.\nEnd."
    row = locate_passages(source, ["Payment is due tomorrow."])[0]
    assert row["found"] is False and row["comparison"] == "whitespace_only"
    assert (
        source[row["source_start"] : row["source_end"]] == "Payment is\n\ndue tomorrow."
    )


@pytest.mark.parametrize("writer", ["reporter", "coder"])
@pytest.mark.parametrize("method", ["file", "unlisted_file", "envelope"])
@pytest.mark.parametrize("with_research", [True, False])
def test_central_can_finish_directly_or_delegate_research(
    tmp_path, writer, method, with_research
):
    from deerflow.sp.actions import SPAction, build_default_action_router
    from deerflow.sp.actions.run_policy import active_run_policy
    from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus
    from dcc_harness.evaluation.benchmarks import extract_delivery

    answer = '[{"text":"Shipment is due.","explanation":"Conflicting deadlines."}]'
    executed = []

    class Replay:
        def execute(self, task):
            executed.append(task.subagent_type)
            assert "review_phase" not in task.metadata
            meta = {"completion_status": "complete"}
            if task.subagent_type == "researcher":
                assert "Survey deadlines" in task.task
                body = "Two deadlines found."
            else:
                assert "Review and deliver" in task.task
                assert {"read_file", "write_file"} <= set(task.allowed_tools)
                filename = f"review-{task.action_id}.json"
                assert filename in task.task
                if method in {"file", "unlisted_file"}:
                    (tmp_path / filename).write_text(answer)
                    if method == "file":
                        meta["created_paths"] = ["/mnt/user-data/outputs/" + filename]
                    body = None
                else:
                    meta["filename"] = filename
                    body = answer
            return SPSubagentResult(
                status=SPSubagentStatus.COMPLETED,
                task_id=task.action_id,
                result="Completed assignment.",
                artifact_content=body,
                artifact_metadata=meta,
            )

    router = build_default_action_router(delegate_executor=Replay())
    state = {"thread_data": {"outputs_path": str(tmp_path)}}

    def dispatch(kind, **args):
        result = router.execute(
            SPAction.create(kind, reason="Central chose this action.", **args),
            state=state,
            thread_id="thread",
            run_id="run",
        )
        state.update(result.state_update)
        return result

    from dcc_harness.datasets.clause import create_plugin

    token = active_run_policy.set(create_plugin().run_policy())
    try:
        if with_research:
            assert not dispatch(
                "DELEGATE",
                target_agent="researcher",
                stage="research",
                task="Survey deadlines in the agreement.",
            ).error
        result = dispatch(
            "DELEGATE",
            target_agent=writer,
            stage="reporting" if writer == "reporter" else "implementation",
            task="Review and deliver the complete JSON answer.",
        )
        assert not result.error, result.error
        refs = [
            v
            for k, v in state["sp_current_artifact_refs"].items()
            if k != "_history" and v["virtual_path"].endswith(".json")
        ]
        [ref] = refs
        assert ref["type"] == "generated_file"
        finish = dispatch(
            "FINISH",
            task="Deliver selected file.",
            metadata={
                "final_artifact_ref": ref["artifact_id"],
                "required_artifact_type": "generated_file",
            },
        )
        assert finish.next_step == "finish", finish.error
        actual, ref = extract_delivery(
            state, thread_id="thread", run_id="run", native_returned=True
        )
        assert actual == answer and ref["metadata"]["registered_existing_file"]
        row = record(actual)
        row.update(thread_id="thread", run_id="run", final_artifact=ref)
        assert validated_answer(row)[0]["text"] == "Shipment is due."
        assert executed == (["researcher"] if with_research else []) + [writer]
    finally:
        active_run_policy.reset(token)


@pytest.mark.parametrize(
    "partial,body", [(True, "[]"), (False, "markdown: []"), (False, "{}")]
)
def test_explicit_fallback_never_registers_partial_or_invalid_json(
    tmp_path, partial, body
):
    policy = ClausePolicy()
    task = NS(action_id="a", task="Review", subagent_type="reporter", metadata={})
    ctx = NS(state={"thread_data": {"outputs_path": str(tmp_path)}})
    policy.configure_task(task, ctx)
    md = {
        "completion_status": "partial" if partial else "complete",
        "filename": "review-a.json",
    }
    policy.prepare_result(
        task, NS(artifact_content=body, artifact_metadata=md, stop_reason=None), ctx
    )
    assert not (tmp_path / "review-a.json").exists() and "clause_file_sha256" not in md
    assert md["completion_status"] == "partial"


def test_undeclared_intermediate_array_is_not_converted_to_an_answer(tmp_path):
    policy = ClausePolicy()
    task = NS(action_id="a", task="Review", subagent_type="researcher", metadata={})
    ctx = NS(state={"thread_data": {"outputs_path": str(tmp_path)}})
    policy.configure_task(task, ctx)
    res = NS(
        artifact_content="[]",
        artifact_metadata={"completion_status": "complete"},
        stop_reason=None,
    )
    policy.prepare_result(task, res, ctx)
    assert res.artifact_content == "[]" and not list(tmp_path.iterdir())


@pytest.mark.parametrize("bad", ["preexisting", "symlink", "incomplete"])
def test_cannot_adopt_stale_unsafe_or_partial_file(tmp_path, bad):
    policy = ClausePolicy()
    task = NS(
        action_id="a",
        task="Review the document.",
        subagent_type="reporter",
        metadata={},
    )
    ctx = NS(state={"thread_data": {"outputs_path": str(tmp_path)}})
    dest = tmp_path / "review-a.json"
    if bad == "preexisting":
        dest.write_text("[]")
        with pytest.raises(ValueError, match="already exists"):
            policy.configure_task(task, ctx)
        return
    policy.configure_task(task, ctx)
    if bad == "symlink":
        other = tmp_path / "review-other.json"
        other.write_text("[]")
        dest.symlink_to(other)
    else:
        dest.write_text("[]")
    md = {"completion_status": "complete" if bad == "symlink" else "partial"}
    policy.prepare_result(
        task, NS(artifact_metadata=md, stop_reason=None, artifact_content=None), ctx
    )
    assert md["completion_status"] == "partial" and "clause_file_sha256" not in md


@pytest.mark.parametrize(
    "assignment,expected,body,status",
    [
        (
            "Review the entire contract. Save the result as a UTF-8 .json file under /mnt/user-data/outputs/.",
            "A UTF-8 .json file under /mnt/user-data/outputs/ containing only a JSON array.",
            None,
            "partial",
        ),
        ("Deliver the final JSON answer.", None, "unfinished prose", "partial"),
        ("Review the contract.", "The final JSON array", "[]", "complete"),
        (
            "Write the final JSON file.",
            None,
            '[{"text":"1. Payment is due.","explanation":"Different deadlines."}]',
            "complete",
        ),
        (
            "Verify that the JSON file exists and report its path.",
            "File path, item count and validation status of the JSON array",
            None,
            "complete",
        ),
        (
            "Collect potential issues for the final JSON.",
            "Candidate list",
            None,
            "complete",
        ),
        (
            "Do not write the final JSON file. Return observations.",
            None,
            None,
            "complete",
        ),
        (
            "Return a JSON array of provisional candidates.",
            "JSON array of candidates",
            None,
            "complete",
        ),
        ("Return a JSON schema for another agent.", "A JSON schema", None, "complete"),
        (
            "Deliver one registered UTF-8 .json file under /mnt/user-data/outputs/.",
            None,
            None,
            "partial",
        ),
        (
            "Review this contract.",
            "A registered UTF-8 .json file under /mnt/user-data/outputs/ containing only the answer.",
            None,
            "partial",
        ),
    ],
)
def test_explicit_delivery_receipt(tmp_path, assignment, expected, body, status):
    policy = ClausePolicy()
    task = NS(
        action_id="r",
        task=assignment,
        expected_output=expected,
        subagent_type="coder",
        metadata={},
    )
    ctx = NS(state={"thread_data": {"outputs_path": str(tmp_path)}})
    policy.configure_task(task, ctx)
    res = NS(
        result="I will write the result now.",
        artifact_content=body,
        artifact_metadata={"completion_status": "complete"},
        completion_status="complete",
        stop_reason=None,
    )
    policy.prepare_result(task, res, ctx)
    assert res.artifact_metadata["completion_status"] == res.completion_status == status
    if status == "partial":
        assert (
            "review-r.json" in res.result
            and "JSON array" in res.artifact_metadata["evidence_gaps"][0]
        )
        assert not res.artifact_metadata["created_paths"]
    elif body:
        assert (tmp_path / "review-r.json").read_text() == body
    else:
        assert not (tmp_path / "review-r.json").exists()
