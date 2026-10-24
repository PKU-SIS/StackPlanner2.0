"""The main lifespan must finish runtime cleanup before closing loop clients."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import app.channels.service as channel_service_module
import app.gateway.app as app_module
from deerflow.models import factory as factory_module


def test_lifespan_closes_clients_after_langgraph_runtime_exit(monkeypatch):
    events: list[str] = []

    @asynccontextmanager
    async def fake_runtime(_app, _config):
        try:
            yield
        finally:
            events.append("runtime_exit")

    def fake_cleanup():
        return 0

    async def fake_start_channels(_config):
        return {"service_running": True, "channels": {}}

    async def fake_stop_channels():
        return None

    async def fake_close_oidc():
        return None

    async def fake_close_clients(loop):
        events.append("clients_closed")
        assert loop is asyncio.get_running_loop()

    async def fake_ensure_admin(_app):
        return None

    startup_config = SimpleNamespace(
        memory=SimpleNamespace(token_counting="char"),
        scheduler=SimpleNamespace(enabled=False),
    )
    monkeypatch.setattr(app_module, "get_app_config", lambda: startup_config)
    monkeypatch.setattr(app_module, "configure_logging", lambda _config: None)
    monkeypatch.setattr(app_module, "warn_if_auth_disabled_enabled", lambda: None)
    monkeypatch.setattr(app_module, "get_gateway_config", lambda: SimpleNamespace(host="127.0.0.1", port=0))
    monkeypatch.setattr(app_module, "cleanup_stale_upload_staging_files", fake_cleanup)
    monkeypatch.setattr(app_module, "langgraph_runtime", fake_runtime)
    monkeypatch.setattr(app_module, "_ensure_admin_user", fake_ensure_admin)
    monkeypatch.setattr(channel_service_module, "start_channel_service", fake_start_channels)
    monkeypatch.setattr(channel_service_module, "stop_channel_service", fake_stop_channels)
    monkeypatch.setattr(app_module.auth, "close_oidc_service", fake_close_oidc)
    monkeypatch.setattr(factory_module, "close_loop_async_clients", fake_close_clients)

    app = SimpleNamespace(state=SimpleNamespace())

    async def run():
        async with app_module.lifespan(app):
            pass

    asyncio.run(run())

    assert events == ["runtime_exit", "clients_closed"]
