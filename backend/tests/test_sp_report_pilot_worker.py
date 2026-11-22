"""Real SDK/HTTPX wiring against local mock transports, with sockets denied."""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from langchain_deepseek import ChatDeepSeek
from report_pilot_audit import AuditError, BudgetGuard, PilotUsageAudit, RateCard
from report_pilot_worker import OfflinePilotWorker, supervise


def response(*, stream=False, usage=True):
    data = {"id": "fixture", "object": "chat.completion", "model": "deepseek-flash", "choices": [{"index": 0, "message": {"role": "assistant", "content": "fixture"}, "finish_reason": "stop"}]}
    if usage:
        data["usage"] = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120, "prompt_cache_hit_tokens": 40}
    if stream:
        first = {"id": "fixture", "object": "chat.completion.chunk", "model": "deepseek-flash", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "fixture"}, "finish_reason": "stop"}]}
        last = {"id": "fixture", "object": "chat.completion.chunk", "model": "deepseek-flash", "choices": [], **({"usage": data["usage"]} if usage else {})}
        return httpx.Response(200, content="".join("data: " + json.dumps(x) + "\n\n" for x in [first, last]) + "data: [DONE]\n\n", headers={"content-type": "text/event-stream"})
    return httpx.Response(200, json=data)


def worker(**kwargs):
    rates = RateCard()
    guard = BudgetGuard(max_cost_cny=kwargs.pop("budget", "1"), max_input_tokens=10000, max_output_tokens=10000)
    return OfflinePilotWorker(PilotUsageAudit(rates), guard, turn=2, input_upper_bound=1000, max_model_attempts=30, max_tool_attempts=10, **kwargs)


def model(w, handler, *, streaming=False):
    transport = httpx.MockTransport(handler)
    return ChatDeepSeek(
        model="deepseek-flash",
        api_key="offline-invalid-placeholder",
        max_tokens=100,
        max_retries=2,
        streaming=streaming,
        http_client=httpx.Client(transport=transport),
        http_async_client=httpx.AsyncClient(transport=transport),
        callbacks=[w.callback("central")],
    )


@pytest.mark.parametrize("streaming", [False, True])
def test_actual_sdk_sync_streaming_and_receipt_callback_join(streaming):
    w = worker()
    original = httpx.Client.send
    with w.installed():
        m = model(w, lambda req: response(stream=streaming), streaming=streaming)
        result = m.invoke("fixture")
        assert result.content == "fixture"
    assert httpx.Client.send is original
    report = w.finalize()
    assert report["usage"]["billing_complete"]
    assert report["usage"]["total_tokens"] == 120
    assert report["budget"]["inflight"] == []
    assert len(report["http_attempts"]) == 1
    assert report["http_attempts"][0]["source_id"] in w.audit.calls
    assert "fixture" not in json.dumps(report)  # no prompts, model output or headers
    assert report["api_experiments_authorized"] is False


def test_actual_sdk_async_receipt_join():
    w = worker()
    with w.installed():
        m = model(w, lambda req: response())
        assert asyncio.run(m.ainvoke("fixture")).content == "fixture"
    assert w.finalize()["usage"]["billing_complete"]


def test_unknown_transport_stops_native_sdk_retry_and_preserves_reservation():
    sent = []

    def broken(req):
        sent.append(1)
        raise httpx.ReadTimeout("secret must not be retained")

    w = worker()
    with w.installed():
        with pytest.raises(Exception):
            model(w, broken).invoke("fixture")
    report = w.finalize()
    assert len(sent) == 1
    assert report["budget"]["inflight"]
    assert not report["usage"]["billing_complete"]
    assert "secret" not in json.dumps(report)


def test_budget_denies_before_any_http_transport():
    sent = []
    w = worker(budget=None)
    with w.installed():
        with pytest.raises(Exception):
            model(w, lambda req: sent.append(1) or response()).invoke("fixture")
    assert sent == []
    assert not w.finalize()["usage"]["billing_complete"]


@pytest.mark.parametrize("streaming", [False, True])
def test_missing_http_usage_keeps_reservation_and_unknown_total(streaming):
    w = worker()
    with w.installed():
        with pytest.raises(Exception):
            model(w, lambda req: response(stream=streaming, usage=False), streaming=streaming).invoke("fixture")
    report = w.finalize()
    assert report["budget"]["inflight"]
    assert not report["usage"]["billing_complete"]


def test_unattributed_model_and_unapproved_endpoint_block_before_transport():
    sent = []
    w = worker()
    with w.installed():
        client = httpx.Client(transport=httpx.MockTransport(lambda req: sent.append(1) or response()))
        with pytest.raises(AuditError, match="source"):
            client.post("https://api.deepseek.com/v1/chat/completions", json={"max_tokens": 10})
        with pytest.raises(AuditError, match="stopped"):
            client.get("https://example.com")
    assert sent == []


