"""An unchanged initial tree cannot count as accepted consolidation."""

import importlib.util
import sys
from pathlib import Path

scripts = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(scripts))
spec = importlib.util.spec_from_file_location("supplement_eval", scripts / "evaluate_report_supplement.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sys.path.pop(0)


def test_requires_new_revision_and_complete_write_coverage():
    tree = {"nodes": [{"node_id": "n1", "kind": "writable"}, {"node_id": "n2", "kind": "writable"}]}
    ledger = {"gaps": [{"gap_id": "g1", "status": "awaiting_outline"}], "write_requests": [{"node_ids": ["n1"], "status": "pending"}]}
    result = module.consolidation_metrics(tree, ledger, accepted_revision=False)
    assert not result["consolidation_ok"]
    assert result["missing_write_nodes"] == ["n2"]
    assert result["unclosed_gaps"] == ["g1"]
    ledger["gaps"][0]["status"] = "resolved"
    ledger["write_requests"].append({"node_ids": ["n2"], "status": "pending"})
    assert not module.consolidation_metrics(tree, ledger, accepted_revision=False)["consolidation_ok"]
    assert module.consolidation_metrics(tree, ledger, accepted_revision=True)["consolidation_ok"]


def test_cancelled_tasks_and_user_gaps_remain_visible():
    result = module.consolidation_metrics({"nodes": [{"node_id": "n1", "kind": "writable"}]}, {"gaps": [{"gap_id": "g1", "status": "awaiting_user"}], "write_requests": [{"node_ids": ["n1"], "status": "cancelled"}]}, accepted_revision=True)
    assert result["missing_write_nodes"] == ["n1"]
    assert result["awaiting_user_gaps"] == ["g1"]
    assert not result["consolidation_ok"]
