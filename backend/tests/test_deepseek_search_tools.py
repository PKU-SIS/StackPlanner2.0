"""DeepSeek tools use explicit credentials, content-bearing search and bounded retries."""

import json
from types import SimpleNamespace

import httpx
import pytest

from deerflow.community.deepseek_tools import tools


@pytest.fixture
def transport(monkeypatch):
    calls = []
    responses = []
    client_type = httpx.Client

    def handle(request):
        calls.append(request)
        status, body = responses.pop(0)
        return httpx.Response(status, json=body)

    monkeypatch.setattr(tools.httpx, "Client", lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs))
    monkeypatch.setattr(tools, "get_app_config", lambda: SimpleNamespace(get_tool_config=lambda name: SimpleNamespace(model_extra={"api_key": "test-secret"})))
    monkeypatch.setattr(tools.time, "sleep", lambda seconds: None)
    return calls, responses


def test_search_defaults_to_ten_results_with_bodies(transport):
    calls, responses = transport
    responses.append((200, {"results": [{"url": "https://example.com/a", "title": "A", "content": "Full body", "summary": "Relevant information"}]}))
    result = json.loads(tools.web_search_tool.invoke({"query": "A2A MCP"}))
    payload = json.loads(calls[0].content)
    assert payload == {"query": "A2A MCP", "count": 10, "need_content": True}
    assert calls[0].url == "https://api.deepseek.com/v1/tools/search"
    assert calls[0].headers["authorization"] == "Bearer test-secret"
    assert result["results"][0]["content"] == "Full body"


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404])
def test_permanent_errors_are_not_retried_or_leaked(transport, status):
    calls, responses = transport
    responses.append((status, {"error": {"code": "denied", "message": "credential test-secret"}}))
    result = json.loads(tools.web_search_tool.invoke({"query": "query"}))
    assert len(calls) == 1
    assert result["status_code"] == status
    assert result["retryable"] is False
    assert "test-secret" not in json.dumps(result)


@pytest.mark.parametrize("status", [408, 429, 500, 504])
def test_transient_errors_retry_once(transport, status):
    calls, responses = transport
    responses.extend([(status, {"error": {"code": "temporary"}}), (200, {"results": []})])
    result = json.loads(tools.web_search_tool.invoke({"query": "query"}))
    assert len(calls) == 2
    assert result["results"] == []
    assert "error" not in result


def test_fetch_returns_markdown_without_a_second_local_fetch(transport):
    calls, responses = transport
    responses.append((200, {"title": "Source", "content": "# Source\nEvidence."}))
    assert tools.web_fetch_tool.invoke({"url": "https://example.com/source"}) == "# Source\nEvidence."
    assert calls[0].url == "https://api.deepseek.com/v1/tools/fetch"
    assert json.loads(calls[0].content) == {"url": "https://example.com/source"}


def test_fetch_rejects_credentials_before_sending_request(transport):
    calls, _ = transport
    result = tools.web_fetch_tool.invoke({"url": "https://user:password@example.com/private"})
    assert result.startswith("Error:")
    assert not calls


def test_missing_key_does_not_send_request(transport, monkeypatch):
    calls, _ = transport
    monkeypatch.setattr(tools, "get_app_config", lambda: SimpleNamespace(get_tool_config=lambda name: None))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    result = json.loads(tools.web_search_tool.invoke({"query": "query"}))
    assert result["error"] == "DEEPSEEK_API_KEY_MISSING"
    assert not calls
