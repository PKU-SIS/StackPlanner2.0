"""Full-flow preparation must not call models before the billing start gate."""

import importlib.util
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

scripts = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(scripts))
spec = importlib.util.spec_from_file_location("full_eval", scripts / "evaluate_report_full.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sys.path.pop(0)


def test_worker_refuses_to_initialize_before_off_peak(tmp_path, monkeypatch):
    monkeypatch.setattr(module.time, "time", lambda: 100)
    with pytest.raises(RuntimeError, match="Off-peak gate"):
        module.worker(tmp_path, 200, 100)
    assert not (tmp_path / "runtime").exists()


def test_trace_omits_credentials_and_provider_private_reasoning():
    message = AIMessage(content="Public answer", additional_kwargs={"reasoning_content": "private"})
    result = module.safe({"message": message, "api_key": "secret", "nested": {"reasoning": "private", "value": "kept"}})
    assert result["message"]["content"] == "Public answer"
    assert "api_key" not in result and result["nested"] == {"value": "kept"}
    assert "private" not in str(result)


def test_worker_watchdog_stops_a_silent_process_that_ignores_termination(tmp_path):
    import time

    with (tmp_path / "worker.log").open("w") as log:
        began = time.monotonic()
        result = module.run_worker_process(
            [sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"],
            directory=tmp_path,
            log=log,
            timeout_seconds=5,
            no_progress_seconds=0.5,
            poll_seconds=0.02,
            terminate_grace_seconds=0.1,
        )
    assert result["stop_reason"] == "no_progress_timeout"
    assert result["returncode"] < 0
    assert time.monotonic() - began < 3


def test_worker_watchdog_allows_progress_and_preserves_success(tmp_path):
    code = "import pathlib,time; p=pathlib.Path('events.jsonl'); " + "\nfor i in range(12):\n p.write_text(str(i)); time.sleep(0.05)"
    with (tmp_path / "worker.log").open("w") as log:
        result = module.run_worker_process(
            [sys.executable, "-c", code],
            directory=tmp_path,
            log=log,
            timeout_seconds=5,
            no_progress_seconds=0.4,
            poll_seconds=0.02,
        )
    assert result["stop_reason"] is None and result["returncode"] == 0
    assert (tmp_path / "events.jsonl").read_text() == "11"


def test_worker_watchdog_total_deadline_still_applies_with_progress(tmp_path):
    code = "import pathlib,time; p=pathlib.Path('events.jsonl'); " + "\nwhile True:\n p.write_text(str(time.time())); time.sleep(0.02)"
    with (tmp_path / "worker.log").open("w") as log:
        result = module.run_worker_process(
            [sys.executable, "-c", code],
            directory=tmp_path,
            log=log,
            timeout_seconds=0.5,
            no_progress_seconds=5,
            poll_seconds=0.02,
        )
    assert result["stop_reason"] == "hard_timeout"
