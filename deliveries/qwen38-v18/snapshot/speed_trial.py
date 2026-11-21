"""One generation-only trial when requested; no automatic RACE or restart."""

import fcntl
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

import run as trial
from supervision import lifecycle


def main():
    trial.support.configure_storage()
    out = trial.OUT
    with (out / "local-storage" / "supervisor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        trial.manifest()
        diagnostic = json.loads((Path(trial.manifest()['baseline']) / 'local-storage/replay-result.json').read_text())
        if not diagnostic.get('passed') or not diagnostic.get('actual_public_failed_reply_repaired_in_same_graph'):
            raise RuntimeError('v17_delivery_replay_not_verified')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:8075/metrics', timeout=10) as response:
            metrics = response.read().decode()
        active = [float(line.rsplit(' ', 1)[1]) for line in metrics.splitlines()
                  if line.startswith(('vllm:num_requests_running{', 'vllm:num_requests_waiting{'))]
        if not active or any(active):
            raise RuntimeError('service_busy_or_unmeasurable_no_generation_started')
        directory = out / "local-storage" / "runs" / trial.NAME
        if (directory / "attempt.json").exists() or (directory / "checkpoints.sqlite").exists():
            raise RuntimeError("existing_attempt_refuse_paid_restart")
        check = trial.support.preflight()
        if not check["ready"]:
            trial.save(out / "local-storage" / "status.json", dict(status="blocked_preflight", details=check, model_generation_started=False))
            print("Preflight blocked; no generation started.", flush=True)
            return 2
        with lifecycle(out / "local-storage"):
            trial.save(directory / "attempt.json", dict(started_at=trial.now(), purpose="v18_new_case10_full_pipeline_v17_delivery_repair", race_requested=False))
            trial.save(out / "local-storage" / "status.json", dict(status="running", current=trial.NAME, final_target=10000, race_requested=False))
            print("Started one v18 case10 complete generation trial; RACE disabled.", flush=True)
            native = trial.native_for(10000)
            with (directory / "worker.log").open("w") as log:
                result = native.run_worker_process(
                    [sys.executable, "-B", str(out / "run.py"), "--worker"],
                    directory=directory, log=log, timeout_seconds=trial.support.TIMEOUT + 90,
                    no_progress_seconds=1860,
                )
            trial.save(directory / "process-result.json", result)
            measured = subprocess.run([sys.executable, "-B", str(out / "run.py"), "--measure"], check=False)
            if measured.returncode:
                trial.save(out / "local-storage" / "status.json", dict(status="measurement_failed", returncode=measured.returncode, generation_restarted=False))
                return 3
            row = trial.summary()
            metrics = trial.read(directory / 'metrics.json') if (directory / 'metrics.json').exists() else {}
            row['elapsed_seconds'] = metrics.get('elapsed_seconds')
            row['complete_native_delivery'] = bool(metrics.get('overall_pass') and not metrics.get('missing_nodes'))
            row['within_30_minutes'] = bool(row['elapsed_seconds'] is not None and row['elapsed_seconds'] <= 1800)
            row['within_length_band'] = bool(row.get('words') and 9000 <= row['words'] <= 11000)
            row['speed_length_delivery_pass'] = all(row[key] for key in ('complete_native_delivery', 'within_30_minutes', 'within_length_band'))
            trial.save(directory / 'observation.json', row)
            trial.summary()
            status = 'passed' if row['speed_length_delivery_pass'] else 'completed_requires_improvement' if row.get('words') else 'stopped_without_complete_report'
            trial.save(out / "local-storage" / "status.json", dict(status=status, native_process=result, observation=row, race_requested=False, automatic_restart=False))
            print(json.dumps({"status": status, "words": row.get("words"), "generation_tokens": row.get("generation_tokens"), "race_started": False}), flush=True)
            return 0 if row.get("words") else 3


if __name__ == "__main__":
    sys.exit(main())
