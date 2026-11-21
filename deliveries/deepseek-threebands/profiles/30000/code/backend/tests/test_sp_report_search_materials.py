"""A single content-bearing search persists multiple citable report materials."""

import json
from pathlib import Path

from langchain.tools import tool

from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.report.search_materials import build_search_material_tool
from deerflow.sp.report.store import ReportPaths


def _ledger(tmp_path):
    ledger = ReportLedger.create(ReportPaths(tmp_path, "r1"), request_text="Compare protocols")
    ledger.begin_research_action("a1", [], allow_empty=True, kind="pre_research")
    ledger.save()
    return ledger


def test_ten_bodies_are_saved_by_one_search_without_fetch(tmp_path):
    ledger = _ledger(tmp_path)
    calls = []

    @tool("web_search")
    def search(query: str, max_results: int = 10) -> str:
        """Search for related documents."""
        calls.append((query, max_results))
        return json.dumps({"results": [{"url": f"https://example.com/{i}", "title": f"Source {i}", "summary": "Relevant analysis " * 300, "content": f"# Source {i}\r\n" + "Evidence\n" * 1000} for i in range(10)]})

    wrapper = build_search_material_tool(paths=ledger.paths, action_id="a1", gap_ids=[], search_tool=search)
    assert wrapper.args_schema.model_json_schema()["properties"]["query"]["maxLength"] == 128
    raw = wrapper.invoke({"query": "protocols"})
    assert len(raw) <= 10_000
    first = json.loads(raw)
    persisted = ReportLedger.load(ledger.paths)
    assert calls == [("protocols", 10)]
    assert len(persisted.materials()) == len(persisted.payload["sources"]) == 10
    assert first["saved_count"] == 10
    assert len(first["materials"]) == 10
    assert first["results"][0]["content_truncated"] is True
    assert Path(persisted.materials()[0]["path"]).read_bytes().decode() == "# Source 0\r\n" + "Evidence\n" * 1000
    assert all(row["source_id"] and row["material_id"] and row["path"] for row in first["materials"])

    second = json.loads(wrapper.invoke({"query": "related protocols"}))
    assert second["saved_count"] == 10
    assert len(ReportLedger.load(ledger.paths).materials()) == 10
    assert len(ReportLedger.load(ledger.paths).payload["sources"]) == 10


def test_empty_body_is_only_a_candidate_and_errors_are_preserved(tmp_path):
    ledger = _ledger(tmp_path)

    @tool("web_search")
    def search(query: str, max_results: int = 10) -> str:
        """Search for related documents."""
        return json.dumps({"results": [{"url": "https://example.com/empty", "title": "Empty", "summary": "Not a body", "content": ""}, {"url": "https://example.com/body", "content": "Verified body"}]})

    wrapper = build_search_material_tool(paths=ledger.paths, action_id="a1", gap_ids=[], search_tool=search)
    result = json.loads(wrapper.invoke({"query": "protocols"}))
    assert result["saved_count"] == 1
    assert result["results"][0]["saved"] is False
    assert "material_id" not in result["results"][0]
    assert len(ReportLedger.load(ledger.paths).materials()) == 1


def test_search_failure_creates_no_sources(tmp_path):
    ledger = _ledger(tmp_path)

    @tool("web_search")
    def search(query: str, max_results: int = 10) -> str:
        """Search for related documents."""
        return json.dumps({"error": "insufficient_balance", "status_code": 402, "retryable": False})

    result = json.loads(build_search_material_tool(paths=ledger.paths, action_id="a1", gap_ids=[], search_tool=search).invoke({"query": "protocols"}))
    assert result["status_code"] == 402
    assert not ReportLedger.load(ledger.paths).materials()
    assert not ReportLedger.load(ledger.paths).payload["sources"]
    assert ReportLedger.load(ledger.paths).action("a1")["tool_executions"][-1]["ok"] is False


def test_completed_action_does_not_accept_search_bodies(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.payload["actions"]["a1"]["status"] = "completed"
    ledger.save()
    calls = []

    @tool("web_search")
    def search(query: str, max_results: int = 10) -> str:
        """Search for related documents."""
        calls.append(query)
        return json.dumps({"results": []})

    result = json.loads(build_search_material_tool(paths=ledger.paths, action_id="a1", gap_ids=[], search_tool=search).invoke({"query": "protocols"}))
    assert result["error"]
    assert not calls
