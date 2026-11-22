"""Budget preparation never enables API execution or changes native role knobs."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import pytest
from report_multiturn_preflight import build_packet, verify_packet
from report_pilot_admission import reviewed_limits
from report_pilot_audit import AuditError, BudgetGuard, RateCard

ROOT = Path(__file__).resolve().parents[2]


def test_reviewed_fifty_yuan_packet_has_a_combined_envelope_and_no_api(tmp_path):
    packet = build_packet(ROOT, tmp_path / "packet", budget_cny="50", admission_review=True)
    limits = packet["limits"]
    assert limits["max_cost_cny"] == "50"
    assert limits["operating_max_cost_cny"] == "48"
    assert limits["safety_margin_cny"] == "2"
    assert limits["provider_input_context_ceiling_tokens"] == 1_048_576
    assert limits["provider_input_context_ceiling_verified"]
    assert not limits["account_billing_currency_confirmed"]
    assert limits["budget_status"] == "confirmed_ceiling_not_execution_authorization"
    assert packet["admission_review"]["combined_envelope_cny"] == "48.000"
    assert packet["roles"]["outline"]["max_tokens"] == 32768
    assert packet["roles"]["writer"]["max_tokens"] == 8192
    assert packet["roles"]["writer"]["thinking"] == {"type": "disabled"}
    assert packet["execution_entrypoint"] is None
    assert not verify_packet(ROOT, tmp_path / "packet")["api_execution_ready"]
    assert packet["api_experiments_authorized"] is False
    assert "verified provider input-context ceiling for reservations" not in packet["remaining_launch_requirements"]
    assert "confirmed account billing currency" in packet["remaining_launch_requirements"]


@pytest.mark.parametrize("budget", [None, "0", "49", "51", "NaN"])
def test_frozen_review_cannot_be_relabelled_with_another_budget(tmp_path, budget):
    with pytest.raises((ValueError, AuditError)):
        build_packet(ROOT, tmp_path / "packet", budget_cny=budget, admission_review=True)
    assert not (tmp_path / "packet").exists()


def test_all_inflight_exposure_fits_reviewed_envelope():
    from report_multiturn_preflight import profile, role_matrix

    limits, review = reviewed_limits(ROOT, "50", profile(), role_matrix(profile()))
    guard = BudgetGuard(max_cost_cny=limits["operating_max_cost_cny"], max_input_tokens=limits["max_input_tokens"], max_output_tokens=limits["max_output_tokens"])
    ceiling = limits["provider_input_context_ceiling_tokens"]
    cost = RateCard().model_cost(ceiling, 32768)
    with ThreadPoolExecutor(max_workers=11) as pool:
        list(pool.map(lambda i: guard.reserve(str(i), input_tokens=ceiling, output_tokens=32768, cost_cny=cost), range(11)))
    assert Decimal(guard.snapshot()["reserved_cost_cny"]) == Decimal(review["stress_parallel_reservation_cny"])
    assert Decimal(guard.snapshot()["reserved_cost_cny"]) < Decimal("48")
    guard.unknown("0")
    assert len(guard.snapshot()["inflight"]) == 11
    with pytest.raises(AuditError, match="stopped"):
        guard.reserve("next", input_tokens=1, output_tokens=1, cost_cny="0.01")


def test_changed_role_budget_is_not_silently_accepted():
    from report_multiturn_preflight import profile, role_matrix

    roles = role_matrix(profile())
    roles["outline"]["max_tokens"] = 384000
    with pytest.raises(ValueError, match="role"):
        reviewed_limits(ROOT, "50", profile(), roles)


def test_worker_uses_operating_limit_from_verified_reviewed_packet(tmp_path):
    from report_pilot_worker import OfflinePilotWorker
    from test_sp_report_pilot_worker import model, response

    destination = tmp_path / "packet"
    build_packet(ROOT, destination, budget_cny="50", admission_review=True)
    w = OfflinePilotWorker.from_preflight(ROOT, destination, turn=1)
    assert w.guard.maximum == Decimal("48")
    assert w.input_upper_bound == 1_048_576
    with w.installed():
        assert model(w, lambda req: response()).invoke("fixture").content == "fixture"
    assert w.finalize()["usage"]["billing_complete"]
    assert not w.finalize()["live_network_enabled"]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_gzip_stream_receipt_is_reconciled_before_sdk_close(asynchronous):
    import asyncio
    import gzip

    import httpx
    from test_sp_report_pilot_worker import model, response, worker

    body = gzip.compress(response(stream=True).content)

    class SyncStream(httpx.SyncByteStream):
        def __iter__(self):
            for offset in range(0, len(body), 7):
                yield body[offset : offset + 7]

    class AsyncStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for offset in range(0, len(body), 7):
                yield body[offset : offset + 7]

    w = worker()
    with w.installed():
        m = model(w, lambda req: httpx.Response(200, stream=AsyncStream() if asynchronous else SyncStream(), headers={"content-type": "text/event-stream", "content-encoding": "gzip"}), streaming=True)
        if asynchronous:
            assert asyncio.run(m.ainvoke("fixture")).content == "fixture"
        else:
            assert m.invoke("fixture").content == "fixture"
    assert w.finalize()["usage"]["billing_complete"]