def test_direct_tools_success_empty_and_failure_are_http_receipts():
    w = worker()
    with w.installed():
        client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"results": []} if b"empty" in req.content else {"results": [{"url": "https://example.com"}]})))
        client.post("https://api.deepseek.com/v1/tools/search", json={"query": "test"})
        client.post("https://api.deepseek.com/v1/tools/search", json={"query": "empty"})
    data = w.finalize()
    assert data["usage"]["direct_tool_successes"] == 1
    assert data["usage"]["direct_tool_cost_cny_observed"] == "0.005"
    assert data["usage"]["billing_complete"]


def test_http_and_callback_disagreement_never_billing_complete():
    w = worker()
    with w.installed():
        m = model(w, lambda req: response())
        m.invoke("fixture")
    key = next(iter(w.audit.calls))
    w.audit.calls[key]["usage"]["output_tokens"] += 1
    assert not w.finalize()["usage"]["billing_complete"]


def test_cancel_and_attempt_limit_block_later_requests():
    w = worker()
    w.cancel()
    with w.installed():
        with pytest.raises(Exception):
            model(w, lambda req: pytest.fail("transport called after cancel")).invoke("fixture")
    assert w.finalize()["stop_reason"] == "cancelled"
    w = worker()
    w.max_model_attempts = 1
    sent = []
    with w.installed():
        m = model(w, lambda req: sent.append(1) or response())
        m.invoke("fixture")
        with pytest.raises(Exception):
            m.invoke("fixture")
    assert sent == [1]
    assert w.finalize()["stop_reason"] == "model_attempt_limit"


def test_concurrent_model_contexts_never_share_source_identity():
    w = worker()
    with w.installed():
        m = model(w, lambda req: response())
        with ThreadPoolExecutor(max_workers=3) as pool:
            assert len(list(pool.map(m.invoke, ["a", "b", "c"]))) == 3
    data = w.finalize()
    assert data["usage"]["billing_complete"]
    assert len({r["source_id"] for r in data["http_attempts"]}) == 3


def sleeping_child(channel):
    channel.send({"event": "turn_started", "turn": 1})
    channel.send({"event": "reservation", "identity": "http-00000000000000000000000000000000-1", "cost_cny": "0.1"})
    time.sleep(5)


def test_external_supervisor_stops_blocked_worker_and_preserves_exposure(tmp_path):
    result = supervise(sleeping_child, tmp_path / "events.jsonl", suite_seconds=2, turn_seconds=2, idle_seconds=0.2)
    assert result["stop_reason"] == "no_progress_timeout"
    assert result["worker_terminated"]
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert any(e.get("identity") == "http-00000000000000000000000000000000-1" for e in events)
    assert result["billing_complete"] is False
    assert result["automatic_restarts"] == 0


def test_native_isolated_executor_joins_real_sdk_http_to_git_role(monkeypatch):
    import importlib
    import sys

    from report_pilot_audit import observe_native_subagents
    from test_sp_report_executor_e2e import _app_config

    from deerflow.subagents.config import SubagentConfig

    key = "deerflow.subagents.executor"
    previous = sys.modules.pop(key, None)
    module = importlib.import_module(key)
    w = worker()
    transport = httpx.MockTransport(lambda req: response())
    m = ChatDeepSeek(model="deepseek-flash", api_key="offline-invalid-placeholder", max_tokens=100, max_retries=2, http_client=httpx.Client(transport=transport), http_async_client=httpx.AsyncClient(transport=transport))
    monkeypatch.setattr(module, "create_chat_model", lambda **kwargs: m)
    records = []
    try:
        cfg = SubagentConfig(name="sp-perception", description="Offline HTTP join", tools=["report_inspect_version", "report_prepare_turn", "report_reuse_version"], skills=[])
        executor = module.SubagentExecutor(config=cfg, tools=[], app_config=_app_config(), parent_model="fake-model", thread_id="http-join", run_id="http-join", force_isolated_loop=True, token_usage_observer=records.append)
        with w.installed(), observe_native_subagents(module, w.audit, turn=2):
            result = executor.execute("Return a synthetic fixture")
        assert result.status.value == "completed"
        data = w.finalize()
        assert data["usage"]["billing_complete"]
        assert data["usage"]["by_role"] == {"git_agent": 1}
        assert data["http_attempts"][0]["source_id"] == records[0]["source_run_id"]
    finally:
        if previous is not None:
            sys.modules[key] = previous
        else:
            sys.modules.pop(key, None)


