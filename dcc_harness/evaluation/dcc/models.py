"""Configurable evaluator-only model calls; no policy or SP invocation."""

from __future__ import annotations

import importlib
import os
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

from dcc_harness.preprocessing.pipeline import digest, encoded, extension


class ModelCallError(RuntimeError):
    pass


class Model:
    def __init__(self, config: dict, context, role: str, *, default_model: str):
        allowed = {"use", "model", "base_url", "api", "api_key_env", "api_key_file_env", "api_key_file", "timeout", "max_tokens", "revision", "thinking"}
        if not isinstance(config, dict) or set(config) - allowed:
            raise ValueError("unsupported evaluator model configuration; configure keys by environment/private file")
        if "thinking" in config and (config["thinking"] not in {"enabled", "disabled"} or config.get("api", "chat") != "chat"):
            raise ValueError("thinking must be enabled/disabled on a compatible chat endpoint")
        self.config = deepcopy(config)
        self.model = config.get("model", default_model)
        self.base_url = config.get("base_url", "https://api.openai.com/v1")
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("judge base_url must be a plain HTTP(S) endpoint")
        factory = extension(config["use"], {}) if config.get("use") else None
        self.callback = factory(deepcopy(config)) if factory else None
        if self.callback is not None and not callable(self.callback):
            raise TypeError("model factory must return a callable")
        self.provenance = {
            "requested_model": self.model,
            "base_url": self.base_url,
            "revision": config.get("revision", "unknown"),
            "original_model": default_model,
            "custom_model": self.model != default_model,
            "use": config.get("use", "openai_sdk"),
            "calls": 0,
            "response_models": [],
            "thinking": config.get("thinking", "provider_default"),
        }
        if self.callback is not None:
            module = importlib.import_module(factory.__module__)
            path = getattr(module, "__file__", None)
            self.provenance["callback_sha256"] = digest(Path(path).read_bytes()) if path and Path(path).is_file() else "unknown"
        context.provenance.setdefault("models", {})[role] = self.provenance

    def __call__(self, api: str, **payload) -> tuple[object, str]:
        payload = {**payload, "model": self.model}
        if self.config.get("max_tokens") is not None and api == "chat":
            payload["max_tokens"] = self.config["max_tokens"]
        if "thinking" in self.config:
            if api != "chat":
                raise ValueError("thinking requires a compatible chat endpoint")
            payload["extra_body"] = {"thinking": {"type": self.config["thinking"]}}
        request = {"api": api, "payload": payload}
        request_hash = digest(encoded(request))
        self.provenance["api"] = api
        self.provenance["calls"] += 1
        if self.callback is not None:
            return self.callback(deepcopy(request)), request_hash
        from openai import OpenAI, OpenAIError

        env_name = self.config.get("api_key_env", "OPENAI_API_KEY")
        key = os.environ.get(env_name, "").strip() if env_name else "EMPTY"
        key_path = self.config.get("api_key_file") or os.environ.get(self.config.get("api_key_file_env", "DCC_JUDGE_API_KEY_FILE"))
        if not key and key_path:
            key = Path(key_path).read_text().strip()
        if not key:
            raise ModelCallError("configure the evaluator's API key environment or private key file")
        try:
            with OpenAI(api_key=key, base_url=self.base_url, timeout=float(self.config.get("timeout", 120)), max_retries=0) as client:
                if api == "responses":
                    response = client.responses.create(**payload)
                    text = response.output_text
                elif api == "chat":
                    response = client.chat.completions.create(**payload)
                    text = response.choices[0].message.content
                    if response.choices[0].finish_reason in {"length", "content_filter"}:
                        raise ModelCallError("judge response was incomplete or filtered")
                else:
                    raise ValueError("judge api must be chat or responses")
                if getattr(response, "model", None) not in self.provenance["response_models"]:
                    self.provenance["response_models"].append(getattr(response, "model", None))
                if not isinstance(text, str):
                    raise ModelCallError("judge did not return text")
                return text, request_hash
        except ModelCallError:
            raise
        except (OpenAIError, OSError) as exc:
            raise ModelCallError(f"judge request failed: {type(exc).__name__}") from None


def model_for(config: dict | None, context, role: str, *, default_model: str) -> Model:
    if config is None:
        raise ValueError(f"configure the official evaluator model for {role}; no numeric/heuristic proxy is substituted")
    models = context.resources.setdefault("dcc_models", {})
    if role not in models:
        models[role] = Model(config, context, role, default_model=default_model)
    return models[role]
