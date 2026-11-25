"""Experiment-scoped worker wiring. Only HTTPX MockTransport is admitted here.

No credentials, API launcher or live-network option. The real SDK and native
executors may run inside installed(), but every outbound attempt is intercepted.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import re
import socket
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from threading import BoundedSemaphore, Lock, RLock
from uuid import uuid4

from unittest.mock import patch

import httpx
from report_pilot_audit import AuditError, PilotCallback, integer, money

_INSTALL_LOCK = Lock()


def normalized_usage(data):
    raw = data.get("usage")
    if not isinstance(raw, dict):
        raise AuditError("missing HTTP usage")
    inp, out = integer(raw["prompt_tokens"]), integer(raw["completion_tokens"])
    if integer(raw["total_tokens"]) != inp + out:
        raise AuditError("HTTP token total mismatch")
    cache = raw.get("prompt_cache_hit_tokens")
    if cache is None:
        cache = (raw.get("prompt_tokens_details") or {}).get("cached_tokens")
    if cache is not None and integer(cache) > inp:
        raise AuditError("HTTP cache exceeds input")
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": inp + out,
        "cache_read_tokens": cache,
    }


class OfflinePilotWorker:
    """Install reservations at actual send(), including retries and Timer threads.

    input_upper_bound is an explicit provider-context ceiling for reservations,
    not a char-based token estimate. Production use must independently verify it.
    This class remains network-disabled regardless of the supplied budget.
    """

    def __init__(
        self,
        audit,
        guard,
        *,
        turn,
        input_upper_bound,
        max_model_attempts,
        max_tool_attempts,
        emit=None,
        max_inflight_requests=None,
    ):
        self.audit, self.guard, self.turn = audit, guard, turn
        self.identity_prefix = uuid4().hex
        self.input_upper_bound = integer(input_upper_bound)
        self.max_model_attempts = integer(max_model_attempts)
        self.max_tool_attempts = integer(max_tool_attempts)
        self.emit = emit or (lambda row: None)
        self.source = ContextVar("pilot_http_source", default=None)
        self.lock = RLock()
        self.receipts = []
        self.stop_reason = None
        self.closed = False
        self.active = False
        self.last_completed_turn = None
        if max_inflight_requests is not None and (
            type(max_inflight_requests) is not int or max_inflight_requests < 1
        ):
            raise ValueError("max_inflight_requests must be positive")
        self.http_slots = (
            BoundedSemaphore(max_inflight_requests)
            if max_inflight_requests is not None
            else None
        )
        self.slot_holders = set()

    @classmethod
    def from_preflight(cls, root, packet_directory, *, turn, emit=None):
        """Use reviewed limits directly; budget preference alone cannot bind them.

        Verifies frozen source/environment before instantiating a still-offline
        worker. This method never changes transport admission or loads secrets.
        """
        from report_multiturn_preflight import verify_packet
        from report_pilot_admission import reviewed_limits
        from report_pilot_audit import BudgetGuard, PilotUsageAudit, RateCard

        verify_packet(root, packet_directory)
        packet = json.loads((Path(packet_directory) / "preflight.json").read_text())
        limits = packet["limits"]
        expected, review = reviewed_limits(
            root, limits["max_cost_cny"], packet["profile"], packet["roles"]
        )
        if packet.get("admission_review") != review or any(
            limits.get(k) != v for k, v in expected.items()
        ):
            raise AuditError("packet lacks matching reviewed operating limits")
        guard = BudgetGuard(
            max_cost_cny=limits["operating_max_cost_cny"],
            max_input_tokens=limits["max_input_tokens"],
            max_output_tokens=limits["max_output_tokens"],
        )
        return cls(
            PilotUsageAudit(RateCard()),
            guard,
            turn=turn,
            input_upper_bound=limits["provider_input_context_ceiling_tokens"],
            max_model_attempts=limits["max_model_http_attempts"],
            max_tool_attempts=limits["max_direct_tool_attempts"],
            emit=emit,
        )

    def _acquire_http_slot(self):
        if self.http_slots is None:
            return
        deadline = time.monotonic() + 180
        while not self.http_slots.acquire(timeout=0.05):
            if self.guard.abort_event.is_set():
                raise AuditError("worker stopped while waiting for transport")
            if time.monotonic() >= deadline:
                self.stop("transport_queue_timeout")
                raise AuditError("transport queue timed out")

    async def _acquire_async_http_slot(self):
        if self.http_slots is None:
            return
        deadline = time.monotonic() + 180
        # Do not block the event loop or leave a background acquire alive after
        # cancellation; active receipts must be able to release their slots.
        while not self.http_slots.acquire(blocking=False):
            if self.guard.abort_event.is_set():
                raise AuditError("worker stopped while waiting for transport")
            if time.monotonic() >= deadline:
                self.stop("transport_queue_timeout")
                raise AuditError("transport queue timed out")
            await asyncio.sleep(0.02)

    def _release_http_slot(self, row):
        with self.lock:
            if row["identity"] in self.slot_holders:
                self.slot_holders.remove(row["identity"])
                self.http_slots.release()

    def callback(self, role):
        return PilotCallback(self.audit, turn=self.turn, role=role)

    def stop(self, reason):
        with self.lock:
            self.stop_reason = self.stop_reason or reason
            self.guard.abort_event.set()
            self.audit.abort_event.set()

    def cancel(self):
        self.stop("cancelled")

    def _check_transport(self, client, request):
        if not isinstance(client._transport_for_url(request.url), httpx.MockTransport):
            self.stop("live_transport_forbidden")
            raise AuditError("only offline MockTransport is permitted")

    @contextmanager
    def _network_scope(self):
        def deny(*args, **kwargs):
            raise AuditError("real socket connections are forbidden")

        with (
            patch.object(socket.socket, "connect", deny),
            patch.object(socket.socket, "connect_ex", deny),
        ):
            yield

    @contextmanager
    def _wire_scope(self):
        yield

    def _send_options(self, options):
        return options

    def _wire_iter(self, stream):
        iterator = iter(stream)
        while True:
            with self._wire_scope():
                try:
                    chunk = next(iterator)
                except StopIteration:
                    return
            yield chunk

    async def _wire_aiter(self, stream):
        iterator = stream.__aiter__()
        while True:
            with self._wire_scope():
                try:
                    chunk = await anext(iterator)
                except StopAsyncIteration:
                    return
            yield chunk

    def _admit(self, client, request):
        with self.lock:
            if (
                self.closed
                or self.guard.abort_event.is_set()
                or self.audit.abort_event.is_set()
            ):
                raise AuditError("worker has stopped")
            url = request.url
            if (
                request.method != "POST"
                or url.scheme != "https"
                or url.host != "api.deepseek.com"
                or url.port not in (None, 443)
                or url.query
                or url.username
                or url.password
                or url.path
                not in {"/v1/chat/completions", "/v1/tools/search", "/v1/tools/fetch"}
            ):
                self.stop("unapproved_endpoint")
                raise AuditError("unapproved endpoint")
            self._check_transport(client, request)
            kind = (
                "model"
                if url.path.endswith("completions")
                else url.path.rsplit("/", 1)[1]
            )
            source = self.source.get() if kind == "model" else None
            if kind == "model" and (
                source is None
                or source not in self.audit.calls
                or self.audit.calls[source]["turn"] != self.turn
            ):
                self.emit(
                    {
                        "event": "source_mismatch",
                        "turn": self.turn,
                        "source_id": source,
                        "callback_turn": self.audit.calls.get(source, {}).get("turn"),
                    }
                )
                self.stop("missing_source")
                raise AuditError("model HTTP has no callback source")
            counts = sum(
                (r["kind"] == "model") == (kind == "model") for r in self.receipts
            )
            limit = (
                self.max_model_attempts if kind == "model" else self.max_tool_attempts
            )
            if counts >= limit:
                self.stop(
                    "model_attempt_limit" if kind == "model" else "tool_attempt_limit"
                )
                raise AuditError("HTTP attempt limit reached")
            try:
                payload = json.loads(request.content)
                if kind == "model":
                    if (
                        payload.get("model") != "deepseek-flash"
                        or payload.get("n", 1) != 1
                    ):
                        raise AuditError("unapproved model or candidate count")
                    out = integer(
                        payload.get("max_tokens", payload.get("max_completion_tokens"))
                    )
                    if not out:
                        raise AuditError("unbounded output")
                else:
                    out = 0
            except (ValueError, TypeError, AuditError):
                self.stop("unbounded_request")
                raise AuditError("unbounded or invalid request") from None
            inp = self.input_upper_bound if kind == "model" else 0
            cost = (
                self.audit.rates.model_cost(inp, out)
                if kind == "model"
                else self.audit.rates.direct_tool_per_success
            )
            identity = f"http-{self.identity_prefix}-{len(self.receipts) + 1}"
            try:
                self.guard.reserve(
                    identity, input_tokens=inp, output_tokens=out, cost_cny=cost
                )
            except AuditError:
                self.stop("reservation_denied")
                raise
            row = {
                "identity": identity,
                "source_id": source,
                "turn": self.turn,
                "kind": kind,
                "status": "reserved",
                "http_status": None,
                "usage": None,
                "reported_model": None,
            }
            self.receipts.append(row)
            self.emit(
                {
                    "event": "reservation",
                    "identity": identity,
                    "cost_cny": str(cost),
                    "turn": self.turn,
                    "source_id": source,
                    "kind": kind,
                    "input_tokens": inp,
                    "output_tokens": out,
                }
            )
            return row

    def _unknown(self, row):
        with self.lock:
            if row["status"] != "reserved":
                return
            row["status"] = "unknown"
            self.guard.unknown(row["identity"])
            if row["kind"] != "model":
                self.audit.tool_attempt(
                    row["identity"],
                    turn=self.turn,
                    tool=row["kind"],
                    status=row["http_status"],
                    has_result=None,
                )
            self.stop("unknown_http_outcome")
            self.emit({"event": "unknown", "identity": row["identity"]})
            self._release_http_slot(row)

    @staticmethod
    def _decoded_receipt(response, body):
        # HTTPX delivers decoded bytes to the SDK but our stream wrapper sees
        # transport bytes. Use an independent decoder; sharing the SDK decoder
        # would consume its gzip/SSE state. This Response performs no network IO.
        encoding = response.headers.get("content-encoding")
        if not encoding:
            return body
        return httpx.Response(
            200, headers={"content-encoding": encoding}, content=body
        ).content

    def _stream_has_done(self, response, chunks):
        try:
            decoded = self._decoded_receipt(response, b"".join(chunks))
            return any(
                line.startswith(b"data:") and line[5:].strip() == b"[DONE]"
                for line in decoded.splitlines()
            )
        except (ValueError, httpx.DecodingError):
            return False

    def _received(self, row, response, body, *, encoded=False):
        with self.lock:
            try:
                row["http_status"] = response.status_code
                if response.status_code != 200:
                    raise AuditError("HTTP error charge not confirmed")
                if encoded:
                    body = self._decoded_receipt(response, body)
                if row["kind"] == "model":
                    if "text/event-stream" in response.headers.get("content-type", ""):
                        records, done = [], False
                        for line in body.decode("utf-8").splitlines():
                            if line.startswith("data:"):
                                value = line[5:].strip()
                                if value == "[DONE]":
                                    done = True
                                elif value:
                                    records.append(json.loads(value))
                        matches = [x for x in records if x.get("usage") is not None]
                        if not done or len(matches) != 1:
                            raise AuditError("incomplete streaming usage")
                        data = matches[0]
                    else:
                        data = json.loads(body)
                    usage = normalized_usage(data)
                    row.update(usage=usage, reported_model=data.get("model"))
                    if row["reported_model"] not in {
                        "deepseek-flash",
                        "DeepSeek-V4.1-Flash-1017",
                        "deepseek-v4.1-flash-1017",
                    }:
                        raise AuditError("unpriced HTTP model")
                    inp, out = usage["input_tokens"], usage["output_tokens"]
                    cost = self.audit.rates.model_cost(
                        inp, out, usage["cache_read_tokens"]
                    )
                else:
                    data = json.loads(body)
                    if not isinstance(data, dict) or data.get("error"):
                        raise AuditError("invalid direct tool receipt")
                    result = (
                        data.get("results")
                        if row["kind"] == "search"
                        else data.get("content")
                    )
                    if not isinstance(result, list if row["kind"] == "search" else str):
                        raise AuditError("invalid direct tool receipt")
                    has_result = bool(
                        result if isinstance(result, list) else result.strip()
                    )
                    self.audit.tool_attempt(
                        row["identity"],
                        turn=self.turn,
                        tool=row["kind"],
                        status=200,
                        has_result=has_result,
                    )
                    inp = out = 0
                    cost = (
                        self.audit.rates.direct_tool_per_success if has_result else "0"
                    )
                self.guard.settle(
                    row["identity"], input_tokens=inp, output_tokens=out, cost_cny=cost
                )
                row["status"] = "complete"
                self.emit(
                    {
                        "event": "settlement",
                        "identity": row["identity"],
                        "cost_cny": str(cost),
                        "input_tokens": inp,
                        "output_tokens": out,
                    }
                )
            except (ValueError, TypeError, KeyError, AuditError, httpx.DecodingError):
                # Overruns are already settled and must retain their actual charge.
                if row["identity"] in self.guard.completed:
                    row["status"] = "overrun"
                    self.stop("reservation_overrun")
                    self.emit(
                        {
                            "event": "settlement",
                            "identity": row["identity"],
                            "cost_cny": str(
                                self.guard.completed[row["identity"]]["cost"]
                            ),
                            "input_tokens": self.guard.completed[row["identity"]][
                                "input_tokens"
                            ],
                            "output_tokens": self.guard.completed[row["identity"]][
                                "output_tokens"
                            ],
                        }
                    )
                else:
                    self._unknown(row)
                raise AuditError("HTTP receipt could not be safely settled") from None
            finally:
                self._release_http_slot(row)

    @contextmanager
    def installed(self):
        """Dedicated experiment process only; restore all hooks even on failure."""
        if self.active or self.closed:
            raise AuditError("worker installation cannot be reused")
        if not _INSTALL_LOCK.acquire(blocking=False):
            raise AuditError("only one experiment installation per process is allowed")
        original_send, original_async = httpx.Client.send, httpx.AsyncClient.send
        original_start, original_end, original_error = (
            PilotCallback.on_chat_model_start,
            PilotCallback.on_llm_end,
            PilotCallback.on_llm_error,
        )
        worker = self

        def start(callback, serialized, messages, *, run_id, **kwargs):
            original_start(callback, serialized, messages, run_id=run_id, **kwargs)
            if callback.audit is worker.audit:
                worker.source.set(str(run_id))

        def end(callback, *args, **kwargs):
            try:
                return original_end(callback, *args, **kwargs)
            finally:
                if callback.audit is worker.audit:
                    worker.source.set(None)

        def error(callback, *args, **kwargs):
            try:
                return original_error(callback, *args, **kwargs)
            finally:
                if callback.audit is worker.audit:
                    worker.source.set(None)

        class SyncReceipt(httpx.SyncByteStream):
            def __init__(self, original, row, response):
                self.original, self.row, self.response = original, row, response
                self.chunks = []

            def __iter__(self):
                chunks = self.chunks
                try:
                    for chunk in worker._wire_iter(self.original):
                        chunks.append(chunk)
                        yield chunk
                    worker._received(
                        self.row, self.response, b"".join(chunks), encoded=True
                    )
                except BaseException:
                    worker._unknown(self.row)
                    raise

            def close(self):
                try:
                    if self.row["status"] == "reserved" and worker._stream_has_done(
                        self.response, self.chunks
                    ):
                        worker._received(
                            self.row, self.response, b"".join(self.chunks), encoded=True
                        )
                finally:
                    with worker._wire_scope():
                        self.original.close()
                    worker._unknown(self.row)

        class AsyncReceipt(httpx.AsyncByteStream):
            def __init__(self, original, row, response):
                self.original, self.row, self.response = original, row, response
                self.chunks = []

            async def __aiter__(self):
                chunks = self.chunks
                try:
                    async for chunk in worker._wire_aiter(self.original):
                        chunks.append(chunk)
                        yield chunk
                    worker._received(
                        self.row, self.response, b"".join(chunks), encoded=True
                    )
                except BaseException:
                    # Async SDKs can close this iterator after receiving DONE,
                    # before it resumes past the last yield. Settle complete
                    # buffered wire evidence before treating closure as unknown.
                    if self.row["status"] == "reserved" and worker._stream_has_done(
                        self.response, self.chunks
                    ):
                        worker._received(
                            self.row, self.response, b"".join(self.chunks), encoded=True
                        )
                    worker._unknown(self.row)
                    raise

            async def aclose(self):
                try:
                    if self.row["status"] == "reserved" and worker._stream_has_done(
                        self.response, self.chunks
                    ):
                        worker._received(
                            self.row, self.response, b"".join(self.chunks), encoded=True
                        )
                finally:
                    with worker._wire_scope():
                        await self.original.aclose()
                    worker._unknown(self.row)

        def send(client, request, *, stream=False, **kwargs):
            worker._acquire_http_slot()
            try:
                row = worker._admit(client, request)
            except BaseException:
                if worker.http_slots is not None:
                    worker.http_slots.release()
                raise
            if worker.http_slots is not None:
                with worker.lock:
                    worker.slot_holders.add(row["identity"])
            try:
                with worker._wire_scope():
                    result = original_send(
                        client, request, stream=True, **worker._send_options(kwargs)
                    )
                row["http_status"] = result.status_code
                if result.is_stream_consumed:
                    worker._received(row, result, result.content)
                else:
                    result.stream = SyncReceipt(result.stream, row, result)
                if not stream:
                    result.read()
                return result
            except BaseException:
                worker._unknown(row)
                raise

        async def asend(client, request, *, stream=False, **kwargs):
            await worker._acquire_async_http_slot()
            try:
                row = worker._admit(client, request)
            except BaseException:
                if worker.http_slots is not None:
                    worker.http_slots.release()
                raise
            if worker.http_slots is not None:
                with worker.lock:
                    worker.slot_holders.add(row["identity"])
            try:
                with worker._wire_scope():
                    result = await original_async(
                        client, request, stream=True, **worker._send_options(kwargs)
                    )
                row["http_status"] = result.status_code
                if result.is_stream_consumed:
                    worker._received(row, result, result.content)
                else:
                    result.stream = AsyncReceipt(result.stream, row, result)
                if not stream:
                    await result.aread()
                return result
            except BaseException:
                worker._unknown(row)
                raise

        self.active = True
        try:
            with (
                patch.object(httpx.Client, "send", send),
                patch.object(httpx.AsyncClient, "send", asend),
                patch.object(PilotCallback, "on_chat_model_start", start),
                patch.object(PilotCallback, "on_llm_end", end),
                patch.object(PilotCallback, "on_llm_error", error),
                self._network_scope(),
            ):
                yield self
        finally:
            self.active = False
            self.closed = True
            try:
                for row in self.receipts:
                    if row["status"] == "reserved":
                        self._unknown(row)
            finally:
                _INSTALL_LOCK.release()

    @contextmanager
    def memory_callbacks(self, updater_module):
        """Bind callbacks in the real Timer thread at model creation time."""
        original = updater_module.MemoryUpdater._get_model
        worker = self

        def get_model(updater):
            return original(updater).with_config(
                callbacks=[worker.callback("memory_updater")]
            )

        with patch.object(updater_module.MemoryUpdater, "_get_model", get_model):
            yield

    def run_turn(
        self, operation, *, turn, executor_module, updater_module, memory_queue
    ):
        """Run the unchanged caller graph; attach/drain numeric observers per turn.

        operation receives a root callback for the normal graph config. It owns
        graph inputs and report delivery; this adapter makes no agent decisions.
        One worker spans the case so attempt limits and reservations never reset.
        """
        from report_pilot_audit import observe_native_subagents

        if (
            not self.active
            or self.closed
            or self.guard.abort_event.is_set()
            or self.audit.abort_event.is_set()
        ):
            raise AuditError("worker is not available for a turn")
        if (
            type(turn) is not int
            or turn < 1
            or self.last_completed_turn is not None
            and turn != self.last_completed_turn + 1
        ):
            raise AuditError("turns must be sequential and cannot be replayed")
        if memory_queue.pending_count or memory_queue.is_processing:
            self.stop("previous_memory_not_drained")
            raise AuditError("previous memory work must drain before changing turns")
        if any(row["status"] != "complete" for row in self.receipts):
            self.stop("previous_http_not_settled")
            raise AuditError("previous HTTP attempts must settle before a new turn")
        self.turn = turn
        self.emit({"event": "turn_started", "turn": turn})
        with (
            observe_native_subagents(executor_module, self.audit, turn=turn),
            self.memory_callbacks(updater_module),
        ):
            try:
                result = operation(self.callback("central"))
            except BaseException:
                self.stop("turn_execution_failed")
                raise
            finally:
                self.drain_memory(
                    memory_queue, timeout=getattr(self, "memory_drain_timeout", 10)
                )
        if self.audit.abort_event.is_set() or self.guard.abort_event.is_set():
            raise AuditError("turn stopped with incomplete accounting")
        self.last_completed_turn = turn
        return result

    def drain_memory(self, queue, *, timeout=10):
        """Do not remove HTTP interception while a queued Timer call is running."""
        deadline = time.monotonic() + timeout
        queue.flush_nowait()
        while queue.pending_count or queue.is_processing:
            if time.monotonic() >= deadline:
                self.stop("memory_drain_timeout")
                raise AuditError("memory did not drain before deadline")
            time.sleep(0.01)

    def finalize(self):
        with self.lock:
            model_rows = [r for r in self.receipts if r["kind"] == "model"]
            unknown = [
                r["identity"] for r in self.receipts if r["status"] != "complete"
            ]
            sources = [r["source_id"] for r in model_rows]
            mismatch = len(sources) != len(set(sources))
            for row in model_rows:
                callback = self.audit.calls.get(row["source_id"], {})
                observed, receipt = callback.get("usage"), row["usage"]
                mismatch |= callback.get("reported_model") != row["reported_model"]
                if observed is None or receipt is None:
                    mismatch = True
                else:
                    mismatch |= any(
                        observed[k] != receipt[k]
                        for k in ("input_tokens", "output_tokens", "total_tokens")
                    )
                    # The frozen SDK drops DeepSeek's top-level cache-hit field.
                    # Missing cache remains unknown in callbacks and is priced
                    # conservatively there; keep the exact HTTP receipt separate.
                    if observed["cache_read_tokens"] is not None:
                        mismatch |= (
                            observed["cache_read_tokens"]
                            != receipt["cache_read_tokens"]
                        )
            try:
                self.audit.reconcile_model_requests(
                    sources,
                    unknown_http_attempts=unknown
                    + (["receipt_callback_disagreement"] if mismatch else []),
                )
            except AuditError:
                self.stop("usage_reconciliation_failed")
            return {
                "api_experiments_authorized": False,
                "live_network_enabled": False,
                "native_delivery_verified": False,
                "usage": self.audit.snapshot(),
                "global_billing_complete": self.audit.snapshot()["billing_complete"]
                and not self.guard.pending,
                "budget": self.guard.snapshot(),
                "http_attempts": list(self.receipts),
                "stop_reason": self.stop_reason,
            }


def _child(target, channel):
    def deny(*args, **kwargs):
        raise AuditError("supervised preparation forbids network connections")

    try:
        # This is a fresh dedicated child: keep denial installed until process
        # exit, including late SDK retries or memory/executor cleanup threads.
        socket.socket.connect = deny
        socket.socket.connect_ex = deny
        channel.send({"event": "ready"})
        target(channel)
        channel.send({"event": "completed"})
    except BaseException:
        channel.send({"event": "failed"})  # no raw exception text
    finally:
        channel.close()


def supervise(
    target,
    journal,
    *,
    suite_seconds=7200,
    turn_seconds=1500,
    idle_seconds=300,
    cancel_event=None,
    api_execution_authorized=False,
):
    """External monotonic supervisor; journal survives kill; never auto-restart.

    Completion means only that the callable returned, not report delivery/billing.
    All child targets run with real sockets denied, including uninstrumented code.
    """
    limits = (suite_seconds, turn_seconds, idle_seconds)
    if any(not isinstance(x, (int, float)) or not 0 < x < float("inf") for x in limits):
        raise ValueError("positive finite supervisor deadlines required")
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_child, args=(target, child))
    started = last = turn_started = time.monotonic()
    reason, ready, complete = None, False, False
    journal = Path(journal)
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open("x", encoding="utf-8") as output:

        def persist(event):
            # Persist only the numeric protocol, including events queued just
            # before termination. Never serialize arbitrary child diagnostics.
            try:
                if (
                    not isinstance(event, dict)
                    or set(event)
                    - {
                        "event",
                        "turn",
                        "identity",
                        "cost_cny",
                        "source_id",
                        "kind",
                        "input_tokens",
                        "output_tokens",
                    }
                    or event.get("event")
                    not in {
                        "ready",
                        "turn_started",
                        "reservation",
                        "carried_reservation",
                        "settlement",
                        "unknown",
                        "completed",
                        "failed",
                    }
                ):
                    return False
                if "identity" in event and not re.fullmatch(
                    r"http-[a-f0-9]{32}-[1-9][0-9]*", str(event["identity"])
                ):
                    return False
                if event.get("source_id") is not None and not re.fullmatch(
                    r"[a-f0-9-]{36}", str(event["source_id"])
                ):
                    return False
                if "kind" in event and event["kind"] not in {
                    "model",
                    "search",
                    "fetch",
                }:
                    return False
                for key in ("turn", "input_tokens", "output_tokens"):
                    if key in event:
                        integer(event[key])
                if "cost_cny" in event:
                    money(event["cost_cny"])
            except (ValueError, TypeError, AuditError):
                return False
            output.write(json.dumps(event) + "\n")
            output.flush()
            os.fsync(output.fileno())
            return True

        process.start()
        child.close()
        try:
            while True:
                now = time.monotonic()
                if cancel_event is not None and cancel_event.is_set():
                    reason = "cancelled"
                elif now - started >= suite_seconds:
                    reason = "suite_timeout"
                elif ready and now - turn_started >= turn_seconds:
                    reason = "turn_timeout"
                elif ready and now - last >= idle_seconds:
                    reason = "no_progress_timeout"
                if reason:
                    break
                if parent.poll(0.02):
                    try:
                        event = parent.recv()
                    except EOFError:
                        break
                    if not persist(event):
                        reason = "invalid_worker_event"
                        break
                    last = time.monotonic()
                    if event["event"] in {"ready", "turn_started"}:
                        ready = True
                        turn_started = last
                    if event["event"] in {"completed", "failed"}:
                        complete = event["event"] == "completed"
                        reason = "worker_returned" if complete else "worker_failed"
                        break
                elif not process.is_alive():
                    break
        finally:
            if process.is_alive():
                process.join(0.1 if complete else 0)
            terminated = process.is_alive()
            if terminated:
                process.terminate()
                process.join(1)
                if process.is_alive():
                    process.kill()
                    process.join(1)
            # A timeout may race with a just-sent reservation. Drain the pipe
            # after stopping the producer so that exposure is never discarded.
            while parent.poll():
                try:
                    if not persist(parent.recv()):
                        reason = "invalid_worker_event"
                except (EOFError, OSError):
                    break
            process.join(0)
            parent.close()
        result = {
            "stop_reason": reason or "worker_exited_without_completion",
            "worker_terminated": terminated,
            "worker_callable_completed": complete,
            "native_delivery_verified": False,
            "billing_complete": False,
            "automatic_restarts": 0,
            "api_experiments_authorized": bool(api_execution_authorized),
        }
        output.write(json.dumps({"supervisor": result}) + "\n")
        output.flush()
        os.fsync(output.fileno())
    return result
