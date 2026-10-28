"""Loop-local OpenAI HTTP clients shared by evaluation and training."""

from __future__ import annotations

import asyncio
import threading
import weakref
from typing import Any

_LOOP_ASYNC_CLIENTS: weakref.WeakKeyDictionary[Any, Any] = weakref.WeakKeyDictionary()

_LOOP_ASYNC_CLIENTS_LOCK = threading.Lock()

def _loop_local_async_client(*, timeout: Any) -> Any:
    """Return one SDK-compatible HTTP client per running event loop."""

    from openai import DefaultAsyncHttpxClient

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Builders are normally invoked from an active rollout/subagent loop.
        # Keep direct synchronous callers isolated rather than entering the
        # process-global LangChain cache.
        return DefaultAsyncHttpxClient(timeout=timeout)
    with _LOOP_ASYNC_CLIENTS_LOCK:
        client = _LOOP_ASYNC_CLIENTS.get(loop)
        if client is None or client.is_closed:
            client = DefaultAsyncHttpxClient(timeout=timeout)
            _LOOP_ASYNC_CLIENTS[loop] = client
        return client
