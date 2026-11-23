"""Entrypoint wiring: no paid execution, isolated synthetic HTTP only."""

import json
from dataclasses import asdict

import httpx
import pytest
from report_pilot_entry import Credentials, LivePermit, OfficialPilotWorker, credential_status, load_credentials, run_turn_sequence
from test_sp_report_pilot_worker import model, response, worker


def test_credentials_are_selected_without_env_expansion_or_secret_repr(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("LLM_API_KEY=PRIVATE_SENTINEL\nLLM_BASE_URL=https://api.deepseek.com/v1\nLLM_MODEL_BASIC=deepseek-chat\nUNRELATED_SECRET=DO_NOT_COPY\n")
    creds = load_credentials(path)
    assert creds.api_key == "PRIVATE_SENTINEL"
    assert "PRIVATE_SENTINEL" not in repr(creds)
    status = credential_status(path)
    assert status["official_endpoint"] and not status["default_model_matches_frozen"]
    assert "PRIVATE_SENTINEL" not in json.dumps(status)
    assert "DO_NOT_COPY" not in json.dumps(status)
    assert not asdict(creds).get("UNRELATED_SECRET")


@pytest.mark.parametrize("base", ["http://api.deepseek.com", "https://proxy.example.com", "https://api.deepseek.com/evil", "https://user:pass@api.deepseek.com", "https://api.deepseek.com?key=secret"])
def test_unapproved_base_url_rejected_without_leaking_value(tmp_path, base):
    path = tmp_path / ".env"
    path.write_text(f"LLM_API_KEY=secret\nLLM_BASE_URL={base}\nLLM_MODEL_BASIC=deepseek-flash\n")
    with pytest.raises(ValueError) as exc:
        load_credentials(path)
    assert base not in str(exc.value)


def test_permit_requires_explicit_execution_and_cny():
    for start, currency in [(False, "CNY"), (True, "USD"), (False, None)]:
        with pytest.raises(ValueError):
            LivePermit.create(start=start, currency=currency)


def official():
    base = worker()
    return OfficialPilotWorker(
        base.audit,
        base.guard,
        turn=1,
        input_upper_bound=1000,
        max_model_attempts=20,
        max_tool_attempts=10,
        permit=LivePermit.create(start=True, currency="CNY"),
        credentials=Credentials("offline-invalid-placeholder", "https://api.deepseek.com/v1", "deepseek-flash"),
        resolver=lambda: {"93.184.216.34"},
    )


def test_official_worker_uses_same_sdk_receipts_without_redirect_or_socket(monkeypatch):
    w = official()
    seen = []

    def handle(transport, req):
        seen.append(req.url.host)
        return response()

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle)
    m = model(w, lambda req: response())
    # Exercise the real transport type, replaced at its wire boundary.
    m.root_client._client._transport = httpx.HTTPTransport(retries=0)
    m.root_client._client._mounts = {}
    with w.installed():
        assert m.invoke("fixture").content == "fixture"
    assert seen == ["api.deepseek.com"]
    assert w.finalize()["usage"]["billing_complete"]


def test_official_scope_blocks_raw_sockets_and_transport_retries(monkeypatch):
    import socket

    w = official()
    with w.installed():
        with pytest.raises(Exception):
            socket.socket().connect(("93.184.216.34", 443))
        transport = httpx.HTTPTransport(retries=1)
        client = httpx.Client(transport=transport)
        with pytest.raises(Exception):
            client.post("https://api.deepseek.com/v1/tools/search", json={"query": "fixture"})
    assert w.receipts == []


def test_frozen_turn_sequence_stops_on_native_failure_and_never_replays():
    requests = [{"turn_index": i, "message_id": f"m{i}", "query": "fixture"} for i in range(1, 8)]
    seen = []

    def execute(request):
        seen.append(request["turn_index"])
        return {"passed": request["turn_index"] != 3, "native_delivery_created": request["turn_index"] not in (5, 6)}

    result = run_turn_sequence(requests, execute)
    assert seen == [1, 2, 3] and not result["completed"]
    seen.clear()
    result = run_turn_sequence(requests, lambda row: seen.append(row["turn_index"]) or {"passed": True})
    assert result["completed"] and seen == list(range(1, 8))


def test_official_dns_rotation_keeps_verified_public_ips_without_opening_other_hosts(monkeypatch):
    import socket

    import report_pilot_entry as entry

    w = official()
    monkeypatch.setattr(entry, "_NATIVE_GETADDRINFO", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.35", 443))])
    dialled = []
    monkeypatch.setattr(entry, "_NATIVE_CONNECT", lambda sock, address: dialled.append(address))
    with w.installed():
        socket.getaddrinfo("api.deepseek.com", 443)
        with w._wire_scope():
            with socket.socket() as sock:
                sock.connect(("93.184.216.35", 443))
        with pytest.raises(Exception):
            socket.getaddrinfo("unapproved.example", 443)
    assert dialled == [("93.184.216.35", 443)]


def test_unknown_wallet_exposure_is_never_released_or_replayed(tmp_path):
    from report_pilot_audit import BudgetGuard
    from report_pilot_entry import carry_prior_usage

    prior = {"usage": {"billing_complete": False}, "budget": {"spent_cost_cny": "2", "input_tokens": 100, "output_tokens": 10, "inflight": ["http-00000000000000000000000000000000-1"], "reserved_cost_cny": "0.5"}}
    (tmp_path / "usage.json").write_text(json.dumps(prior))
    event = {"event": "reservation", "identity": prior["budget"]["inflight"][0], "cost_cny": "0.5", "input_tokens": 1000, "output_tokens": 100}
    (tmp_path / "numeric-events.jsonl").write_text(json.dumps(event) + "\n")
    guard = BudgetGuard(max_cost_cny="3", max_input_tokens=2000, max_output_tokens=1000)
    with pytest.raises(ValueError):
        carry_prior_usage(guard, tmp_path / "usage.json")
    emitted = []
    carry_prior_usage(guard, tmp_path / "usage.json", allow_unknown=True, emit=emitted.append)
    assert str(guard.spent) == "2" and guard.snapshot()["reserved_cost_cny"] == "0.5"
    assert emitted[0]["event"] == "carried_reservation"
    with pytest.raises(Exception):
        guard.reserve("new", input_tokens=1, output_tokens=1, cost_cny="0.6")
    assert guard.snapshot()["reserved_cost_cny"] == "0.5"


def test_unbounded_unreconciled_wallet_cannot_be_carried(tmp_path):
    from report_pilot_audit import BudgetGuard
    from report_pilot_entry import carry_prior_usage

    prior = {"usage": {"billing_complete": False}, "budget": {"spent_cost_cny": "2", "input_tokens": 100, "output_tokens": 10, "inflight": [], "reserved_cost_cny": "0"}}
    (tmp_path / "usage.json").write_text(json.dumps(prior))
    guard = BudgetGuard(max_cost_cny="50", max_input_tokens=2000, max_output_tokens=1000)
    with pytest.raises(ValueError):
        carry_prior_usage(guard, tmp_path / "usage.json", allow_unknown=True)


def test_targeted_turn_scope_executes_only_the_frozen_requested_turn():
    requests = [{"turn_index": i, "message_id": f"m{i}", "query": "fixture"} for i in range(5, 8)]
    seen = []
    result = run_turn_sequence(requests, lambda row: seen.append(row["turn_index"]) or {"passed": True}, first_turn=5, last_turn=5)
    assert result["completed"] and seen == [5]
