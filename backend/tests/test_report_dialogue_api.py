"""HTTP boundary tests; native report tools are real, provider calls are absent."""

import asyncio
import json

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from test_sp_report_turns import accepted, followup
from test_sp_report_writer_first import complete, setup_edit
from test_threads_router import _write_checkpoint

from app.gateway.routers import threads
from app.gateway.services import normalize_input
from deerflow.sp.report.turns import adapter
from deerflow.sp.report.turns.store import file_manifest


def client_for(state, *, owner=True, thread_id="t1"):
    app = make_authed_test_app(owner_check_passes=owner)
    app.include_router(threads.router)
    app.state.checkpointer = InMemorySaver()
    asyncio.run(_write_checkpoint(app.state.checkpointer, thread_id, "checkpoint-1", state["messages"], step=1, channel_values=state))
    return TestClient(app)


def read(state, **kwargs):
    with client_for(state, **kwargs) as client:
        return client.get(f"/api/threads/{kwargs.get('thread_id', 't1')}/report-dialogue")


def test_delivered_origin_is_available_without_freezing_on_get(tmp_path):
    ctx, _ = accepted(tmp_path)
    before = file_manifest(tmp_path)
    response = read(ctx.state)
    assert response.status_code == 200
    value = response.json()
    assert value["available"] is True
    assert value["binding"] == {"enabled": True, "report_id": "r1"}
    assert value["current_turn"] is None and value["versions"] == []
    assert file_manifest(tmp_path) == before


def test_first_turn_and_ordinary_thread_do_not_create_version_store(tmp_path):
    state = {"messages": [], "thread_data": {"outputs_path": str(tmp_path)}}
    assert read(state).json()["available"] is False
    assert not (tmp_path / "report_dialogue").exists()


@pytest.mark.parametrize("status", ["prepared", "executing", "failed", "cancelled", "read_only", "exact_reuse"])
def test_turn_status_is_not_confused_with_delivery(tmp_path, status):
    ctx, store = accepted(tmp_path)
    first = store.freeze("r1", resolved_query="Original")
    # Exercise the normal Gateway conversion before binding the actual message.
    ctx.state["messages"] += normalize_input({"messages": [{"role": "user", "id": "m2", "content": "change", "additional_kwargs": {"report_dialogue": {"enabled": True, "report_id": "r1"}}}]})["messages"]
    assert adapter.session(ctx.state, thread_id="t1")
    if status in {"read_only", "exact_reuse"}:
        turn = store.reuse(first["turn_id"], message_id="m2", raw_query="change", response="answer", mode=status)
    else:
        turn = store.prepare(first["turn_id"], message_id="m2", raw_query="change", resolved_query="changed")
        if status == "executing":
            adapter.activate(store, turn, ctx)
        elif status in {"failed", "cancelled"}:
            store.end(turn["turn_id"], status=status, diagnostic={"error": "private diagnostic", "usage": 99})
    before = file_manifest(tmp_path)
    value = read(ctx.state).json()
    assert value["current_turn"]["status"] == status
    assert value["current_turn"]["native_delivery_created"] is False
    assert value["head_version_id"] == value["active_version_id"] == first["turn_id"]
    assert "private diagnostic" not in json.dumps(value)
    assert str(tmp_path) not in json.dumps(value)
    assert file_manifest(tmp_path) == before
    # A later ordinary question must not inherit this turn's status.
    ctx.state["messages"] += normalize_input({"messages": [{"role": "user", "id": "m3", "content": "hello"}]})["messages"]
    assert read(ctx.state).json()["current_turn"] is None


