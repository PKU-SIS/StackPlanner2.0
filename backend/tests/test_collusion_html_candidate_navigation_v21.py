"""Navigate distant candidates without rereading a large JSON character stream."""

import asyncio
import copy
import json

import pytest
from test_collusion_audit import scoped_profile as scoped_profile
from test_collusion_html_candidate_delivery_v21 import Adapter

from app.experiments.collusion_audit.m01_delivery import build_candidate_index_tool


def index_fixture(tmp_path):
    rows = []
    for n in range(1, 401):
        rows.append(
            dict(
                candidate_id=f"C{n:06d}",
                finding_offset=n - 1,
                review_rank=n,
                category_hint="other_passage" if n % 2 else "service_or_implementation",
                non_template_shared_chars=500 - n,
                match_mode="near_copy" if n % 3 else "normalized_exact",
                document_pair=["d001", "d004"] if n <= 390 else ["d007", "d010"],
                path=f"/mnt/user-data/outputs/bid_audit/current-run/m01_candidates/C{n:06d}.json",
            )
        )
    directory = tmp_path / "m01_candidates"
    directory.mkdir()
    (directory / "index.json").write_text(json.dumps({"audit_run_id": "current-run", "candidates": rows}, ensure_ascii=False))
    return rows


def query(tmp_path, **kwargs):
    if isinstance(kwargs.get("document_pair"), list):
        kwargs["document_pair"] = "|".join(kwargs["document_pair"])
    return json.loads(asyncio.run(build_candidate_index_tool(Adapter(tmp_path)).ainvoke({"run_id": "current-run", **kwargs})))


def test_pair_lookup_reaches_tail_without_reading_irrelevant_index(tmp_path):
    rows = index_fixture(tmp_path)
    before = (tmp_path / "m01_candidates/index.json").read_bytes()
    result = query(tmp_path, document_pair=["d010", "d007"], limit=3)
    assert result["candidates"] == rows[390:393]
    assert result["matching_candidate_count"] == 10
    assert result["candidate_count_all"] == 400
    assert result["next_offset"] == 3
    assert result["navigation_only"] is True and result["full_passages_delivered"] is False
    assert len(json.dumps(result, ensure_ascii=False)) < 6000
    assert (tmp_path / "m01_candidates/index.json").read_bytes() == before


def test_all_matching_rows_reachable_once_in_original_rank_order(tmp_path):
    rows = index_fixture(tmp_path)
    actual = []
    offset = 0
    while True:
        result = query(tmp_path, document_pair=["d007", "d010"], offset=offset, limit=3)
        actual.extend(result["candidates"])
        if result["next_offset"] is None:
            break
        offset = result["next_offset"]
    assert actual == rows[390:]
    assert len({r["candidate_id"] for r in actual}) == 10


def test_category_and_match_mode_filters_do_not_change_records(tmp_path):
    rows = index_fixture(tmp_path)
    expected = copy.deepcopy([r for r in rows if r["category_hint"] == "service_or_implementation" and r["match_mode"] == "normalized_exact"])
    result = query(tmp_path, category_hint="service_or_implementation", match_mode="normalized_exact", limit=32)
    delivered = len(result["candidates"])
    assert 0 < delivered <= 32
    assert result["candidates"] == expected[:delivered]
    assert result["next_offset"] == delivered
    assert result["matching_candidate_count"] == len(expected)
    assert result["document_pairs"][1]["candidate_count"] == 10


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": 33}, {"offset": -1}, {"document_pair": ["d001"]}, {"document_pair": ["d001", "d001"]}])
def test_invalid_windows_and_pairs_rejected(tmp_path, kwargs):
    index_fixture(tmp_path)
    with pytest.raises(ValueError):
        query(tmp_path, **kwargs)


def test_other_runs_cannot_be_read(tmp_path):
    index_fixture(tmp_path)
    with pytest.raises(AssertionError):
        asyncio.run(build_candidate_index_tool(Adapter(tmp_path)).ainvoke({"run_id": "another-run"}))


