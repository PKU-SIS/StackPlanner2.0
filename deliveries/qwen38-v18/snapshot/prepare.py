"""Freeze one authorized unseen-case trial; all checks are local or read-only."""

import ast
import json
import os
from pathlib import Path

import yaml

import run as trial

ROOT = Path(__file__).resolve().parent
FIX = ROOT.parent / "qwen38-outline-delivery-repair-v17-20261119"
BASE = ROOT.parent / "qwen38-full-repaired-budget-v16-20261119"


def main():
    trial.support.configure_storage()
    from dotenv import load_dotenv

    load_dotenv(trial.REPO / ".env", override=True)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    os.environ.update(DEER_FLOW_PROJECT_ROOT=str(ROOT / "project"), DEER_FLOW_HOME=str(ROOT / "local-storage/preparation-runtime"))
    directory = ROOT / "local-storage/runs" / trial.NAME
    assert not (directory / "attempt.json").exists()
    assert not (ROOT / "manifest.json").exists(), "Preserve frozen preparation"
    repairs = trial.read(FIX / "local-storage/manifest.json")
    replay = trial.read(FIX / "local-storage/replay-result.json")
    assert repairs["regression_tests_passed"] == 494 and repairs["ruff_passed"] and replay["passed"]
    for rel, expected in repairs["code_hashes"].items():
        assert trial.sha(FIX / "code" / rel) == trial.sha(ROOT / "code" / rel) == expected, rel
    for name in ("runner_support.py", "execution_runner.py", "qwen_adapter.py", "supervision.py", "launch.py", "race_driver.py"):
        assert trial.sha(ROOT / name) == trial.sha(BASE / name), name
    before = yaml.safe_load((BASE / "project/config.yaml").read_text())
    after = yaml.safe_load((ROOT / "project/config.yaml").read_text())
    assert json.dumps(after, sort_keys=True).replace(str(ROOT), str(BASE)) == json.dumps(before, sort_keys=True)
    query = trial.read(directory / "query.json")
    prior_query = trial.read(BASE / "local-storage/runs/band10000-case17-outline-selected/query.json")
    assert query["report_length"] == prior_query["report_length"]
    prior_manifest = trial.read(BASE / "manifest.json")
    assert trial.sha(trial.REPO / "config.yaml") == prior_manifest["original_config_sha256"]
    trial.native_for(10000)
    from deerflow.config.app_config import AppConfig, set_app_config
    from deerflow.models import create_chat_model
    from deerflow.sp.report.length import normalize_contract
    from deerflow.subagents.config import resolve_subagent_model_name
    from deerflow.subagents.registry import get_subagent_config
    from langchain_core.messages import HumanMessage

    normalize_contract(query["report_length"])
    app = AppConfig.from_file(str(ROOT / "project/config.yaml"))
    set_app_config(app)
    settings = []
    for role, thinking in (("sp-outline", True), ("sp-researcher", True), ("sp-reporter", False)):
        cfg = get_subagent_config(role, app_config=app)
        alias = resolve_subagent_model_name(cfg, app.models[0].name, app_config=app)
        model = create_chat_model(name=alias, thinking_enabled=thinking, app_config=app, attach_tracing=False, max_tokens=32768)
        payload = model._get_request_payload([HumanMessage(content="Local configuration check only")])
        assert payload["model"] == "qwen3.8-27b" and payload["max_completion_tokens"] == 32768
        assert payload["extra_body"]["chat_template_kwargs"]["enable_thinking"] is thinking
        assert payload.get("reasoning_effort") == ("low" if thinking else None)
        assert model.max_retries == 0
        settings.append(dict(role=role, alias=alias, thinking_enabled=thinking, reasoning_effort=payload.get("reasoning_effort"), output_cap=32768))
    helpers = ("run.py", "runner_support.py", "execution_runner.py", "qwen_adapter.py", "supervision.py", "speed_trial.py", "launch.py", "watch.py", "race_driver.py", "prepare.py")
    for name in helpers:
        ast.parse((ROOT / name).read_text())
    manifest = dict(
        label="Qwen v17 delivery repair, unseen case10 complete trial v18",
        baseline=str(FIX), previous_full_trial=str(BASE), created_at=trial.now(),
        runtime_hashes=repairs["code_hashes"], helper_hashes={name: trial.sha(ROOT / name) for name in helpers},
        cells=[dict(name=trial.NAME, case_id=query["id"], target_words=10000, query_sha256=trial.sha(directory / "query.json"))],
        original_config_sha256=prior_manifest["original_config_sha256"], qwen_config_sha256=trial.sha(ROOT / "project/config.yaml"),
        final_contract=query["report_length"], settings=settings, max_concurrent_subagents=6,
        race_requested=False, automatic_restart=False, report_timeout_seconds=7200, complete_report_requirement_seconds=1800,
        source_changes="Exact frozen v17 code; missing structured Outline delivery correction only relative to v16.",
        input_changes="New pinned official case10; all length and model parameters match v16.",
        method_stages_unchanged=True, report_postprocessing=False, writer_model_unchanged=True,
    )
    trial.save(ROOT / "manifest.json", manifest)
    trial.manifest()
    trial.support.validate_race_inputs(manifest["cells"][0])
    preflight = trial.support.preflight()
    result = dict(passed=True, runtime_files_verified=len(manifest["runtime_hashes"]), settings=settings,
                  repair_tests_passed=494, public_failure_replay_passed=True, preflight_ready=preflight["ready"],
                  case_id=query["id"], body_budget_range=query["report_length"]["outline_body_budget_range"],
                  full_acceptance=[9000, 10000, 11000], generation_started=False, race_started=False, shared_service_changes=False)
    trial.save(ROOT / "local-storage/preparation.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