def test_real_local_edit_then_undo_then_readonly_http_status(tmp_path):
    ctx, action, task, tools, original = setup_edit(tmp_path)
    from test_sp_report_p3 import _finish

    assert json.loads(tools["report_replace_text"].invoke({"node_id": "n2", "old_text": "Seed", "new_text": "Revised"}))["status"] == "saved"
    assert not complete(ctx, action, task).error
    assert not _finish(ctx).error
    value = read(ctx.state).json()
    assert value["current_turn"]["status"] == "accepted"
    assert value["current_turn"]["native_delivery_created"] is True
    assert value["head_version_id"] == "t0002"
    store, _, _, _ = adapter.current_turn(ctx.state, thread_id="t1")
    # Whole-version undo is the existing programmatic ExactReuse operation.
    followup(ctx, text="Restore original version", message_id="m3")
    store.reuse("t0001", message_id="m3", raw_query="Restore original version", mode="exact_reuse")
    value = read(ctx.state).json()
    assert value["head_version_id"] == "t0002"
    assert value["active_version_id"] == "t0001"
    followup(ctx, text="What does the original say?", message_id="m4")
    store.reuse("t0001", message_id="m4", raw_query="What does the original say?", mode="read_only", response="Seed body")
    value = read(ctx.state).json()
    assert value["current_turn"]["status"] == "read_only"
    assert len(value["versions"]) == 2
    assert file_manifest(tmp_path / "report/r1") == original


def test_owner_decorator_denies_http_access(tmp_path):
    ctx, _ = accepted(tmp_path)
    assert read(ctx.state, owner=False).status_code == 404


def test_cross_session_index_cannot_be_projected(tmp_path):
    ctx, store = accepted(tmp_path)
    store.freeze("r1", resolved_query="original")
    response = read(ctx.state, thread_id="another")
    assert response.status_code == 409
    assert response.json()["detail"] == "Report dialogue state is unavailable"


def test_http_run_submission_preserves_binding_and_authenticated_owner(monkeypatch):
    """Real POST -> start_run -> worker arguments; worker/model execution is stubbed."""
    from types import SimpleNamespace

    from langgraph.store.memory import InMemoryStore

    from app.gateway import services
    from app.gateway.routers import thread_runs
    from deerflow.config.app_config import AppConfig, reset_app_config, set_app_config
    from deerflow.runtime import RunManager
    from deerflow.runtime.runs.store.memory import MemoryRunStore

    captured = {}

    async def capture_worker(*args, **kwargs):
        captured.update(kwargs)

    app = make_authed_test_app()
    app.include_router(thread_runs.router)
    app.state.stream_bridge = SimpleNamespace()
    app.state.run_manager = RunManager(store=MemoryRunStore())
    app.state.checkpointer = InMemorySaver()
    app.state.store = InMemoryStore()
    app.state.run_event_store = SimpleNamespace()
    app.state.run_events_config = None
    # Preserve router ownership checks; service-level store uses the same object.
    monkeypatch.setattr(services, "get_run_context", lambda request: SimpleNamespace(thread_store=request.app.state.thread_store))
    monkeypatch.setattr(services, "resolve_agent_factory", lambda _: object())
    monkeypatch.setattr(services, "run_agent", capture_worker)
    binding = {"enabled": True, "report_id": "r1"}
    set_app_config(AppConfig.model_validate({"sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"}}))
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/threads/t1/runs",
                json={
                    "input": {"messages": [{"role": "user", "id": "original-message", "content": "只改第二章的一句话", "additional_kwargs": {"report_dialogue": binding}}]},
                    "context": {"user_id": "forged-owner", "thread_id": "forged-thread"},
                },
            )
            assert response.status_code == 200

            # Await the actual background task rather than depending on scheduling.
            async def finish():
                record = await app.state.run_manager.get(response.json()["run_id"])
                await record.task

            client.portal.call(finish)
        message = captured["graph_input"]["messages"][0]
        assert message.id == "original-message"
        assert message.content == "只改第二章的一句话"
        assert message.additional_kwargs["report_dialogue"] == binding
        context = captured["config"]["context"]
        assert context["user_id"] != "forged-owner"
        assert captured["config"]["configurable"]["thread_id"] == "t1"
    finally:
        reset_app_config()