def test_real_memory_queue_timer_model_is_included_and_drained(monkeypatch):
    from langchain_core.messages import HumanMessage

    from deerflow.agents.memory import queue as queue_module
    from deerflow.agents.memory import updater
    from deerflow.config.memory_config import MemoryConfig

    w = worker()
    transport = httpx.MockTransport(lambda req: response())
    m = ChatDeepSeek(model="deepseek-flash", api_key="offline-invalid-placeholder", max_tokens=100, http_client=httpx.Client(transport=transport), http_async_client=httpx.AsyncClient(transport=transport))
    monkeypatch.setattr(updater, "create_chat_model", lambda **kw: m)
    monkeypatch.setattr(queue_module, "get_memory_config", lambda: MemoryConfig(enabled=True, debounce_seconds=60))
    monkeypatch.setattr(updater, "get_memory_config", lambda: MemoryConfig(enabled=True))
    # Isolate memory files; actual queue, Timer, updater invocation and SDK remain real.
    monkeypatch.setattr(updater.MemoryUpdater, "_prepare_update_prompt", lambda self, **kw: ({}, "synthetic memory input"))
    completed = []
    monkeypatch.setattr(updater.MemoryUpdater, "_finalize_update", lambda self, **kw: completed.append(1) or True)
    queue = queue_module.MemoryUpdateQueue()
    original = updater.MemoryUpdater._get_model
    try:
        with w.installed(), w.memory_callbacks(updater):
            queue.add("isolated-memory", [HumanMessage(content="synthetic input")])
            w.drain_memory(queue, timeout=2)
            assert not queue.is_processing and queue.pending_count == 0
        assert updater.MemoryUpdater._get_model is original
        data = w.finalize()
        assert completed == [1]
        assert data["usage"]["billing_complete"]
        assert data["usage"]["by_role"] == {"memory_updater": 1}
    finally:
        queue.clear()


def test_model_receipt_overrun_retains_actual_charge():
    w = worker()
    w.input_upper_bound = 1
    with w.installed():
        with pytest.raises(Exception):
            model(w, lambda req: response()).invoke("fixture")
    data = w.finalize()
    assert data["budget"]["input_tokens"] == 100
    assert float(data["budget"]["spent_cost_cny"]) > 0
    assert data["stop_reason"] == "reservation_overrun"
    assert not data["usage"]["billing_complete"]


def test_true_stream_consumption_abandonment_keeps_reservation():
    w = worker()

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"id":"partial","choices":[{"index":0,"delta":{"content":"partial"}}]}\n\n'

    with w.installed():
        m = model(w, lambda req: httpx.Response(200, stream=Stream(), headers={"content-type": "text/event-stream"}), streaming=True)
        stream = m.stream("fixture")
        next(stream)
        stream.close()
    data = w.finalize()
    assert data["budget"]["inflight"]
    assert not data["usage"]["billing_complete"]


def test_live_http_client_and_redirect_endpoint_are_blocked_without_connection():
    for client, url in [(httpx.Client(), "https://api.deepseek.com/v1/tools/search"), (httpx.Client(transport=httpx.MockTransport(lambda req: pytest.fail("forbidden endpoint sent"))), "https://example.com")]:
        w = worker()
        with w.installed():
            with pytest.raises(AuditError):
                client.post(url, json={"query": "fixture"})
        assert w.receipts == []
        client.close()


def test_async_stream_is_parsed_incrementally_and_joined():
    w = worker()
    body = response(stream=True).content

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for i in range(0, len(body), 7):
                yield body[i : i + 7]

    async def run():
        m = model(w, lambda req: httpx.Response(200, stream=Stream(), headers={"content-type": "text/event-stream"}), streaming=True)
        return await m.ainvoke("fixture")

    with w.installed():
        assert asyncio.run(run()).content == "fixture"
    assert w.finalize()["usage"]["billing_complete"]


def quick_child(channel):
    channel.send({"event": "turn_started", "turn": 1})


def test_supervisor_callable_completion_is_not_delivery_or_billing(tmp_path):
    result = supervise(quick_child, tmp_path / "events.jsonl", suite_seconds=10, turn_seconds=2, idle_seconds=2)
    assert result["worker_callable_completed"]
    assert not result["native_delivery_verified"] and not result["billing_complete"]


@pytest.mark.parametrize("limit,expected", [("suite", "suite_timeout"), ("turn", "turn_timeout")])
def test_supervisor_enforces_absolute_deadlines(tmp_path, limit, expected):
    result = supervise(sleeping_child, tmp_path / "events.jsonl", suite_seconds=1.5 if limit == "suite" else 10, turn_seconds=0.15 if limit == "turn" else 10, idle_seconds=10)
    assert result["stop_reason"] == expected
    assert not result["worker_callable_completed"]


