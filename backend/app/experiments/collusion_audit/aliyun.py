"""Run-local official Alibaba Cloud model; no local-vLLM output repair."""

import os
from urllib.parse import urlparse

from deerflow.config.model_config import ModelConfig


def model_config(*, max_tokens=8192):
    key = os.environ.get("DASHSCOPE_API_KEY", "")
    base = os.environ.get("DASHSCOPE_BASE_URL", "").rstrip("/")
    parsed = urlparse(base)
    if not key or key in {"EMPTY", "dummy"}:
        raise ValueError("A real DASHSCOPE_API_KEY is required for the remote model")
    if parsed.scheme != "https" or not parsed.hostname or not (parsed.hostname.endswith(".maas.aliyuncs.com") or parsed.hostname in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"}) or parsed.path != "/compatible-mode/v1":
        raise ValueError("DASHSCOPE_BASE_URL must be an official Alibaba OpenAI-compatible endpoint")
    return ModelConfig(
        name="aliyun-qwen3.8-27b",
        display_name="Alibaba Qwen3.8-27B",
        use="langchain_openai:ChatOpenAI",
        model="qwen3.8-27b",
        api_key=key,
        base_url=base,
        supports_thinking=True,
        supports_reasoning_effort=True,
        supports_vision=True,
        when_thinking_enabled={"extra_body": {"enable_thinking": True}},
        when_thinking_disabled={"extra_body": {"enable_thinking": False}},
        max_tokens=max_tokens,
        timeout=240,
        max_retries=0,
        streaming=True,
        stream_usage=True,
        use_responses_api=False,
    )
