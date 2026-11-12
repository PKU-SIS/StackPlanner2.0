"""Persistent MinerU knowledge has exact source and per-document scopes."""

import json

import pytest

from app.experiments.collusion_audit.knowledge import DEFAULT_OPTIONS, KnowledgeStore, fingerprint, order_chunks


def test_mineru_is_explicit_without_generated_summaries():
    p = DEFAULT_OPTIONS["parser_config"]
    assert "mineru" in p["layout_recognize"].lower()
    assert p["chunk_token_num"] == 512 and p["auto_keywords"] == p["auto_questions"] == 0
    assert not p["raptor"]["use_raptor"] and not p["graphrag"]["use_graphrag"]


def test_identity_changes_with_source_or_parser():
    assert fingerprint({"sha": "a"}) != fingerprint({"sha": "b"})
    assert fingerprint({"x": 1, "y": 2}) == fingerprint({"y": 2, "x": 1})


def test_order_uses_page_position_not_api_or_id_order():
    rows = [{"id": "z", "content": "first", "positions": [[1, 0, 100, 20, 30]]}, {"id": "a", "content": "later", "positions": [[2, 0, 100, 2, 3]]}]
    result = order_chunks(list(reversed(rows)))
    assert [r["id"] for r in result] == ["z", "a"]
    with pytest.raises(ValueError, match="position"):
        order_chunks([{"id": "x", "content": "unlocated"}])


def test_store_continuous_read_and_scope(tmp_path):
    snap = tmp_path / "d001"
    snap.mkdir()
    rows = order_chunks([{"id": "c1", "content": "alpha", "positions": [[1, 0, 1, 0, 1]]}, {"id": "c2", "content": "beta", "positions": [[2, 0, 1, 0, 1]]}])
    (snap / "chunks.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (tmp_path / "index.json").write_text(json.dumps({"ready": True, "documents": {"d001": {"dataset_id": "kb1", "document_id": "doc1", "snapshot": "d001", "source_sha256": "sha"}}}))
    store = KnowledgeStore(tmp_path)
    assert store.read("d001", 0, 1)["next_offset"] == 1
    assert store.read("d001", 1, 1)["items"][0]["content"] == "beta"
    with pytest.raises(ValueError):
        store.read("other", 0, 1)
    with pytest.raises(ValueError):
        store.validate_hits("d001", [{"document_id": "foreign"}])
    with pytest.raises(ValueError):
        store.read("d001", -1, 1)


def test_mineru_scope_never_shares_a_dataset(tmp_path):
    (tmp_path / "index.json").write_text(json.dumps({"ready": True, "documents": {"a": {"dataset_id": "same", "document_id": "one", "snapshot": "a"}, "b": {"dataset_id": "same", "document_id": "two", "snapshot": "b"}}}))
    with pytest.raises(ValueError, match="one document"):
        KnowledgeStore(tmp_path)


def test_textless_chunks_are_preserved_as_explicit_gaps():
    row = {"id": "image", "content": "", "image_id": "source-image", "positions": [[3, 0, 1, 0, 1]]}
    result = order_chunks([row])
    assert result[0]["content"] == "" and result[0]["content_state"] == "image_only_or_empty"
    assert result[0]["id"] == "image"
