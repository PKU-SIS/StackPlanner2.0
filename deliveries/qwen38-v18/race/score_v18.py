"""Score one unchanged v18 report with the existing pinned RACE protocol."""

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

LOCAL = Path(__file__).resolve().parent
WORK = LOCAL.parent.parent
EVAL = LOCAL / "race-evaluation"
SOURCES = {10: "qwen38-newcase-v18-20261119", 17: "qwen38-v18-lowcode-v22-20261119"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def now():
    return datetime.now(timezone.utc).isoformat()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def score(case):
    source = WORK / SOURCES[case]
    name = f"band10000-case{case}-outline-selected"
    target = EVAL / "race" / name
    target.mkdir(parents=True, exist_ok=True)
    for key, suffix in {"TMPDIR": "tmp", "TMP": "tmp", "TEMP": "tmp", "XDG_CACHE_HOME": "cache",
                        "HF_HOME": "cache/hf", "MPLCONFIGDIR": "cache/matplotlib"}.items():
        folder = EVAL / suffix
        folder.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(folder)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    os.chdir(EVAL)
    with (target / "score.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        trial = load(f"v18_trial_{case}", source / "run.py")
        manifest = trial.manifest()
        cell = manifest["cells"][0]
        assert cell["case_id"] == case and cell["name"] == name
        trial.support.validate_race_inputs(cell)
        run = source / "local-storage/runs" / name
        row = read(run / "observation.json")
        report = run / "evaluated-report.md"
        assert row["formal_delivered"] and sha(report) == row["report_sha256"]
        if (target / "result.json").exists():
            assert read(target / "result.json")["report_sha256"] == sha(report)
            print(json.dumps({"case_id": case, "status": "existing_result", "new_model_calls": 0}))
            return 0
        assert not (target / "attempt.json").exists(), "Existing scoring attempt requires review; no automatic retry"
        driver = load(f"pinned_v18_race_driver_{case}", source / "race_driver.py")
        driver.ROOT = trial.REPO
        driver.OUT = EVAL
        driver.cell_directory = lambda _: run
        transport = trial.REPO / "artifacts/drbench-paired-20261114/run.py"
        api = load(f"v18_judge_preflight_{case}", transport)
        judge_base, judge_key = api.credentials()
        assert judge_base and judge_key
        del judge_key
        before = {str(path): sha(path) for path in (report, run / "observation.json", run / "query.json",
                                                    source / "manifest.json", source / "project/config.yaml")}
        save(target / "attempt.json", {"started_at": now(), "case_id": case, "report_sha256": sha(report),
             "version": "v18", "source": str(source), "judge_requested": api.MODEL,
             "transport_sha256": sha(transport), "race_driver_sha256": sha(source / "race_driver.py"),
             "automatic_restart": False, "report_regeneration": False,
             "protocol": "Existing frozen upstream cleaner, rubric, reference, prompts and weighted aggregation"})
        lifecycle = load(f"v18_score_supervision_{case}", source / "supervision.py").lifecycle
        with lifecycle(target):
            print(json.dumps({"case_id": case, "status": "scoring", "at": now()}, ensure_ascii=False), flush=True)
            try:
                scores = driver.race_score(cell)
                result = read(target / "result.json")
                assert result["report_sha256"] == sha(report)
                assert all(sha(Path(path)) == digest for path, digest in before.items())
                trial.manifest()
                records = [read(path) for path in (target / "calls").glob("*.json")
                           if not path.name.endswith((".pending.json", ".error.json"))]
                summary = {"case_id": case, "status": "completed", "version": "v18", "source": str(source),
                           "report_sha256": sha(report), "scores": scores,
                           "race_overall_percent": scores["overall_score"] * 100,
                           "race_tokens": sum(item.get("usage", {}).get("total_tokens", 0) for item in records),
                           "judge_calls": len(records), "judge_requested": api.MODEL,
                           "judge_returned": sorted({item.get("model_returned", "") for item in records}),
                           "generation_tokens": row["generation_tokens"], "generation_tokens_changed": False,
                           "formal_delivered": True, "original_sources_and_report_unchanged": True,
                           "official_leaderboard_comparable": False, "fact_status": "not_run", "finished_at": now()}
                save(target / "summary.json", summary)
                print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
            except Exception as error:
                save(target / "error.json", {"at": now(), "error_type": type(error).__name__, "automatic_retry": False})
                print(json.dumps({"case_id": case, "status": "requires_review", "error_type": type(error).__name__}), flush=True)
                return 3
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=int, choices=sorted(SOURCES), required=True)
    raise SystemExit(score(parser.parse_args().case))
