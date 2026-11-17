"""Cancellation must stop the actual calculation process, not just its awaiter."""

import os
import sys
import threading
import time

import pytest


def test_timeout_reaps_running_calculation(tmp_path):
    from app.experiments.collusion_audit.calculation_process import run_process

    pidfile = tmp_path / "child.pid"
    command = [sys.executable, "-c", "import os,time,pathlib;pathlib.Path(" + repr(str(pidfile)) + ").write_text(str(os.getpid()));time.sleep(30)"]
    with pytest.raises(TimeoutError):
        run_process(command, tmp_path, timeout_seconds=0.5)
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_cancellation_reaps_running_calculation(tmp_path):
    from app.experiments.collusion_audit.calculation_process import CalculationCancelled, run_process

    pidfile = tmp_path / "child.pid"
    command = [sys.executable, "-c", "import os,time,pathlib;pathlib.Path(" + repr(str(pidfile)) + ").write_text(str(os.getpid()));time.sleep(30)"]
    cancelled = threading.Event()
    timer = threading.Timer(0.5, cancelled.set)
    timer.start()
    try:
        with pytest.raises(CalculationCancelled):
            run_process(command, tmp_path, timeout_seconds=10, cancel_event=cancelled)
    finally:
        timer.join()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


def test_unsuccessful_child_is_not_a_successful_empty_result(tmp_path):
    from app.experiments.collusion_audit.calculation_process import run_process

    with pytest.raises(RuntimeError, match="7"):
        run_process([sys.executable, "-c", "raise SystemExit(7)"], tmp_path, timeout_seconds=5)


def test_adapter_cancel_reaches_actual_thread_and_worker(tmp_path):
    import asyncio
    from types import SimpleNamespace

    from app.experiments.collusion_audit.calculation_process import CALCULATION_CANCEL, run_process
    from app.experiments.collusion_audit.tools import AuditTools

    events = []
    trace = SimpleNamespace(event=lambda name, **fields: events.append((name, fields)))
    adapter = AuditTools("M03", tmp_path, tmp_path / "out", tmp_path / "cache", trace)
    pidfile = tmp_path / "worker.pid"
    command = [sys.executable, "-c", "import os,time,pathlib;pathlib.Path(" + repr(str(pidfile)) + ").write_text(str(os.getpid()));time.sleep(30)"]

    def calculate():
        assert CALCULATION_CANCEL.get() is not None
        run_process(command, tmp_path / "job", timeout_seconds=10, cancel_event=CALCULATION_CANCEL.get())
        raise AssertionError("Cancelled calculation must not return a success")

    async def check():
        task = asyncio.create_task(adapter.recorded("audit_m03", {}, calculate))
        for _ in range(200):
            if pidfile.exists():
                break
            await asyncio.sleep(0.01)
        assert pidfile.exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    start = time.monotonic()
    asyncio.run(check())
    assert time.monotonic() - start < 3
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)
    assert any(name == "calculation.awaiter_cancelled" for name, fields in events)
