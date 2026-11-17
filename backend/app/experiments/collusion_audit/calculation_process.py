"""Killable host-side calculation, reusing only source-derived immutable facts."""

import argparse
import contextvars
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .knowledge import atomic

CALCULATION_CANCEL = contextvars.ContextVar("collusion_calculation_cancel", default=None)
ROOT = Path(__file__).resolve().parents[4]


class CalculationCancelled(RuntimeError):
    pass


def run_process(command, job, *, timeout_seconds=1800, cancel_event=None, on_event=None):
    job = Path(job)
    job.mkdir(parents=True, exist_ok=True)
    if cancel_event is not None and cancel_event.is_set():
        raise CalculationCancelled("Calculation cancelled before launch")
    env = {k: v for k, v in os.environ.items() if not any(x in k.upper() for x in ["KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"]) and k != "SSH_AUTH_SOCK"}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=os.pathsep.join(str(ROOT / p) for p in ["backend", "backend/packages/harness", "vendor/bid_audit_tools"]))

    def emit(event, **fields):
        record = {"event": event, "at": datetime.now(UTC).isoformat(), **fields}
        with (job / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        if on_event is not None:
            on_event(event, **fields)

    started = time.monotonic()
    with (job / "worker.log").open("a") as log:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        atomic(job / "process.json", {"pid": process.pid, "timeout_seconds": timeout_seconds})
        emit("process_started", pid=process.pid, timeout_seconds=timeout_seconds)
        try:
            while process.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    emit("cancel_requested", pid=process.pid)
                    raise CalculationCancelled("Calculation cancelled; actual worker will be terminated")
                if time.monotonic() - started >= timeout_seconds:
                    emit("deadline_reached", pid=process.pid)
                    raise TimeoutError(f"Code calculation exceeded {timeout_seconds} seconds; worker log: {job / 'worker.log'}")
                time.sleep(0.1)
            if process.returncode != 0:
                raise RuntimeError(f"Calculation worker exited with code {process.returncode}; log: {job / 'worker.log'}")
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
            emit("process_reaped", pid=process.pid, returncode=process.returncode, duration_seconds=round(time.monotonic() - started, 3))


def calculate_in_worker(rule_id, project, outputs, parse_cache, cache_root, knowledge_root, *, timeout_seconds=1800, on_event=None, general_quality=False):
    job = Path(outputs) / "calculation_jobs" / uuid.uuid4().hex
    request = {"rule_id": rule_id, "project": str(project), "outputs": str(outputs), "parse_cache": str(parse_cache), "cache_root": str(cache_root), "knowledge_root": str(knowledge_root), "general_quality": general_quality}
    atomic(job / "request.json", request)
    run_process([sys.executable, "-m", __name__, "--request", str(job / "request.json")], job, timeout_seconds=timeout_seconds, cancel_event=CALCULATION_CANCEL.get(), on_event=on_event)
    result = json.loads((job / "result.json").read_text())
    if result.get("rule_id") != rule_id or not result.get("run_id", "").startswith(rule_id + "_"):
        raise ValueError("Calculation worker returned a different rule identity")
    return result


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text())
    if request["rule_id"] not in {"M03", "M04", "M07"}:
        raise ValueError("This host-side worker is scoped to response-evidence rules")
    # Project and knowledge are explicitly host-configured read-only roots,
    # just as in the existing profile; Context validates source containment.
    # Writes remain confined to this copy or the test/temporary work area.
    allowed_writes = (ROOT, Path("/tmp"), Path(tempfile.gettempdir()).resolve())
    for name in ["outputs", "parse_cache", "cache_root"]:
        if not any(Path(request[name]).resolve().is_relative_to(base) for base in allowed_writes):
            raise ValueError("Calculation write path escaped the isolated work area")
    if not args.request.resolve().is_relative_to(Path(request["outputs"]).resolve() / "calculation_jobs"):
        raise ValueError("Calculation request is outside its host-owned job directory")
    from bid_audit.core import Context

    from .cache import calculate_cached
    from .knowledge import KnowledgeStore
    from .m01_corpus import parsed_sources

    project = Path(request["project"])
    context = Context(project / "manifest.json", request["parse_cache"], permitted_root=project)
    derived = parsed_sources(context, KnowledgeStore(request["knowledge_root"], general_quality=request.get("general_quality", False)))
    result = calculate_cached(request["rule_id"], project, request["outputs"], request["parse_cache"], request["cache_root"], parsed_texts=derived)
    atomic(args.request.parent / "result.json", result)


if __name__ == "__main__":
    main()
