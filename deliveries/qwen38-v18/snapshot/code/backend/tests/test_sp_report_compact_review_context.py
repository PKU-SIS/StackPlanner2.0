"""Drop duplicate/unused inline text while retaining originals and native bindings."""

import json
from pathlib import Path

from test_sp_report_p3 import _seed

from deerflow.sp.report import assembly, packets, store
from deerflow.sp.report.length import normalize_contract


def test_compact_review_inlines_only_bound_materials_and_one_body_copy(tmp_path):
    _, ledger = _seed(tmp_path, with_sources=True, body="A supported observation [S1].\n", node_evidence=[{"material_id": "m1", "source_ids": ["S1"], "locator": {"lines": [1, 1]}, "use": "Required fact"}])
    # Enough original source text to expose the difference without provider calls.
    material = ledger.material("m2")
    original = Path(material["path"]).read_bytes()
    assembly.ensure_candidate(ledger)
    ordinary = packets.build_integrate_review_packet(ledger)
    assert {m["material_id"] for m in ordinary["materials"]} == {"m1", "m2"}
    assert "body" in ordinary["drafts"][0]
    ledger.payload["length_contract"] = normalize_contract({"compact_outline": True})
    tree = json.loads(Path(ledger.payload["tree"]["path"]).read_text())
    tree["nodes"][1]["constraints"].update(target_words=100, max_words=200)
    tree_path = Path(ledger.payload["tree"]["path"])
    store.write_json_atomic(tree_path, tree)
    ledger.payload["tree"]["sha256"] = store.sha256_text(tree_path.read_text())
    ledger.save()
    assembly.ensure_candidate(ledger)
    compact = packets.build_integrate_review_packet(ledger)
    assert [m["material_id"] for m in compact["materials"]] == ["m1"]
    assert "body" not in compact["drafts"][0]
    assert compact["candidate"]["content"] == ordinary["candidate"]["content"]
    assert compact["drafts"][0]["path"] == ordinary["drafts"][0]["path"]
    assert compact["drafts"][0]["sha256"] == ordinary["drafts"][0]["sha256"]
    assert compact["material_catalog_path"] == ledger.paths.virtual(ledger.paths.catalog)
    assert Path(material["path"]).read_bytes() == original
    assert assembly.review_binding_error(ledger, require_ready=False) is not None  # no fake accepted review


def test_source_only_citation_retains_versions_when_no_material_is_selected(tmp_path):
    _, ledger = _seed(tmp_path, with_sources=True, body="A supported observation [S1].\n")
    ledger.payload["length_contract"] = normalize_contract({"compact_outline": True})
    tree_path = Path(ledger.payload["tree"]["path"])
    tree = json.loads(tree_path.read_text())
    tree["nodes"][1]["constraints"].update(target_words=100, max_words=200)
    store.write_json_atomic(tree_path, tree)
    ledger.payload["tree"]["sha256"] = store.sha256_text(tree_path.read_text())
    ledger.save()
    assembly.ensure_candidate(ledger)
    packet = packets.build_integrate_review_packet(ledger)
    assert {m["material_id"] for m in packet["materials"]} == {"m1", "m2"}
