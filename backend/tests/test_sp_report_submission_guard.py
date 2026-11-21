"""Bounded native tool feedback without truncating Writer content."""

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from test_sp_report_writer_length import _writer

from deerflow.sp.report.ledger import ReportLedger


def _enabled(tmp_path):
    middleware, path, ledger, task, ctx = _writer(tmp_path)
    ledger.payload["length_contract"]["writer_submission_limit"] = True
    ledger.save()
    return middleware, path, ledger


def _request(middleware, content, **extra):
    path = middleware.paths.draft_file(middleware.identity["node_id"], middleware.identity["draft_id"])
    return SimpleNamespace(tool_call={"name": "write_file", "id": "write-call", "args": {"path": middleware.paths.virtual(path), "content": content, **extra}})


def test_overlong_submission_is_not_written_and_one_complete_resubmission_is_allowed(tmp_path):
    middleware, path, ledger = _enabled(tmp_path)
    original = path.read_bytes()
    invoked = []

    def write(request):
        invoked.append(True)
        path.write_text(request.tool_call["args"]["content"])
        return "saved"

    result = middleware.wrap_tool_call(_request(middleware, "证" * 75), write)
    assert result.status == "error"
    assert json.loads(result.content)["estimated_words"] == 75
    assert path.read_bytes() == original and not invoked
    assert middleware.wrap_tool_call(_request(middleware, "证" * 40), write) == "saved"
    assert path.read_text() == "证" * 40
    attempt = ReportLedger.load(ledger.paths).payload["write_requests"][0]["attempts"][0]
    assert len(attempt["length_submissions"]) == 2
    assert attempt["length_submissions"][0]["within_limit"] is False
    assert attempt["length_submissions"][1]["within_limit"] is True


def test_second_overlong_submission_terminates_without_cutting_existing_file(tmp_path):
    from langgraph.graph import END
    from langgraph.types import Command

    middleware, path, _ = _enabled(tmp_path)
    original = path.read_bytes()

    def handler(request):
        raise AssertionError("overlong body must not be written")

    middleware.wrap_tool_call(_request(middleware, "证" * 75), handler)
    result = middleware.wrap_tool_call(_request(middleware, "证" * 65), handler)
    assert isinstance(result, Command) and result.goto == END
    assert path.read_bytes() == original
    assert middleware.consume_stop_reason() == "writer_length_submission_failed"
    assert middleware.consume_stop_reason() is None


@pytest.mark.parametrize("failure", ["wrong_path", "symlink", "stale_request", "changed_tree"])
def test_invalid_binding_never_writes(tmp_path, failure):
    middleware, path, ledger = _enabled(tmp_path)
    request = _request(middleware, "证" * 40)
    if failure == "wrong_path":
        request.tool_call["args"]["path"] += ".other"
    elif failure == "symlink":
        outside = tmp_path / "outside"
        outside.write_text("untouched")
        path.unlink()
        path.symlink_to(outside)
    elif failure == "stale_request":
        ledger.payload["write_requests"][0]["status"] = "completed"
        ledger.save()
    else:
        from pathlib import Path

        tree_path = Path(ledger.payload["tree"]["path"])
        tree_path.write_text(tree_path.read_text() + "\n")
    result = middleware.wrap_tool_call(request, lambda request: pytest.fail("invalid proposal reached write"))
    assert result.goto == "__end__"
    assert middleware.consume_stop_reason("run") == "writer_length_submission_failed"


def test_append_is_rejected_even_if_fragment_fits(tmp_path):
    middleware, path, _ = _enabled(tmp_path)
    original = path.read_bytes()
    result = middleware.wrap_tool_call(_request(middleware, "证", append=True), lambda request: pytest.fail("append bypass"))
    assert result.status == "error" and path.read_bytes() == original


@pytest.mark.asyncio
async def test_async_submission_is_offloaded_and_respects_limit(tmp_path, monkeypatch):
    import asyncio

    middleware, path, _ = _enabled(tmp_path)
    real = asyncio.to_thread
    calls = []

    async def tracked(fn, *args):
        calls.append(fn)
        return await real(fn, *args)

    async def write(request):
        pytest.fail("overlong content reached async handler")

    monkeypatch.setattr(asyncio, "to_thread", tracked)
    result = await middleware.awrap_tool_call(_request(middleware, "证" * 75), write)
    assert result.status == "error" and len(calls) == 1


@pytest.mark.parametrize("value", ["true", 1, None])
def test_submission_flag_requires_boolean(value):
    from deerflow.sp.report.ledger import ReportValidationError
    from deerflow.sp.report.length import normalize_contract

    with pytest.raises(ReportValidationError, match="writer_submission_limit must be boolean"):
        normalize_contract({"writer_submission_limit": value})


def test_explicit_five_percent_tolerance_is_bounded_before_generation(tmp_path):
    middleware, path, ledger = _enabled(tmp_path)
    ledger.payload["length_contract"]["writer_submission_tolerance_percent"] = 5
    ledger.save()
    # The fixture allocates maximum 50, so integer floor permits 52, not 53.
    result = middleware.wrap_tool_call(_request(middleware, "证" * 53), lambda request: pytest.fail("outside tolerance"))
    assert result.status == "error"
    assert json.loads(result.content)["max_words"] == 52

    def write(request):
        path.write_text(request.tool_call["args"]["content"])
        return "saved"

    assert middleware.wrap_tool_call(_request(middleware, "证" * 52), write) == "saved"
    attempt = ReportLedger.load(ledger.paths).payload["write_requests"][0]["attempts"][0]
    assert attempt["length_submissions"][-1]["assigned_max_words"] == 50
    assert len(path.read_text()) == 52


@pytest.mark.parametrize("value", [-1, 6, True, 1.5])
def test_submission_tolerance_must_be_integer_zero_to_five(value):
    from deerflow.sp.report.ledger import ReportValidationError
    from deerflow.sp.report.length import normalize_contract

    with pytest.raises(ReportValidationError, match="writer_submission_tolerance_percent"):
        normalize_contract({"writer_submission_limit": True, "writer_submission_tolerance_percent": value})


def test_tolerance_cannot_be_silently_ignored_without_submission_check():
    from deerflow.sp.report.ledger import ReportValidationError
    from deerflow.sp.report.length import normalize_contract

    with pytest.raises(ReportValidationError, match="requires writer_submission_limit"):
        normalize_contract({"writer_submission_tolerance_percent": 5})


def test_legacy_length_contract_retains_measure_only_behavior(tmp_path):
    middleware, _, _, _, _ = _writer(tmp_path)
    assert middleware.wrap_tool_call(_request(middleware, "证" * 75), lambda request: "legacy saved") == "legacy saved"


def test_native_agent_exits_after_two_rejected_submissions(tmp_path):
    from _agent_e2e_helpers import FakeToolCallingModel
    from langchain.agents import create_agent
    from langchain.tools import tool

    middleware, path, _ = _enabled(tmp_path)
    original = path.read_bytes()

    @tool
    def write_file(path: str, content: str) -> str:
        """Save a complete chapter."""
        raise AssertionError("rejected content must never reach the writer")

    virtual = middleware.paths.virtual(path)
    model = FakeToolCallingModel(responses=[AIMessage(content="", tool_calls=[{"name": "write_file", "id": f"w{i}", "args": {"path": virtual, "content": "证" * 75}}]) for i in range(3)])
    result = create_agent(model=model, tools=[write_file], middleware=[middleware]).invoke({"messages": [AIMessage(content="write")]})
    assert sum(isinstance(m, AIMessage) and bool(m.tool_calls) for m in result["messages"]) == 2
    assert path.read_bytes() == original
