"""General regressions for cropped clauses and unverified public origins."""

from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def vendor(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "vendor/bid_audit_tools"))


def test_cropped_undertaking_is_not_aligned_to_a_declaration():
    from bid_audit.reference_context import ReferenceCatalog, reference_compatibility

    query = "单位）郑重承诺，根据本国产品标准相关政策的规定，本公司（单位）提供的符合本国产品标准的产品成"
    declaration = "本公司（单位）郑重声明，根据本国产品标准相关政策的规定，本公司（单位）提供的以下产品属于本国产品"
    undertaking = "本公司（单位）郑重承诺，根据本国产品标准相关政策的规定，本公司（单位）提供的符合本国产品标准的产品成本之和占全部产品成本之和的比例达到80%以上"

    def b(t, p):
        return {"text": t, "page": p, "physical_pages": [p]}

    assert reference_compatibility(query, declaration)["status"] == "different_explicit_purpose"
    best = ReferenceCatalog({"t": [b(declaration, 4), b(undertaking, 5)]}).lookup(query)[0]
    assert best["reference_source"]["page"] == 5
    assert best["query_fully_explained_by_literal_template"]
    assert best["scope"].startswith("Only this quoted unit")


def test_common_reference_chooses_same_purpose_over_closer_other_form():
    from bid_audit.source_quality import common_baseline_edits

    query = "单位）郑重承诺，根据本国产品标准相关政策的规定，本公司（单位）提供的符合本国产品标准的产品成"
    declaration = "本公司（单位）郑重声明，根据本国产品标准相关政策的规定，本公司（单位）提供的以下产品属于本国产品"
    undertaking = "本公司（单位）郑重承诺，根据本国产品标准相关政策的规定，本公司（单位）提供的符合本国产品标准的产品成本之和占全部产品成本之和的比例达到80%以上"

    def b(t, p):
        return {"text": t, "page": p, "physical_pages": [p]}

    observations = []
    rows = common_baseline_edits({"a": [b(query, 2)], "b": [b(query, 3)]}, {"t": [b(declaration, 4), b(undertaking, 5)]}, source_observations=observations)
    assert not rows or all(r["reference_source"]["page"] == 5 for r in rows)
    assert observations and observations[0]["reference_matches"][0]["reference_source"]["page"] == 5


def test_decision_card_keeps_the_actual_reference_location():
    from app.experiments.collusion_audit.review_quality import decision_source_card

    navigation = {
        "common_reference_differences": {
            "source_observation_index": [
                {
                    "offset": 0,
                    "document_ids": ["a", "b"],
                    "shared_reference_edits": [{"shared": "承诺", "reference": "声明"}],
                    "selected_reference": {"document_id": "t", "physical_pages": [5], "quote": "对应承诺原文"},
                    "reference_candidates": [{"reference_document_id": "t", "reference_source": {"physical_pages": [6], "text": "另一个待核来源"}}],
                }
            ]
        }
    }
    result = decision_source_card(navigation)
    assert result and result[0]["reference_source_locations"][0]["physical_pages"] == [5]
    assert result[0]["reference_correspondence_verified"] is False


def test_requirement_scope_summary_never_promotes_own_column_to_tender():
    from bid_audit.source_quality import VERSION, enrich

    ctx = SimpleNamespace(by_id={"a": {"sha256": "s"}}, documents=lambda *roles: [])
    data = {"requirement_copy_pairs": [{"kind": "same_own_requirement_copy", "requirement_reference_verified": False}, {"kind": "same_own_requirement_copy", "requirement_reference_verified": True}]}
    out = enrich(ctx, "M03", data, {"a": {"source_view_version": VERSION, "source_sha256": "s", "chunks": []}})
    scope = out["requirement_origin_scope_observations"][0]
    assert scope["public_source_verified_pairs"] == 1 and scope["bid_stated_requirement_only_pairs"] == 1
    assert scope["all_requirements_verified_as_tender_text"] is False
    assert out["requirement_copy_pairs"][0]["requirement_reference_verified"] is False
