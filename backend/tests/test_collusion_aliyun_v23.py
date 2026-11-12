"""Remote configuration maps official parameters without vLLM compatibility."""

import pytest
from langchain_core.messages import HumanMessage

from app.experiments.collusion_audit.aliyun import model_config


def test_cloud_requires_its_own_secret_and_endpoint(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with pytest.raises(ValueError):
        model_config()
    monkeypatch.setenv("DASHSCOPE_API_KEY", "unit-test-only")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "http://127.0.0.1:8075/v1")
    with pytest.raises(ValueError):
        model_config()


@pytest.mark.parametrize("thinking", [False, True])
def test_real_cloud_payload_uses_correct_thinking_and_budget(monkeypatch, thinking):
    from app.experiments.tender_audit.runner import experiment_config
    from deerflow.models import create_chat_model

    monkeypatch.setenv("DASHSCOPE_API_KEY", "unit-test-only")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
    config = experiment_config("aliyun-qwen3.8-27b", max_tokens=32768)
    model = create_chat_model(name="aliyun-qwen3.8-27b", thinking_enabled=thinking, app_config=config, attach_tracing=False)
    payload = model._get_request_payload([HumanMessage("probe")])
    assert payload["model"] == "qwen3.8-27b" and payload["stream"] is True
    assert payload["extra_body"]["enable_thinking"] is thinking
    assert "chat_template_kwargs" not in payload["extra_body"]
    assert payload.get("max_completion_tokens", payload.get("max_tokens")) == 32768
    assert model.root_async_client.max_retries == 0