def test_real_profile_registers_navigation_only_in_html(scoped_profile, tmp_path):
    from app.experiments.collusion_audit.m01_delivery import materialize_candidates
    from app.experiments.collusion_audit.profile import CollusionProfile

    options = dict(quality_controls=True, rule_level_review=True)
    markdown = CollusionProfile(scoped_profile.rule, scoped_profile.project, scoped_profile.outputs, tmp_path / "md-cache", scoped_profile.trace, **options)
    assert "audit_m01_index" not in {t.name for t in markdown.tools}
    profile = CollusionProfile(scoped_profile.rule, scoped_profile.project, scoped_profile.outputs, tmp_path / "html-cache", scoped_profile.trace, report_format="html", **options)
    tools = {t.name: t for t in profile.tools}
    receipt = json.loads(asyncio.run(tools["audit_m01"].ainvoke({})))
    root = profile.adapters.result_root(receipt["run_id"])
    findings = [json.loads(line) for line in (root / "findings.jsonl").read_text().splitlines()]
    evidence = [json.loads(line) for line in (root / "evidence.jsonl").read_text().splitlines()]
    materialize_candidates(root, "/mnt/user-data/outputs/bid_audit/" + receipt["run_id"], {"summary": {"run_id": receipt["run_id"]}}, findings, evidence)
    result = json.loads(asyncio.run(tools["audit_m01_index"].ainvoke({"run_id": receipt["run_id"], "document_pair": "d1|d0"})))
    expected = json.loads((root / "m01_candidates/index.json").read_text())
    assert result["candidates"] == expected["candidates"]
    assert result["navigation_only"] and not result["full_passages_delivered"]


def test_navigation_recovery_identity_is_distinct_from_full_passage_receipt(tmp_path):
    from app.experiments.collusion_audit.identifier_provenance import source_reference_tokens
    from app.experiments.collusion_audit.recovery import tool_scope_refs

    index_fixture(tmp_path)
    result = query(tmp_path, document_pair=["d007", "d010"], limit=3)
    index_scope = tool_scope_refs("audit_m01_index", result, {})
    assert len(index_scope) == 1 and next(iter(index_scope)).startswith("code-index:")
    assert source_reference_tokens("audit_m01_index", result, {}) == ()
    assert index_scope.isdisjoint(tool_scope_refs("audit_m01_candidates", {"audit_run_id": "current-run", "candidates": result["candidates"]}, {}))


def test_navigation_transport_remains_bounded_without_clipping_rows(tmp_path):
    original = index_fixture(tmp_path)
    for row in original:
        row["path"] += "/" + "长路径" * 180
    path = tmp_path / "m01_candidates/index.json"
    path.write_text(json.dumps({"audit_run_id": "current-run", "candidates": original}, ensure_ascii=False))
    result = query(tmp_path, document_pair=["d001", "d004"], limit=32)
    count = len(result["candidates"])
    assert 0 < count < 32
    assert len(json.dumps(result, ensure_ascii=False)) <= 10000
    assert result["candidates"] == original[:count]
    assert result["next_offset"] == count
    follow = query(tmp_path, document_pair=["d001", "d004"], offset=count, limit=32)
    assert follow["candidates"][0] == original[count]


def test_scalar_pair_parameter_avoids_ambiguous_optional_array_schema(tmp_path):
    original = index_fixture(tmp_path)
    tool = build_candidate_index_tool(Adapter(tmp_path))
    schema = tool.args_schema.model_json_schema()
    assert schema["properties"]["document_pair"]["type"] == "string"
    assert "anyOf" not in schema["properties"]["document_pair"]
    result = json.loads(asyncio.run(tool.ainvoke({"run_id": "current-run", "document_pair": "d010|d007", "limit": 3})))
    assert result["candidates"] == original[390:393]


def test_serialized_json_array_is_rejected_without_json_repair(tmp_path):
    index_fixture(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(build_candidate_index_tool(Adapter(tmp_path)).ainvoke({"run_id": "current-run", "document_pair": '["d001", "d007"]'}))
