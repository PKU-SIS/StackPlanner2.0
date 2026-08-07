"""Unit tests for the Bocha / LangSearch community web search tool."""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest


@pytest.fixture(autouse=True)
def reset_api_key_warned():
    import deerflow.community.bocha.tools as bocha_mod

    bocha_mod._api_key_warned = False
    yield
    bocha_mod._api_key_warned = False


@pytest.fixture
def mock_config_with_key():
    with patch("deerflow.community.bocha.tools.get_app_config") as mock:
        tool_config = MagicMock()
        tool_config.model_extra = {"api_key": "test-bocha-key", "max_results": 5}
        mock.return_value.get_tool_config.return_value = tool_config
        yield mock


@pytest.fixture
def mock_config_no_key():
    with patch("deerflow.community.bocha.tools.get_app_config") as mock:
        tool_config = MagicMock()
        tool_config.model_extra = {}
        mock.return_value.get_tool_config.return_value = tool_config
        yield mock


def _make_bocha_response(items: list) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": {"webPages": {"value": items}}}
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def test_get_api_key_returns_config_key_when_present(mock_config_with_key):
    from deerflow.community.bocha.tools import _get_api_key

    assert _get_api_key("web_search") == "test-bocha-key"


def test_get_api_key_falls_back_to_env_when_config_empty(mock_config_no_key):
    with patch.dict("os.environ", {"BOCHA_API_KEY": "env-bocha-key"}, clear=True):
        from deerflow.community.bocha.tools import _get_api_key

        assert _get_api_key("web_search") == "env-bocha-key"


def test_get_api_key_falls_back_to_langsearch_env(mock_config_no_key):
    with patch.dict("os.environ", {"LANGSEARCH_API_KEY": "env-langsearch-key"}, clear=True):
        from deerflow.community.bocha.tools import _get_api_key

        assert _get_api_key("web_search") == "env-langsearch-key"


def test_web_search_missing_key_returns_structured_error(mock_config_no_key):
    with patch.dict("os.environ", {}, clear=True):
        from deerflow.community.bocha.tools import web_search_tool

        parsed = json.loads(web_search_tool.invoke({"query": "Allianz"}))

    assert parsed["error"] == "BOCHA_API_KEY is not configured"
    assert parsed["query"] == "Allianz"


def test_web_search_returns_normalized_results(mock_config_with_key):
    from deerflow.community.bocha import tools

    with patch("deerflow.community.bocha.tools.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.post.return_value = _make_bocha_response(
            [
                {
                    "name": "Allianz Group SFCR 2024",
                    "url": "https://www.allianz.com/example.pdf",
                    "snippet": "Solvency report snippet",
                    "summary": "Long solvency report summary",
                    "siteName": "Allianz",
                    "datePublished": "2025-03-01",
                }
            ]
        )

        parsed = json.loads(
            tools.web_search_tool.invoke({"query": "Allianz 2024 SFCR"})
        )

    assert parsed["total_results"] == 1
    result = parsed["results"][0]
    assert result["title"] == "Allianz Group SFCR 2024"
    assert result["url"] == "https://www.allianz.com/example.pdf"
    assert result["content"] == "Long solvency report summary"
    assert result["snippet"] == "Solvency report snippet"
    assert result["site_name"] == "Allianz"
    client_cls.assert_called_once()
    assert client_cls.call_args.kwargs["trust_env"] is False


def test_web_search_honors_trust_env_config():
    with patch("deerflow.community.bocha.tools.get_app_config") as mock_config:
        tool_config = MagicMock()
        tool_config.model_extra = {
            "api_key": "test-bocha-key",
            "trust_env": True,
            "timeout": 12,
            "freshness": "oneYear",
        }
        mock_config.return_value.get_tool_config.return_value = tool_config

        from deerflow.community.bocha import tools

        with patch("deerflow.community.bocha.tools.httpx.Client") as client_cls:
            client = client_cls.return_value.__enter__.return_value
            client.post.return_value = _make_bocha_response(
                [{"name": "Result", "url": "https://example.com", "summary": "Summary"}]
            )

            json.loads(tools.web_search_tool.invoke({"query": "test"}))

    assert client_cls.call_args.kwargs["trust_env"] is True
    assert client_cls.call_args.kwargs["timeout"] == 12.0
    payload = client_cls.return_value.__enter__.return_value.post.call_args.kwargs["json"]
    assert payload["freshness"] == "oneYear"


def test_web_search_no_results_returns_structured_error(mock_config_with_key):
    from deerflow.community.bocha import tools

    with patch("deerflow.community.bocha.tools.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.post.return_value = _make_bocha_response([])

        parsed = json.loads(tools.web_search_tool.invoke({"query": "no results"}))

    assert parsed["error"] == "No results found"
    assert parsed["query"] == "no results"


def test_web_search_http_error_returns_structured_error(mock_config_with_key):
    from deerflow.community.bocha import tools

    response = httpx.Response(429, text="rate limited", request=httpx.Request("POST", "https://example.com"))
    error = httpx.HTTPStatusError("rate limited", request=response.request, response=response)

    with patch("deerflow.community.bocha.tools.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.post.return_value.raise_for_status.side_effect = error

        parsed = json.loads(tools.web_search_tool.invoke({"query": "rate limited"}))

    assert parsed["error"] == "Bocha/LangSearch API error: HTTP 429"
    assert parsed["query"] == "rate limited"


def test_web_search_unexpected_response_returns_structured_error(mock_config_with_key):
    from deerflow.community.bocha import tools

    with patch("deerflow.community.bocha.tools.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.post.return_value.json.return_value = ["not", "a", "dict"]
        client.post.return_value.raise_for_status = MagicMock()

        parsed = json.loads(tools.web_search_tool.invoke({"query": "bad"}))

    assert parsed["error"] == "Bocha/LangSearch returned an unexpected response format"
