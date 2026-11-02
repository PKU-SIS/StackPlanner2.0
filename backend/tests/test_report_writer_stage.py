"""Writer-stage scheduling respects late nodes and the experiment cutoff."""

import importlib.util
import sys
from pathlib import Path

scripts = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(scripts))
spec = importlib.util.spec_from_file_location("writer_eval", scripts / "evaluate_report_writer.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sys.path.pop(0)


def test_no_new_dispatch_in_the_last_minute():
    assert module.can_dispatch(100, 200)
    assert not module.can_dispatch(140, 200)
    assert not module.can_dispatch(201, 200)


def test_late_writing_nodes_wait_for_other_nodes():
    tree = {"nodes": [{"node_id": "n1", "constraints": {}}, {"node_id": "n2", "constraints": {"write_last": True}}, {"node_id": "n3", "constraints": {"depends_on": "preceding sections"}}]}
    requests = [{"key": "w1", "node_ids": ["n1"]}, {"key": "w2", "node_ids": ["n2"]}, {"key": "w3", "node_ids": ["n3"]}]
    assert module.writing_waves(requests, tree) == [[requests[0]], requests[1:]]


def test_ready_initial_outline_is_allowed_without_faking_consolidation():
    tree = {"nodes": [{"node_id": "n1", "kind": "writable", "status": "ready"}]}
    ledger = {"gaps": [], "write_requests": [{"node_ids": ["n1"], "status": "pending"}]}
    assert module.accepted_writer_input({"accepted": True}, ledger, tree)
    ledger["gaps"] = [{"gap_id": "g1", "status": "open"}]
    assert not module.accepted_writer_input({"accepted": True}, ledger, tree)
    ledger["gaps"] = []
    ledger["write_requests"] = []
    assert not module.accepted_writer_input({"accepted": True}, ledger, tree)
    assert not module.accepted_writer_input({}, ledger, tree)


def test_resume_requires_existing_current_drafts_and_ready_requests():
    tree = {"nodes": [{"node_id": "n1", "kind": "writable", "status": "ready"}]}
    ledger = {"gaps": [], "write_requests": [{"node_ids": ["n1"], "status": "completed"}], "drafts": [{"node_id": "n1", "is_current": True}]}
    assert module.accepted_writer_input({"outcome": "acceptance_error"}, ledger, tree)
    ledger["drafts"] = []
    assert not module.accepted_writer_input({"outcome": "acceptance_error"}, ledger, tree)