def test_supervisor_cancellation_stops_without_restart(tmp_path):
    import threading

    cancelled = threading.Event()
    cancelled.set()
    result = supervise(sleeping_child, tmp_path / "events.jsonl", cancel_event=cancelled)
    assert result["stop_reason"] == "cancelled"
    assert result["automatic_restarts"] == 0


def test_two_turn_adapter_keeps_attempt_inventory_and_memory_turn_binding(monkeypatch):
    import importlib
    import sys

    from langchain_core.messages import HumanMessage

    from deerflow.agents.memory import queue as queue_module
    from deerflow.agents.memory import updater
    from deerflow.config.memory_config import MemoryConfig

    key = "deerflow.subagents.executor"
    previous = sys.modules.pop(key, None)
    module = importlib.import_module(key)
    w = worker()
    queue = queue_module.MemoryUpdateQueue()
    transport = httpx.MockTransport(lambda req: response())

    def make_model(callbacks=None):
        return ChatDeepSeek(model="deepseek-flash", api_key="offline-invalid-placeholder", max_tokens=100, http_client=httpx.Client(transport=transport), http_async_client=httpx.AsyncClient(transport=transport), callbacks=callbacks)

    monkeypatch.setattr(updater, "create_chat_model", lambda **kw: make_model())
    monkeypatch.setattr(updater, "get_memory_config", lambda: MemoryConfig(enabled=True))
    monkeypatch.setattr(queue_module, "get_memory_config", lambda: MemoryConfig(enabled=True, debounce_seconds=60))
    monkeypatch.setattr(updater.MemoryUpdater, "_prepare_update_prompt", lambda self, **kw: ({}, "synthetic memory"))
    monkeypatch.setattr(updater.MemoryUpdater, "_finalize_update", lambda self, **kw: True)

    def operation(callback):
        result = make_model([callback]).invoke("fixture")
        queue.add("isolated", [HumanMessage(content="fixture")])
        return result.content

    try:
        with w.installed():
            for turn in [1, 2]:
                assert w.run_turn(operation, turn=turn, executor_module=module, updater_module=updater, memory_queue=queue) == "fixture"
            with pytest.raises(AuditError, match="sequential"):
                w.run_turn(operation, turn=2, executor_module=module, updater_module=updater, memory_queue=queue)
        data = w.finalize()
        assert data["usage"]["billing_complete"]
        assert data["usage"]["by_role"] == {"central": 2, "memory_updater": 2}
        assert data["usage"]["tokens_by_turn"]["1"]["total_tokens"] == 240
        assert data["usage"]["tokens_by_turn"]["2"]["total_tokens"] == 240
        assert len(data["http_attempts"]) == 4
    finally:
        queue.clear()
        if previous is not None:
            sys.modules[key] = previous
        else:
            sys.modules.pop(key, None)


def test_tool_retry_on_http_503_stops_before_second_charge(monkeypatch):
    from deerflow.community.deepseek_tools import tools

    w = worker()
    sent = []
    original_client = httpx.Client

    def client(**kwargs):
        return original_client(transport=httpx.MockTransport(lambda req: sent.append(1) or httpx.Response(503, json={"error": {"code": "temporary"}})), **kwargs)

    monkeypatch.setattr(tools, "_options", lambda name: {"api_key": "offline-invalid-placeholder"})
    monkeypatch.setattr(tools.httpx, "Client", client)
    # Patch the actual class separately: tools.httpx is the same global module.
    # installed() expects a Client class, so restore the symbol during installation.
    monkeypatch.setattr(httpx, "Client", original_client)
    with w.installed():
        monkeypatch.setattr(tools.httpx, "Client", client)
        with pytest.raises(AuditError):
            tools._post("web_search", "search", {"query": "fixture"})
        monkeypatch.setattr(httpx, "Client", original_client)
    assert sent == [1]
    assert w.finalize()["budget"]["inflight"]


def malicious_child(channel):
    channel.send({"event": "reservation", "identity": "credential-must-never-be-written", "cost_cny": "0.1"})


def test_supervisor_rejects_non_numeric_child_log_fields(tmp_path):
    path = tmp_path / "events.jsonl"
    result = supervise(malicious_child, path, suite_seconds=10)
    assert result["stop_reason"] == "invalid_worker_event"
    assert "credential" not in path.read_text()


def test_preflight_freezes_worker_and_leaves_context_ceiling_unconfirmed(tmp_path):
    from report_multiturn_preflight import build_packet

    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    packet = build_packet(root, tmp_path / "packet")
    assert "scripts/report_pilot_worker.py" in packet["engine_sha256"]
    assert packet["limits"]["provider_input_context_ceiling_tokens"] is None
    assert not packet["limits"]["provider_input_context_ceiling_verified"]
    assert packet["api_experiments_authorized"] is False
