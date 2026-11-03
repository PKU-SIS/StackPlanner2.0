"""Numeric citation display is a pure export step, independent of agent review."""

from copy import deepcopy

from deerflow.sp.report.citation_display import render_final_citations


def test_numbers_follow_existing_source_order_and_repeat_consistently():
    table = [
        {"source_id": "S7", "title": "First", "url": "https://example.com/first", "material_versions": [{"material_id": "m8"}]},
        {"source_id": "S181", "title": "Airport paper", "url": "https://example.com/airport"},
    ]
    original = deepcopy(table)
    text = "# Report\n\nClaim [S181]; other [S7]; again [S181].\n\n## 来源\n\n1. [S7] First（material: m8）\n2. [S181] Airport paper\n"
    rendered, numbering = render_final_citations(text, table)
    assert numbering == {"S7": 1, "S181": 2}
    assert "Claim [2]; other [1]; again [2]." in rendered
    assert "[1] First — https://example.com/first\n\n[2] Airport paper" in rendered
    assert "[S181]" not in rendered and "material:" not in rendered
    assert "1. [1]" not in rendered
    assert table == original


def test_display_does_not_rewrite_plain_ids_numbers_or_partial_matches():
    text = "# Report\n\nS181 m195 [100] [S1][S181].\n\n## 来源\n\n1. [S1] One\n2. [S181] Two\n"
    rendered, _ = render_final_citations(text, [{"source_id": "S1", "title": "One"}, {"source_id": "S181", "title": "Two"}])
    assert "S181 m195 [100] [1][2]." in rendered


def test_unrecognized_legacy_layout_is_preserved_without_a_delivery_gate():
    text = "Legacy body [S181] without a host-generated source section."
    assert render_final_citations(text, [{"source_id": "S181"}]) == (text, {})
    normal = "# Report\n\nNo citations.\n\n## 来源\n\n（无引用）\n"
    assert render_final_citations(normal, []) == (normal, {})


def test_escaped_citations_share_source_identity_and_leave_math_untouched():
    from deerflow.sp.report.assembly import _citation_ids
    body = r"Evidence \[S11\], plain [S19], repeat \[S11\]. Math \[x+y\] and [100]."
    assert _citation_ids(body) == {"S11", "S19"}
    rendered, mapping = render_final_citations(body + "\n## 来源\n", [{"source_id": "S11", "title": "One"}, {"source_id": "S19", "title": "Two"}])
    assert mapping == {"S11": 1, "S19": 2}
    assert r"Evidence [1], plain [2], repeat [1]. Math \[x+y\] and [100]." in rendered
    assert "S11" not in rendered and "S19" not in rendered
