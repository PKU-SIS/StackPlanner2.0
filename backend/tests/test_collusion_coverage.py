"""Reading, judgement and completion must remain distinct."""

import pytest

from app.experiments.collusion_audit.coverage import ReviewLedger


def ledger(tmp_path):
    r = ReviewLedger(tmp_path)
    r.add_unit("r0", {"kind": "parameter", "requirement": "at least 10", "response": "8", "source_refs": ["E1"]})
    return r


def test_read_is_not_review(tmp_path):
    r = ledger(tmp_path)
    r.mark_seen(["E1"])
    assert r.status()["reviewed"] == 0 and r.status()["unreviewed"] == 1


def test_commit_requires_visible_source_and_real_unit(tmp_path):
    r = ledger(tmp_path)
    item = {"unit_id": "r0", "judgement": "负偏离", "comparison": "8 < 10, actual shortfall", "evidence_refs": ["E1"]}
    with pytest.raises(ValueError):
        r.commit([item])
    r.mark_seen(["E1"])
    r.commit([item])
    assert r.status()["complete"]
    assert r.commit([item])["added"] == 0
    with pytest.raises(ValueError):
        r.commit([{**item, "unit_id": "fake"}])


def test_atomic_rejection_does_not_count_partial_batch(tmp_path):
    r = ledger(tmp_path)
    r.mark_seen(["E1"])
    good = {"unit_id": "r0", "judgement": "负偏离", "comparison": "8 less than 10", "evidence_refs": ["E1"]}
    with pytest.raises(ValueError):
        r.commit([good, {**good, "unit_id": "fake"}])
    assert r.status()["reviewed"] == 0


def test_does_not_repair_or_accept_label_only_judgement(tmp_path):
    r = ledger(tmp_path)
    r.mark_seen(["E1"])
    with pytest.raises(ValueError):
        r.commit([{"unit_id": "r0", "judgement": "无偏离"}])


def test_record_conflict_needs_explicit_revision(tmp_path):
    r = ledger(tmp_path)
    r.mark_seen(["E1"])
    good = {"unit_id": "r0", "judgement": "负偏离", "comparison": "8 less than 10", "evidence_refs": ["E1"]}
    r.commit([good])
    with pytest.raises(ValueError):
        r.commit([{**good, "judgement": "满足"}])
    r.commit([{**good, "judgement": "无法判断", "comparison": "unit differs and cannot resolve"}], replace=True)
    assert r.status()["judgements"] == {"无法判断": 1}


def test_discovered_rows_require_current_read_source(tmp_path):
    from types import SimpleNamespace

    r = ledger(tmp_path)
    r.calculation_registered = True
    k = SimpleNamespace(rows=lambda doc: [{"id": "c1", "positions": [[1, 0, 1, 0, 1]]}])
    manifest = {"documents": [{"id": "d1", "role": "bid", "bidder": "A", "round": "1"}]}
    item = {"requirement": "at least10", "response": "8", "source_refs": ["d1:c1"]}
    with pytest.raises(ValueError):
        r.add_discovered("d1", [item], k, manifest)
    r.mark_seen(["d1:c1"])
    ids = r.add_discovered("d1", [item], k, manifest)
    assert len(ids) == 1 and r.status()["expected"] == 2
    assert r.add_discovered("d1", [item], k, manifest) == ids and r.status()["expected"] == 2
    with pytest.raises(ValueError):
        r.add_discovered("foreign", [item], k, manifest)
