"""Valid judgement batches remain atomic without an arbitrary30-row cap."""

import pytest

from app.experiments.collusion_audit.coverage import ReviewLedger


def make_batch(tmp_path, count):
    ledger = ReviewLedger(tmp_path)
    records = []
    for i in range(count):
        ledger.add_unit(f"r{i}", {"kind": "parameter", "source_refs": [f"E{i}"]})
        records.append({"unit_id": f"r{i}", "judgement": "满足", "comparison": "response10 equals requirement10", "evidence_refs": [f"E{i}"]})
    ledger.mark_seen(f"E{i}" for i in range(count))
    return ledger, records


@pytest.mark.parametrize("count", [40, 80, 762])
def test_full_valid_batch_has_no_page_size_submission_limit(tmp_path, count):
    ledger, records = make_batch(tmp_path, count)
    assert ledger.commit(records)["added"] == count
    assert ledger.status()["complete"]
    assert ledger.commit(records)["added"] == 0


def test_large_batch_still_rejects_last_bad_source_atomically(tmp_path):
    ledger, records = make_batch(tmp_path, 40)
    records[-1]["evidence_refs"] = ["unseen"]
    with pytest.raises(ValueError, match="Evidence"):
        ledger.commit(records)
    assert ledger.status()["reviewed"] == 0
