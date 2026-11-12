"""HTML audit source transport never silently replaces candidate text with previews."""

import asyncio
import json

from test_collusion_audit import scoped_profile as scoped_profile

from app.experiments.collusion_audit.m01_delivery import build_candidate_tool


class Adapter:
    def __init__(self, root, report_format="html"):
        self.root = root
        self.report_format = report_format

    def result_root(self, run_id):
        assert run_id == "current-run"
        return self.root

    async def recorded(self, name, args, invoke):
        return json.dumps(invoke(), ensure_ascii=False)


def create(root, number, length):
    cid = f"C{number:06d}"
    row = {"candidate_id": cid, "candidate": {"text_a": "甲" * length, "text_b": "乙" * length}, "evidence": [{"quote": "完整来源"}]}
    path = root / "m01_candidates" / f"{cid}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(row, ensure_ascii=False))
    return cid, row


def invoke(adapter, ids):
    tool = build_candidate_tool(adapter)
    return json.loads(asyncio.run(tool.ainvoke({"run_id": "current-run", "candidate_ids": ids})))


def test_html_keeps_complete_candidates_and_declares_remaining_ids(tmp_path):
    first, a = create(tmp_path, 1, 20000)
    second, b = create(tmp_path, 2, 20000)
    result = invoke(Adapter(tmp_path), [first, second])
    assert result["candidates"] == [a]
    assert result["remaining_candidate_ids"] == [second]
    assert result["complete_requested_set"] is False
    assert len(json.dumps(result, ensure_ascii=False)) <= 60000
    follow = invoke(Adapter(tmp_path), [second])
    assert follow["candidates"] == [b] and follow["complete_requested_set"] is True


def test_oversized_single_candidate_has_exact_source_path_and_no_partial_quote(tmp_path):
    cid, row = create(tmp_path, 1, 40000)
    result = invoke(Adapter(tmp_path), [cid])
    assert result["candidates"] == []
    reference = result["oversized_candidate_refs"][0]
    assert reference["candidate_id"] == cid
    assert reference["path"].endswith("/m01_candidates/" + cid + ".json")
    assert reference["character_count"] == len(json.dumps(row, ensure_ascii=False))
    assert result["complete_requested_set"] is False
    assert json.loads((tmp_path / "m01_candidates" / f"{cid}.json").read_text()) == row


def test_markdown_delivery_stays_unchanged(tmp_path):
    first, a = create(tmp_path, 1, 20000)
    second, b = create(tmp_path, 2, 20000)
    assert invoke(Adapter(tmp_path, "markdown"), [first, second]) == {"audit_run_id": "current-run", "candidates": [a, b]}


def test_only_bounded_html_candidate_tool_is_exempt_from_second_externalization():
    from app.experiments.collusion_audit.runner import html_source_exemptions

    assert html_source_exemptions("M01", "html") == ["audit_m01_candidates"]
    assert html_source_exemptions("M01", "markdown") == []
    assert html_source_exemptions("M02", "html") == []


def test_character_budget_includes_all_continuation_and_source_metadata():
    from app.experiments.collusion_audit.m01_delivery import bounded_html_candidates

    rows = [{"candidate_id": "C000001", "candidate": {"text_a": "甲" * 28850, "text_b": "乙" * 28850}, "evidence": [{"quote": "完整来源"}]}]
    rows += [{"candidate_id": f"C{i:06d}", "candidate": {"text_a": "甲" * 40000, "text_b": "乙" * 40000}, "evidence": []} for i in range(2, 9)]
    result = bounded_html_candidates("M01_20261111T100000_abcdef1234", rows)
    assert len(json.dumps(result, ensure_ascii=False)) <= 60000
    delivered = {row["candidate_id"] for row in result["candidates"]}
    deferred = set(result["remaining_candidate_ids"]) | {row["candidate_id"] for row in result["oversized_candidate_refs"]}
    assert delivered | deferred == {row["candidate_id"] for row in rows}
    assert not delivered & deferred


def test_real_html_profile_delivers_exact_saved_candidate(scoped_profile, tmp_path):
    from app.experiments.collusion_audit.profile import CollusionProfile

    profile = CollusionProfile(scoped_profile.rule, scoped_profile.project, scoped_profile.outputs, tmp_path / "html-source-cache", scoped_profile.trace, quality_controls=True, rule_level_review=True, report_format="html")
    tools = {tool.name: tool for tool in profile.tools}
    receipt = json.loads(asyncio.run(tools["audit_m01"].ainvoke({})))
    assert receipt["finding_count"] >= 1
    # Small fixtures normally arrive inline; exercise the actual large-result
    # index materializer before calling a tool that requires an index ID.
    from app.experiments.collusion_audit.m01_delivery import materialize_candidates

    root = profile.adapters.result_root(receipt["run_id"])
    findings = [json.loads(line) for line in (root / "findings.jsonl").read_text().splitlines()]
    evidence = [json.loads(line) for line in (root / "evidence.jsonl").read_text().splitlines()]
    materialize_candidates(root, "/mnt/user-data/outputs/bid_audit/" + receipt["run_id"], {"summary": {"run_id": receipt["run_id"]}}, findings, evidence)
    delivered = json.loads(asyncio.run(tools["audit_m01_candidates"].ainvoke({"run_id": receipt["run_id"], "candidate_ids": ["C000001"]})))
    actual = profile.adapters.result_root(receipt["run_id"]) / "m01_candidates/C000001.json"
    assert delivered.get("status") != "error", delivered
    assert delivered["candidates"] == [json.loads(actual.read_text())]
    assert delivered["complete_requested_set"] is True
    assert delivered["remaining_candidate_ids"] == []
