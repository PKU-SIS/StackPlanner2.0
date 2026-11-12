"""Existing native Central retry knobs, no new scheduling policy."""

from types import SimpleNamespace

import pytest

from app.experiments.collusion_audit.runner import central_retry_options
from deerflow.sp.central_resilience import SPCentralResilienceMiddleware


def test_defaults_preserve_native_configuration():
    assert central_retry_options() == {}


@pytest.mark.parametrize("seconds,tokens", [(0, 8192), (-1, 8192), (float("nan"), 8192), (240, 0), (240, True), (240, 1.2)])
def test_invalid_retry_allowances_rejected(seconds, tokens):
    with pytest.raises(ValueError):
        central_retry_options(seconds, tokens)


def test_actual_native_factory_receives_the_resource_knobs(monkeypatch):
    from deerflow.sp.runtime import make_sp_agent

    captured = {}

    class App:
        models = [SimpleNamespace(name="test-model")]
        title = SimpleNamespace(enabled=False, max_chars=60)

        @staticmethod
        def get_model_config(name):
            return SimpleNamespace(supports_thinking=False)

    class Graph:
        metadata = {}

    def lead(config, **kwargs):
        captured.update(kwargs)
        return Graph()

    monkeypatch.setattr("deerflow.agents.lead_agent.agent._make_lead_agent", lead)
    options = {"sp_central_timeout_seconds": 180, **central_retry_options(240, 8192)}
    make_sp_agent({"configurable": {"model_name": "test-model", **options}}, app_config=App())
    guard = next(m for m in captured["extra_middlewares"] if isinstance(m, SPCentralResilienceMiddleware))
    assert guard.initial_timeout_seconds == 180
    assert guard.retry_timeout_seconds == 240
    assert guard.retry_max_tokens == 8192

    class Request:
        model_settings = {"max_tokens": 8192}

        def override(self, **kwargs):
            return SimpleNamespace(**kwargs)

    assert guard._retry_request(Request()).model_settings["max_tokens"] == 8192
