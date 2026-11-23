"""Existing accepted reports import into a new owner/thread without model calls."""

from report_pilot_entry import import_seed, run_turn_sequence
from test_sp_report_turns import accepted

from deerflow.sp.report.store import pointer_path, read_json
from deerflow.sp.report.turns.store import ReportTurnStore, file_manifest


def test_seed_import_preserves_source_and_makes_one_current_origin(tmp_path):
    source = tmp_path / "source"
    ctx, source_store = accepted(source)
    before = file_manifest(source / "report/r1")
    target = ReportTurnStore(tmp_path / "new-outputs", user_id="new-owner", thread_id="new-thread")
    result = import_seed(source, target, tmp_path / "provenance")
    assert file_manifest(source / "report/r1") == before
    assert result["source_sha_unchanged"]
    assert len(target.index()["versions"]) == 1
    assert target.inspect(target.index()["head"])["requirements_status"] == "native_accepted"
    assert read_json(pointer_path(target.outputs))["report_id"] == result["report_id"]
    assert target._accepted(target._paths(result["report_id"]))
    assert result["canonical_sha_unchanged"]


def test_seeded_sequence_skips_original_first_round():
    turns = [{"turn_index": i, "message_id": f"m{i}"} for i in range(2, 8)]
    seen = []
    result = run_turn_sequence(turns, lambda row: seen.append(row["turn_index"]) or {"passed": True}, first_turn=2)
    assert result["completed"] and seen == list(range(2, 8))


def test_inspection_binds_native_normalized_source_id(tmp_path):
    from types import SimpleNamespace

    from report_pilot_entry import inspect_turn

    ctx, store = accepted(tmp_path)
    origin = store.freeze("r1", resolved_query="Accepted requirement")
    turn = store.reuse(origin["turn_id"], message_id="read-query__user", raw_query="Read the accepted report", response="Saved answer", mode="read_only")
    row = inspect_turn(tmp_path, store, {"turn_index": 5, "message_id": "read-query"}, "new-run", "r1", {}, SimpleNamespace(calls={}), set(), source_message_id="read-query__user")
    assert row["passed"]
    assert row["dialogue_turn_id"] == turn["turn_id"]
    assert row["runtime_message_id"] == "read-query__user"


def test_prefix_clone_has_independent_owner_and_preserves_source(tmp_path):
    from report_pilot_entry import import_prefix

    ctx, store = accepted(tmp_path / "old")
    origin = store.freeze("r1", resolved_query="Accepted requirement")
    store.reuse(origin["turn_id"], message_id="read", raw_query="Read only", mode="read_only", response="Saved reply")
    before = file_manifest(store.outputs)
    target = ReportTurnStore(tmp_path / "new", user_id="new-owner", thread_id="new-thread")
    result = import_prefix(store.outputs, target, tmp_path / "prefix-provenance", through=2)
    assert file_manifest(store.outputs) == before
    assert result["starts_at_user_turn"] == 3
    assert len(target.index()["versions"]) == 1
    assert target.index()["head"] == origin["turn_id"]
    assert len(target.index()["requests"]) == 2
    assert target.inspect("t0002")["status"] == "read_only"
    assert target.verify("t0002")["owner"] == target.owner
    assert target._accepted(target._paths(result["report_id"]))


def test_inspection_recovers_host_normalization_with_exact_source_text(tmp_path):
    from types import SimpleNamespace

    from report_pilot_entry import inspect_turn

    ctx, store = accepted(tmp_path)
    origin = store.freeze("r1", resolved_query="Accepted requirement")
    turn = store.reuse(origin["turn_id"], message_id="read-query__user", raw_query="Read exactly this", response="Saved answer", mode="read_only")
    request = {"turn_index": 5, "message_id": "read-query", "query": "Read exactly this"}
    row = inspect_turn(tmp_path, store, request, "new-run", "r1", {}, SimpleNamespace(calls={}), set(), source_message_id="read-query")
    assert row["passed"] and row["dialogue_turn_id"] == turn["turn_id"]
    request["query"] = "Different source text"
    assert not inspect_turn(tmp_path, store, request, "new-run", "r1", {}, SimpleNamespace(calls={}), set())["passed"]
