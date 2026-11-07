import json
from types import SimpleNamespace
import pytest
from dcc_harness.datasets.clause_tools import search_document, grep_tool


def test_long_paragraph_is_searched_and_actual_match_is_visible():
    text = "prefix " * 1000 + "Section 42 conflicts with Section 8." + " suffix" * 1000
    r = search_document(text, "Section", literal=True, max_results=100)
    assert r["status"] == "success" and r["search_complete"]
    assert r["matched_lines"] == 1 and r["matches"][0]["line"] == 1
    assert "Section 42" in r["matches"][0]["text"]
    assert r["matches"][0]["text"] in text
    assert r["matches"][0]["start_column"] > 2000
    assert search_document(text, r"Section \d+", max_results=100)["matched_lines"] == 1


def test_limits_empty_and_invalid_regex_are_distinguished():
    assert search_document("abc", "z")["search_complete"]
    r = search_document("x\nx\nx", "x", max_results=2)
    assert r["truncated"] and not r["search_complete"] and len(r["matches"]) == 2
    r = search_document("abc", "[")
    assert r["status"] == "error" and not r["search_complete"]


def test_regex_timeout_never_claims_no_matches():
    r = search_document("a" * 100000 + "!", "(a+)+$", timeout_seconds=0.001)
    assert r["status"] == "incomplete" and not r["search_complete"]
    assert r["reason"] == "regex_timeout"


def test_source_file_and_upload_directory_are_equivalent_without_siblings(monkeypatch):
    seen = []

    def read(runtime, path):
        seen.append(path)
        return "x " * 3000 + "Section 5"

    monkeypatch.setattr(
        "dcc_harness.datasets.clause_tools.read_current_file_content", read
    )
    for path in [
        "/mnt/user-data/uploads",
        "/mnt/user-data/uploads/",
        "/mnt/user-data/uploads/document.txt",
    ]:
        r = json.loads(
            grep_tool.func(SimpleNamespace(), "find section", "Section", path)
        )
        assert r["matched_lines"] == 1
    assert seen == ["/mnt/user-data/uploads/document.txt"] * 3


def test_source_read_failure_and_glob_mismatch_are_truthful(monkeypatch):
    def fail(*args):
        raise PermissionError("private host path")

    monkeypatch.setattr(
        "dcc_harness.datasets.clause_tools.read_current_file_content", fail
    )
    r = json.loads(
        grep_tool.func(
            SimpleNamespace(), "search", "x", "/mnt/user-data/uploads/document.txt"
        )
    )
    assert r["status"] == "error" and "private host path" not in json.dumps(r)
    r = json.loads(
        grep_tool.func(
            SimpleNamespace(),
            "search",
            "x",
            "/mnt/user-data/uploads/document.txt",
            glob="*.pdf",
        )
    )
    assert r["status"] == "success" and r["matched_lines"] == 0


def test_numbered_read_preserves_source_and_reports_coverage():
    from dcc_harness.datasets.clause_tools import document_read

    text = "first\nsecond\nthird"
    whole = document_read(text)
    assert whole["file_complete"] and whole["eof"] and whole["total_lines"] == 3
    assert "".join(x["text"] for x in whole["lines"]) == text
    part = document_read(text, start_line=2, end_line=2)
    assert not part["file_complete"] and not part["eof"]
    assert part["lines"] == [{"line": 2, "text": "second\n"}]


def test_numbered_read_budget_is_explicit_and_resumable():
    from dcc_harness.datasets.clause_tools import document_read

    text = "some source line\n" * 100
    r = document_read(text, max_chars=1000)
    assert r["truncated"] and not r["file_complete"] and not r["eof"]
    assert r["next_start_line"] == r["lines"][-1]["line"] + 1
    assert len(json.dumps(r, ensure_ascii=False)) <= 1000
    tail = document_read(text, start_line=r["next_start_line"])
    assert "".join(x["text"] for x in r["lines"] + tail["lines"]) == text


def test_plugin_overrides_are_scoped_and_fail_closed():
    import dataclasses
    from deerflow.config.app_config import AppConfig
    from deerflow.config.tool_config import ToolConfig
    from dcc_harness.datasets.benchmark_plugins import (
        configure_dataset_app,
        resolve_dataset_plugin,
    )
    from dcc_harness.preprocessing.clause import make_plugin
    from deerflow.tools import get_available_tools

    app = AppConfig.from_file("configs/stackplanner_benchmarks.yaml")
    plugin = resolve_dataset_plugin("clause")
    configured = configure_dataset_app(app, plugin)
    tools = {
        t.name: t for t in get_available_tools(app_config=configured, include_mcp=False)
    }
    assert tools["grep"].func.__module__ == "dcc_harness.datasets.clause_tools"
    assert tools["read_file"].func.__module__ == "dcc_harness.datasets.clause_tools"
    assert next(t for t in app.tools if t.name == "grep").use.startswith("deerflow.")
    assert not make_plugin().tool_overrides
    assert not resolve_dataset_plugin("cuad").tool_overrides
    with pytest.raises(ValueError, match="tool overrides"):
        configure_dataset_app(
            app,
            dataclasses.replace(
                plugin,
                tool_overrides=(
                    ToolConfig(name="missing", group="file:read", use="missing:tool"),
                ),
            ),
        )
    with pytest.raises(ValueError, match="tool overrides"):
        configure_dataset_app(
            app,
            dataclasses.replace(plugin, tool_overrides=(plugin.tool_overrides[0],) * 2),
        )


def test_search_delivers_every_occurrence_on_one_line_and_pages_without_loss():
    text = "x " * 1000 + "Term A; Term B; Term C"
    first = search_document(text, "Term", max_results=2)
    assert len(first["matches"]) == 2 and first["truncated"]
    second = search_document(text, "Term", start_offset=first["next_offset"])
    assert len(second["matches"]) == 1 and second["search_complete"]
    spans = [m["match_start_offset"] for m in first["matches"] + second["matches"]]
    assert spans == [2000, 2008, 2016]
