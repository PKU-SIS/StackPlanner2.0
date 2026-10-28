"""Run one benchmark task through the unmodified native StackPlanner graph."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from deerflow.models.vllm_provider import VllmChatModel
from langchain_openai import ChatOpenAI

from dcc_harness.datasets.benchmark_plugins import (
    configure_dataset_app,
    resolve_dataset_plugin,
)
from dcc_harness.datasets.benchmarks import Task
from dcc_harness.model_transport import _loop_local_async_client

from .benchmarks import PROTOCOL, DeliveryError, extract_delivery


class _LoopLocalTransport:
    """Keep native model behavior while isolating its asynchronous pool."""

    def __init__(self, **kwargs):
        kwargs["http_async_client"] = _loop_local_async_client(timeout=kwargs.get("timeout"))
        # Keep the default sync client proxy-aware too: LangChain's socket
        # transport otherwise shadows environment proxies beside our async client.
        kwargs.setdefault("http_socket_options", ())
        super().__init__(**kwargs)


class BenchmarkChatOpenAI(_LoopLocalTransport, ChatOpenAI):
    """OpenAI-compatible evaluation transport for native Central and helpers."""

    def __init__(self, **kwargs):
        from deerflow.models.factory import _apply_stream_chunk_timeout_default

        # Native factory defaults are keyed by the configured provider path.
        # Preserve its OpenAI stream budget when using this external subclass.
        _apply_stream_chunk_timeout_default("langchain_openai:ChatOpenAI", kwargs)
        super().__init__(**kwargs)


class BenchmarkVllmChatModel(_LoopLocalTransport, VllmChatModel):
    """The same loop isolation with native Qwen/vLLM tool-call recovery."""


@dataclasses.dataclass(frozen=True)
class Settings:
    config: str
    base_url: str
    model: str
    api_key_env: str = "DCC_MODEL_API_KEY"
    max_iterations: int = 20
    max_tokens: int = 4096
    timeout: float = 900.0
    temperature: float = 0.0
    seed: int = 0
    thinking: str | None = None
    recover_qwen_tool_calls: bool = False
    dataset_plugins: tuple[str, ...] = ()


def json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return json_value(value.model_dump(mode="json"))
    if dataclasses.is_dataclass(value):
        return json_value(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return type(value).__name__


def effective_app(settings: Settings, benchmark: str | None = None):
    """Resolve the same endpoint for native helpers that bypass role injection.

    In particular native context compaction constructs its own model from
    AppConfig. Credentials stay in this in-memory object, never the manifest.
    """
    from deerflow.config.app_config import AppConfig

    app = AppConfig.from_file(settings.config)
    if not app.models:
        raise ValueError("native benchmark config has no model")
    values = {
        "model": settings.model,
        "base_url": settings.base_url,
        "api_key": os.environ.get(settings.api_key_env) or "EMPTY",
        "use": f"{__name__}:BenchmarkVllmChatModel" if settings.recover_qwen_tool_calls else f"{__name__}:BenchmarkChatOpenAI",
        "max_tokens": settings.max_tokens,
        "timeout": settings.timeout,
        "temperature": settings.temperature,
        "seed": settings.seed,
        "max_retries": 0,
        "streaming": False,
    }
    if settings.thinking:
        values["extra_body"] = {"thinking": {"type": settings.thinking}}
    # Revalidate so AppConfig rebuilds its native name -> model indexes; merely
    # replacing models[0] leaves get_model_config() pointing at the template.
    payload = app.model_dump(mode="python")
    payload["models"][0].update(values)
    app = AppConfig.model_validate(payload)
    if app.summarization.model_name and app.summarization.model_name != app.models[0].name:
        raise ValueError("benchmark summarization must use the selected model, not an unrecorded endpoint")
    if benchmark is not None:
        app = configure_dataset_app(app, resolve_dataset_plugin(benchmark, settings.dataset_plugins))
    return app


def failure_status(state: dict) -> str:
    """Classify observed native failures without imposing execution gates."""
    for message in reversed(json_value(state.get("messages") or [])):
        if message.get("type") != "ai":
            continue
        if (message.get("additional_kwargs") or {}).get("error_type"):
            return "infrastructure_error"
        if message.get("invalid_tool_calls") or ("DSML" in str(message.get("content", "")) and not message.get("tool_calls")):
            return "protocol_error"
    return "unfinished"


async def run_task(task: Task, settings: Settings, *, output_dir: Path, judge=None) -> dict:
    from deerflow.runtime.user_context import reset_current_user, set_current_user

    identity = uuid4().hex
    token = set_current_user(SimpleNamespace(id=f"bench-{identity}"))
    try:
        return await _run_task(task, settings, output_dir=output_dir, judge=judge, identity=identity)
    finally:
        reset_current_user(token)


async def _run_task(task: Task, settings: Settings, *, output_dir: Path, judge, identity: str) -> dict:
    from deerflow.config.app_config import set_app_config
    from deerflow.config.paths import get_paths
    from deerflow.sandbox.sandbox_provider import reset_sandbox_provider
    from deerflow.sp.runtime import make_sp_agent
    from langchain_core.messages import HumanMessage

    from dcc_harness.widesearch_metrics import JudgeError

    plugin = resolve_dataset_plugin(task.benchmark, settings.dataset_plugins)
    if plugin.parse_answer is None or plugin.score_task is None:
        raise ValueError(f"dataset plugin requires answer parsing and scoring: {plugin.name}")
    output_dir.mkdir(parents=True, exist_ok=False)
    thread_id, run_id = f"bench-{identity}", f"run-{identity}"
    context = {"thread_id": thread_id, "run_id": run_id, "episode_id": f"episode-{identity}", "user_id": f"bench-{identity}"}
    result = dict(task_id=task.id, benchmark=task.benchmark, split=task.split, protocol=PROTOCOL, **context, status="unfinished", detail="", answer=None, metrics={})
    started = time.monotonic()
    abort = threading.Event()
    context["__run_abort_event"] = abort
    state = {}
    app = effective_app(settings, task.benchmark)
    set_app_config(app)
    # This entry point runs tasks serially. Rebuild the native provider so its
    # static public-skill mount follows the current dataset's skill root.
    reset_sandbox_provider()
    config = {"configurable": {**context, "model_name": app.models[0].name, "max_concurrent_subagents": 1, "sp_max_actions": settings.max_iterations}, "context": context}
    inputs = {"messages": [HumanMessage(content=plugin.prepare_task(task), id=f"input-{identity}")]}
    inputs.update(plugin.prepare_inputs(task, thread_id))

    async def execute():
        nonlocal state
        # Deliberately no executor_provider/action_executor override: the SP
        # default DR2SPExecutorProvider owns native role execution and tools.
        graph = make_sp_agent(config, app_config=app)
        with (output_dir / "trace.jsonl").open("x", encoding="utf-8") as trace:
            async for kind, payload in graph.astream(
                inputs,
                config={"configurable": {"thread_id": thread_id}, "recursion_limit": max(256, settings.max_iterations * 16)},
                context=context,
                stream_mode=["values", "custom"],
            ):
                if kind == "values":
                    state = payload
                trace.write(json.dumps({"kind": kind, "data": json_value(payload)}, ensure_ascii=False) + "\n")
                trace.flush()
            os.fsync(trace.fileno())

    try:
        await asyncio.wait_for(execute(), timeout=settings.timeout)
        text, artifact = extract_delivery(state, thread_id=thread_id, run_id=run_id)
        result.update(delivery=text, final_artifact=artifact)
        result["answer"] = plugin.parse_answer(task, text)
        result["metrics"] = await asyncio.to_thread(plugin.score_task, task, text, judge)
        result["status"] = "completed"
    except DeliveryError as exc:
        result.update(status=failure_status(state) if exc.status == "unfinished" else exc.status, detail=str(exc))
    except JudgeError as exc:
        result.update(status="judge_error", detail=str(exc))
    except (json.JSONDecodeError, ValueError) as exc:
        result.update(status="final_parse_error", detail=str(exc))
    except (TimeoutError, ConnectionError, ImportError) as exc:
        result.update(status="infrastructure_error", detail=type(exc).__name__)
    except Exception as exc:
        # Do not dump SDK request headers or credentials. Full native state and
        # tool observations are retained independently for diagnosis.
        kind = "infrastructure_error" if type(exc).__module__.startswith(("openai", "httpx", "httpcore")) else "runtime_error"
        result.update(status=kind, detail=type(exc).__name__)
    finally:
        # Use the native cooperative abort signal when workers unwind after
        # timeout or external cancellation; never leave a second agent loop.
        abort.set()
    result.update(elapsed_seconds=time.monotonic() - started, native_iterations=state.get("sp_loop_iteration"), native_handler_result=json_value(state.get("sp_last_handler_result")))
    (output_dir / "state.json").write_text(json.dumps(json_value(state), ensure_ascii=False, indent=2), encoding="utf-8")
    tool_log = get_paths().thread_dir(thread_id, user_id=context["user_id"]) / "benchmark-tools.jsonl"
    result["tool_events"] = [json.loads(line) for line in tool_log.read_text(encoding="utf-8").splitlines()] if tool_log.exists() else []
    with (output_dir / "result.json").open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    return result
