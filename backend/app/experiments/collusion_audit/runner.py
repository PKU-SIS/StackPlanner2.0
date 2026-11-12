"""Native-SP runner: one autonomous task per rule, with independent traces."""

import argparse
import asyncio
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HOME = ROOT / "collusion_audit"
sys.path.insert(0, str(ROOT / "vendor/bid_audit_tools"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def select_report_artifact(state, run_id, report_format="markdown"):
    """Read the exact native-FINISH artifact regardless of the selected role."""
    selected = state.get("sp_last_final_artifact_ref")
    if state.get("sp_current_stage") != "finished" or not selected:
        return None
    for ref in (state.get("sp_current_artifact_refs") or {}).values():
        if not isinstance(ref, dict) or ref.get("run_id") != run_id or not ref.get("is_current", True):
            continue
        if selected not in {ref.get("artifact_id"), ref.get("virtual_path"), ref.get("workspace_path")}:
            continue
        if Path(str(ref.get("virtual_path", ""))).suffix.lower() == (".html" if report_format == "html" else ".md"):
            return ref
    return None


def select_markdown_artifact(state, run_id):
    return select_report_artifact(state, run_id, "markdown")


def setup_env(directory, extra_env=None):
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    if extra_env:
        path = Path(extra_env).resolve()
        load_dotenv(path, override=True)
    os.environ["DEER_FLOW_PROJECT_ROOT"] = str(ROOT)
    os.environ["DEER_FLOW_CONFIG_PATH"] = str(Path(os.environ.get("COLLUSION_CONFIG") or ROOT / "config.yaml").resolve())
    os.environ["DEER_FLOW_HOME"] = str(Path(directory) / "runtime")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"


def central_retry_options(timeout_seconds=None, max_tokens=None):
    """Trusted CLI resources only; use the existing native recovery mechanism."""
    import math

    if timeout_seconds is not None and (type(timeout_seconds) not in {int, float} or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError("Central retry timeout must be finite and positive")
    if max_tokens is not None and (type(max_tokens) is not int or max_tokens <= 0):
        raise ValueError("Central retry token allowance must be a positive integer")
    return {**({"sp_central_retry_timeout_seconds": float(timeout_seconds)} if timeout_seconds is not None else {}), **({"sp_central_retry_max_tokens": max_tokens} if max_tokens is not None else {})}


def progress_watchdog_limit(request_timeout=None, central_timeout=None):
    """Preserve600s by default; explicit longer requests get120s headroom."""
    limits = [600.0]
    for value in (request_timeout, central_timeout):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("Explicit request timeout must be a finite positive number")
        limits.append(float(value) + 120.0)
    return max(limits)


def html_source_exemptions(rule_id, report_format):
    """Only our explicitly bounded candidate payload skips generic previews."""
    return ["audit_m01_candidates"] if rule_id == "M01" and report_format == "html" else []


async def run_rule_task(
    rule,
    directory,
    *,
    model="qwen3-8-27b",
    case_timeout=3600,
    knowledge_root=None,
    rule_level_review=False,
    quality_controls=False,
    reporter_max_tokens=None,
    reporter_request_timeout=None,
    evidence_role_capacity=False,
    evidence_max_tokens=None,
    evidence_request_timeout=None,
    researcher_no_thinking=False,
    resilient_delivery=False,
    central_efficiency=False,
    reporter_delivery=False,
    central_retry_timeout=None,
    central_retry_max_tokens=None,
    report_format="markdown",
    project=None,
):
    from langchain_core.messages import HumanMessage

    from app.experiments.tender_audit.observability import AuditTrace
    from app.experiments.tender_audit.runner import experiment_config, runtime_model_settings
    from deerflow.config.app_config import AppConfig
    from deerflow.config.paths import get_paths
    from deerflow.runtime.events.store.jsonl import JsonlRunEventStore
    from deerflow.runtime.journal import RunJournal
    from deerflow.sp.runtime import make_sp_agent

    from .profile import CollusionProfile

    started = time.monotonic()
    progress_limit = max(progress_watchdog_limit(reporter_request_timeout, central_retry_timeout), progress_watchdog_limit(evidence_request_timeout, None))
    retry_options = central_retry_options(central_retry_timeout, central_retry_max_tokens)
    if retry_options and not quality_controls:
        raise ValueError("Central resource overrides require explicit quality controls")
    if central_efficiency and (model != "aliyun-qwen3.8-27b" or not quality_controls or not rule_level_review or report_format != "html"):
        raise ValueError("Central efficiency requires explicit Alibaba HTML quality rule review")
    if central_efficiency:
        retry_options = central_retry_options(480, 8192)
    config = experiment_config(model, max_tokens=8192)
    config.models[0] = config.models[0].model_copy(update={"supports_vision": True})
    # No unbounded ordinary-tool output externalization: adapters already page
    # their complete persistent collections and record each returned page.
    exempt = ["audit_" + rule["合并规则ID"].lower(), "audit_read_result", "audit_collection_overview", "project_search", "project_read", "read_file"]
    exempt += html_source_exemptions(rule["合并规则ID"], report_format)
    if rule_level_review:
        exempt.append("audit_read_complete")
    if knowledge_root is not None:
        exempt += ["ragflow_retrieve", "knowledge_read", "audit_review_units", "audit_record_reviews", "audit_add_review_units", "audit_review_status"]
    config.tool_output = config.tool_output.model_copy(update={"exempt_tools": list(dict.fromkeys([*config.tool_output.exempt_tools, *exempt]))})
    if reporter_delivery:
        if model != "aliyun-qwen3.8-27b" or report_format != "html" or not quality_controls or not rule_level_review:
            raise ValueError("Reporter streaming requires trusted Alibaba HTML quality review")
        import httpx

        config.models.append(
            config.models[0].model_copy(
                update={
                    "name": "aliyun-qwen-reporter-stream",
                    "use": "app.experiments.collusion_audit.reporter_stream:StreamingReporterModel",
                    "max_tokens": reporter_max_tokens or 32768,
                    "timeout": httpx.Timeout(connect=10, read=330, write=60, pool=30),
                    "stream_chunk_timeout": 300,
                    "normal_output_timeout": 300,
                }
            )
        )
    config = AppConfig.model_validate(config.model_dump())
    thread_id, run_id = "collusion-" + uuid.uuid4().hex, uuid.uuid4().hex
    paths = get_paths()
    paths.ensure_thread_dirs(thread_id, user_id="default")
    outputs = paths.sandbox_outputs_dir(thread_id, user_id="default")
    from .settings import ragflow_settings

    rag_secrets = ragflow_settings() if knowledge_root else {}
    secrets = tuple(str(v) for v in [config.models[0].model_extra.get("api_key", ""), rag_secrets.get("RAGFLOW_API_KEY", "")] if v)
    trace = AuditTrace(directory, secrets=secrets)
    profile = CollusionProfile(
        rule,
        Path(project or HOME / "project_complete"),
        outputs,
        directory / "parse-cache",
        trace,
        knowledge_root=knowledge_root,
        rule_level_review=rule_level_review,
        request_run_id=run_id,
        quality_controls=quality_controls,
        reporter_max_tokens=reporter_max_tokens,
        reporter_request_timeout=reporter_request_timeout,
        evidence_role_capacity=evidence_role_capacity,
        evidence_max_tokens=evidence_max_tokens,
        evidence_request_timeout=evidence_request_timeout,
        researcher_no_thinking=researcher_no_thinking,
        resilient_delivery=resilient_delivery,
        central_efficiency=central_efficiency,
        reporter_delivery=reporter_delivery,
        report_format=report_format,
    )
    extensions = profile.extensions()
    if central_efficiency:
        from .central_decision import verify_central_settings

        verified_central_settings = verify_central_settings(config)
    else:
        verified_central_settings = None
    action_budget = extensions.max_actions or 20

    class SafeStore(JsonlRunEventStore):
        async def put_batch(self, events):
            return await super().put_batch([trace.safe(event) for event in events])

    journal = RunJournal(run_id, thread_id, SafeStore(directory / "native-events"))
    abort = threading.Event()
    context = {
        "thread_id": thread_id,
        "run_id": run_id,
        "user_id": "default",
        "thinking_enabled": True,
        "sp_subagent_thinking_enabled": True,
        "sp_central_timeout_seconds": 180.0,
        "sp_central_retry_timeout_seconds": 120.0,
        "debug_trace_enabled": True,
        "__run_journal": journal,
        "__run_abort_event": abort,
    }
    run_config = {
        "configurable": {
            "thread_id": thread_id,
            "user_id": "default",
            "model_name": config.models[0].name,
            "thinking_enabled": True,
            "sp_subagent_thinking_enabled": True,
            "sp_central_timeout_seconds": 180.0,
            "sp_central_retry_timeout_seconds": 120.0,
        },
        "context": context,
        "callbacks": [trace, journal],
        "tags": ["lead_agent"],
        "recursion_limit": max(320, action_budget * 16),
    }
    context.update(retry_options)
    run_config["configurable"].update(retry_options)
    write(directory / "input.json", {"rule": rule, "project_manifest": profile.manifest, "user_input": profile.user_input(), "thread_id": thread_id, "run_id": run_id})
    write(
        directory / "effective_config.json",
        trace.safe(
            {
                "app_config": config.model_dump(),
                "runtime_model_settings": runtime_model_settings(config, thinking=True),
                "case_timeout_seconds": case_timeout,
                "no_progress_timeout_seconds": progress_limit,
                "no_progress_timeout_basis": "max(600, explicit role/Central request timeout +120); defaults unchanged",
                "native_scheduling": True,
                "verified_central_model_settings": verified_central_settings,
                "central_owned_policy": {
                    "enabled": central_efficiency,
                    "reasoning_effort": "low" if central_efficiency else None,
                    "http_read_seconds": 510 if central_efficiency else None,
                    "stream_gap_seconds": 510 if central_efficiency else None,
                    "generic_timeout_and_retry": "bypassed" if central_efficiency else "native",
                    "sdk_retries": 0,
                    "attempts_per_decision": 2,
                    "initial_seconds": 180,
                    "post_delegate_and_retry_seconds": 480 if central_efficiency else None,
                },
                "central_retry_overrides": {
                    "enabled": bool(retry_options),
                    "initial_timeout_seconds": 180.0,
                    "post_delegate_and_retry_timeout_seconds": retry_options.get("sp_central_retry_timeout_seconds", 120.0),
                    "retry_max_tokens": retry_options.get("sp_central_retry_max_tokens", 2048),
                    "main_max_tokens": 8192,
                    "thinking_enabled": True,
                    "default_off": True,
                },
                "source_reference_compatibility": True,
                "quality_controls": quality_controls,
                "report_format": report_format,
                "reporter_capacity_overrides": {
                    "max_tokens": reporter_max_tokens,
                    "http_timeout_seconds": 330 if reporter_delivery else reporter_request_timeout,
                    "normal_output_idle_seconds": 300 if reporter_delivery else None,
                    "stream_chunk_timeout_seconds": 300 if reporter_delivery else None,
                    "model_request_deadline_seconds": reporter_request_timeout,
                    "thinking_enabled": not reporter_delivery,
                    "default_off": True,
                    "streamed_delivery": reporter_delivery,
                },
                "evidence_role_capacity": {
                    "enabled": evidence_role_capacity,
                    "roles": ["coder", "researcher"],
                    "max_tokens": (evidence_max_tokens if evidence_max_tokens is not None else reporter_max_tokens) if evidence_role_capacity else None,
                    "http_timeout_seconds": (evidence_request_timeout if evidence_request_timeout is not None else reporter_request_timeout) if evidence_role_capacity else None,
                    "model_request_deadline_seconds": (evidence_request_timeout if evidence_request_timeout is not None else reporter_request_timeout) if evidence_role_capacity else None,
                    "thinking_enabled": True,
                    "default_off": True,
                },
                "researcher_no_thinking": {"enabled": researcher_no_thinking, "thinking_enabled": False if researcher_no_thinking else True, "default_off": True},
                "resilient_delivery": {"enabled": resilient_delivery, "evidence_tool_limit": 64 if resilient_delivery else 20, "allow_limited_report": extensions.allow_limited_report, "read_volume_withdrawal": not resilient_delivery},
                "reference_validation": "explicit_catalog" if quality_controls else "legacy_literal",
                "report_projection_run_bound": True,
                "central_action_budget": action_budget,
                "precise_report_intent": extensions.precise_report_intent,
                "preserve_registered_tool_intent": getattr(extensions, "preserve_registered_tool_intent", False),
                "trusted_registered_tool_names": [tool.name for tool in (extensions.tools or ())] if getattr(extensions, "preserve_registered_tool_intent", False) else [],
                "graph_recursion_limit": run_config["recursion_limit"],
                "forced_next_agent": False,
                "memory_updates": False,
                "long_term_memory_injection": False,
                "knowledge_root": str(knowledge_root) if knowledge_root else None,
                "knowledge_fingerprint": profile.knowledge.index["fingerprint"] if profile.knowledge else None,
                "business_coverage_gate": profile.adapters.ledger is not None,
                "review_mode": "rule" if rule_level_review else "legacy_rows",
                "code_result_delivery": ("complete_grouped_records_or_explicit_indexed_large_M01" if rule["合并规则ID"] == "M01" else "complete_grouped_records") if rule_level_review else "summary_and_pages",
                "scoped_delegate_recovery": extensions.scoped_delegate_recovery,
                "budget_closing_enabled": profile.knowledge is not None,
                "allowed_tools": [t.name for t in profile.tools],
            }
        ),
    )
    state, error, iterator = {}, None, None
    try:
        graph = make_sp_agent(run_config, app_config=config, extensions=extensions)
        iterator = graph.astream({"messages": [HumanMessage(content=profile.user_input())]}, config=run_config, context=context, stream_mode=["values", "custom"]).__aiter__()
        async with asyncio.timeout(case_timeout):
            next_event = asyncio.create_task(anext(iterator))
            try:
                while True:
                    done, _ = await asyncio.wait({next_event}, timeout=1)
                    if not done:
                        if time.monotonic() - trace.last_progress > progress_limit:
                            raise TimeoutError(f"No observable progress for {progress_limit:g} seconds")
                        continue
                    try:
                        mode, payload = next_event.result()
                    except StopAsyncIteration:
                        break
                    if mode == "values":
                        state = payload
                    else:
                        trace.event("native.custom", payload=payload)
                    next_event = asyncio.create_task(anext(iterator))
            finally:
                if not next_event.done():
                    next_event.cancel()
                    await asyncio.gather(next_event, return_exceptions=True)
    except Exception as exc:
        error = trace.safe(type(exc).__name__ + ": " + str(exc))
        trace.event("collusion.failed", error=error)
    finally:
        abort.set()
        if iterator is not None:
            try:
                await iterator.aclose()
            except Exception:
                pass
        trace.close_pending(error or "Graph ended without terminal callback")
        await journal.flush()
    selected = select_report_artifact(state, run_id, report_format)
    report_filename = "report.html" if report_format == "html" else "report.md"
    report = ""
    if selected:
        try:
            source = profile.output_path(str(selected.get("virtual_path", "")))
            report = source.read_text()
            (directory / report_filename).write_text(report)
        except Exception as exc:
            error = error or trace.safe(type(exc).__name__ + ": " + str(exc))
    run_ids = [result["run_id"] for result in profile.adapters.runs if result.get("status") != "error"]
    valid = bool(not error and selected and report and rule["合并规则ID"] in report and any(run_id in report for run_id in run_ids))
    if report_format == "html":
        from .html_report import validate_html_report

        valid = bool(not error and selected and report and run_ids and not validate_html_report(report, expected_grade=str(rule.get("证据等级") or ""), rule_id=rule["合并规则ID"], metadata=selected.get("metadata") or {}))
    if profile.adapters.ledger is not None:
        valid = valid and profile.adapters.ledger.status()["complete"]
    tool_errors = [record for record in trace.tool_records if record.get("status") != "completed"]
    metrics = trace.metrics(time.monotonic() - started)
    if profile.knowledge is not None:
        from .metrics import knowledge_metrics

        metrics["tools"] = knowledge_metrics(trace.tool_records)
    metrics.update(
        {
            "rule_id": rule["合并规则ID"],
            "rule_name": rule["规则名称"],
            "status": "completed" if valid else "failed",
            "error": error,
            "native_finish": state.get("sp_current_stage") == "finished",
            "selected_markdown_artifact": bool(selected) and report_format == "markdown",
            "selected_html_artifact": bool(selected) and report_format == "html",
            "report_format": report_format,
            "native_final_artifact_ref": state.get("sp_last_final_artifact_ref"),
            "artifact": selected,
            "code_tool_run_ids": run_ids,
            "code_tool_statuses": [r.get("status") for r in profile.adapters.runs],
            "business_tool_calls": len(trace.tool_records),
            "business_tool_errors": len(tool_errors),
            "report_valid": valid,
            "report_sha256": hashlib.sha256(report.encode()).hexdigest() if report else None,
            "ground_truth_accuracy": "not evaluated; historical analyses withheld",
            "review_coverage": profile.adapters.ledger.status() if profile.adapters.ledger else None,
            "business_complete": profile.adapters.ledger.status()["complete"] if profile.adapters.ledger else None,
            "review_mode": "rule" if rule_level_review else "legacy_rows",
            "row_coverage_required": profile.adapters.ledger is not None,
            "complete_code_collections": [
                {name: spec["record_count"] for name, spec in r.get("complete_code_result", {}).get("collections", {name: {"record_count": spec["count"]} for name, spec in r.get("collections", {}).items()}).items()}
                for r in profile.adapters.runs
            ]
            if rule_level_review
            else None,
            "calculation_cache": [r.get("cache") for r in profile.adapters.runs],
            "actual_code_result_delivery": [r.get("code_result_delivery") for r in profile.adapters.runs],
        }
    )
    write(directory / "metrics.json", metrics)
    write(directory / "final_state.json", trace.safe(state))
    summary = {key: metrics[key] for key in ["rule_id", "rule_name", "status", "error", "duration_seconds", "llm_totals", "business_tool_calls", "business_tool_errors", "code_tool_statuses", "report_valid"]}
    write(directory / "attempt-summary.json", summary)
    return summary


def worker(args):
    directory = Path(args.worker_directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    setup_env(directory, args.env_file)
    from app.experiments.tender_audit.runner import request_deadline_environment
    from app.experiments.tender_audit.timing import timeout_multiplier_environment

    rules = json.loads(Path(args.rules or HOME / "rules.json").read_text())
    rule = next(r for r in rules if r["合并规则ID"] == args.rule[0])
    with timeout_multiplier_environment(2), request_deadline_environment(300, no_progress_timeout=progress_watchdog_limit(getattr(args, "reporter_request_timeout", None), getattr(args, "central_retry_timeout", None))):
        result = asyncio.run(
            run_rule_task(
                rule,
                directory,
                model=args.model,
                case_timeout=args.timeout,
                knowledge_root=args.knowledge_root,
                rule_level_review=args.rule_level_review,
                quality_controls=args.quality_controls,
                reporter_max_tokens=getattr(args, "reporter_max_tokens", None),
                reporter_request_timeout=getattr(args, "reporter_request_timeout", None),
                evidence_role_capacity=getattr(args, "evidence_role_capacity", False),
                evidence_max_tokens=getattr(args, "evidence_max_tokens", None),
                evidence_request_timeout=getattr(args, "evidence_request_timeout", None),
                researcher_no_thinking=getattr(args, "researcher_no_thinking", False),
                resilient_delivery=getattr(args, "resilient_delivery", False),
                central_efficiency=getattr(args, "central_efficiency", False),
                reporter_delivery=getattr(args, "reporter_delivery", False),
                report_format=getattr(args, "report_format", "markdown"),
                project=args.project,
                central_retry_timeout=getattr(args, "central_retry_timeout", None),
                central_retry_max_tokens=getattr(args, "central_retry_max_tokens", None),
            )
        )
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["status"] == "completed" else 1


def batch(args):
    rules = json.loads(Path(args.rules or HOME / "rules.json").read_text())
    ids = [r["合并规则ID"] for r in rules] if args.all_rules else args.rule
    if not ids:
        raise ValueError("Choose --rule or --all-rules")
    batch_directory = Path(args.output_root or HOME / "runs").resolve() / args.batch_name
    batch_directory.mkdir(parents=True, exist_ok=False)
    # Model availability is a batch gate, not a planner action or trajectory override.
    preflight_dir = batch_directory / "preflight"
    preflight_module = "app.experiments.collusion_audit.aliyun_probe" if args.model == "aliyun-qwen3.8-27b" else "app.experiments.collusion_audit.preflight"
    preflight_command = [sys.executable, "-m", preflight_module, "--directory", str(preflight_dir), "--model", args.model]
    if args.env_file:
        preflight_command += ["--env-file", str(Path(args.env_file).resolve())]
    if getattr(args, "reporter_delivery", False):
        preflight_command.append("--reporter-delivery")
    with (batch_directory / "preflight.log").open("w") as stream:
        checked = subprocess.run(preflight_command, cwd=ROOT / "backend", stdout=stream, stderr=subprocess.STDOUT, timeout=3600 if args.model == "aliyun-qwen3.8-27b" else 1000, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if checked.returncode:
        write(batch_directory / "status.json", {"status": "blocked_model_preflight", "planned_rules": ids, "submitted_rule_tasks": 0, "preflight": str(preflight_dir / "preflight.json")})
        return 2
    results = []
    if args.knowledge_root:
        knowledge_root = Path(args.knowledge_root).resolve()
        write(batch_directory / "knowledge_freeze.json", {str(p.relative_to(knowledge_root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in knowledge_root.rglob("*") if p.is_file()})
    source_files = list((ROOT / "backend/app/experiments/collusion_audit").glob("*.py")) + list((ROOT / "vendor/bid_audit_tools/bid_audit").glob("*.py"))
    source_files += list((ROOT / "backend/packages/harness/deerflow").rglob("*.py"))
    source_files += [p for p in (ROOT / "backend/app/experiments/collusion_audit/templates").rglob("*") if p.is_file()]
    source_files += [p for p in (ROOT / "skills/public/collusion-reporting-html").rglob("*") if p.is_file()]
    source_files += [ROOT / "skills/public/stackplanner-reporting/SKILL.md"]
    source_files += [Path(os.environ.get("COLLUSION_CONFIG") or ROOT / "config.yaml").resolve(), Path(args.rules or HOME / "rules.json").resolve(), Path(args.project or HOME / "project_complete").resolve() / "manifest.json"]
    write(batch_directory / "source_freeze.json", {str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files})
    for rule_id in ids:
        directory = batch_directory / rule_id / "attempt_001"
        directory.parent.mkdir()
        command = [sys.executable, "-m", "app.experiments.collusion_audit.runner", "--rule", rule_id, "--worker-directory", str(directory), "--model", args.model, "--timeout", str(args.timeout)]
        for option in ("project", "rules", "config", "ragflow_env_file"):
            value = getattr(args, option, None)
            if value:
                command += ["--" + option.replace("_", "-"), str(Path(value).resolve())]
        if args.env_file:
            command += ["--env-file", str(Path(args.env_file).resolve())]
        if args.knowledge_root:
            command += ["--knowledge-root", str(Path(args.knowledge_root).resolve())]
        if args.rule_level_review:
            command += ["--rule-level-review"]
        if args.quality_controls:
            command += ["--quality-controls"]
        if getattr(args, "report_format", "markdown") == "html":
            command += ["--report-format", "html"]
        if getattr(args, "central_retry_timeout", None) is not None:
            command += ["--central-retry-timeout", str(args.central_retry_timeout)]
        if getattr(args, "central_retry_max_tokens", None) is not None:
            command += ["--central-retry-max-tokens", str(args.central_retry_max_tokens)]
        if getattr(args, "evidence_role_capacity", False):
            command.append("--evidence-role-capacity")
        if getattr(args, "reporter_delivery", False):
            command.append("--reporter-delivery")
        if getattr(args, "central_efficiency", False):
            command.append("--central-efficiency")
        if getattr(args, "resilient_delivery", False):
            command.append("--resilient-delivery")
        if getattr(args, "researcher_no_thinking", False):
            command.append("--researcher-no-thinking")
        for option in ["evidence_max_tokens", "evidence_request_timeout"]:
            if getattr(args, option, None) is not None:
                command += ["--" + option.replace("_", "-"), str(getattr(args, option))]
        if getattr(args, "reporter_max_tokens", None) is not None:
            command += ["--reporter-max-tokens", str(args.reporter_max_tokens)]
        if getattr(args, "reporter_request_timeout", None) is not None:
            command += ["--reporter-request-timeout", str(args.reporter_request_timeout)]
        write(batch_directory / "status.json", {"status": "running", "current_rule": rule_id, "completed_rules": len(results), "planned_rules": ids})
        with (directory.parent / "worker.log").open("w") as stream:
            try:
                process = subprocess.run(command, cwd=ROOT / "backend", stdout=stream, stderr=subprocess.STDOUT, timeout=args.timeout + 120, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "DEER_FLOW_PROJECT_ROOT": str(ROOT)})
                returncode = process.returncode
            except subprocess.TimeoutExpired:
                returncode = -1
        summary_path = directory / "attempt-summary.json"
        if summary_path.exists():
            result = json.loads(summary_path.read_text())
        else:
            result = {"rule_id": rule_id, "status": "failed", "error": "worker exited before summary", "returncode": returncode}
        results.append(result)
        if result["status"] == "completed":
            report_dir = batch_directory / "reports"
            report_dir.mkdir(exist_ok=True)
            suffix = ".html" if getattr(args, "report_format", "markdown") == "html" else ".md"
            shutil.copy2(directory / ("report" + suffix), report_dir / (rule_id + suffix))
        write(batch_directory / "summary.json", {"planned_rules": ids, "results": results})
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if args.stop_on_failure and result["status"] != "completed":
            break
    write(
        batch_directory / "status.json",
        {"status": "finished", "planned_rules": ids, "completed_rules": [r["rule_id"] for r in results if r["status"] == "completed"], "failed_rules": [r["rule_id"] for r in results if r["status"] != "completed"]},
    )
    return 0 if len(results) == len(ids) and all(r["status"] == "completed" for r in results) else 1


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--rule", action="append")
    parser.add_argument("--all-rules", action="store_true")
    parser.add_argument("--report-format", choices=["markdown", "html"], default="markdown", help="Trusted opt-in human HTML delivery; native scheduling unchanged")
    parser.add_argument("--batch-name", default="qwen-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument("--model", default="qwen3-8-27b")
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument("--worker-directory")
    parser.add_argument("--knowledge-root", type=Path)
    parser.add_argument("--rule-level-review", action="store_true", help="Deliver full code facts, allow native evidence-driven review, and require a rule report without row judgements")
    parser.add_argument("--quality-controls", action="store_true", help="Use trusted fact navigation, tool identity and explicit-reference audit contracts")
    parser.add_argument("--central-retry-timeout", type=float, default=None, help="Opt-in native Central post-delegation and retry timeout seconds")
    parser.add_argument("--central-retry-max-tokens", type=int, default=None, help="Opt-in native Central retry token ceiling; roles and recovery policy unchanged")
    parser.add_argument("--evidence-role-capacity", action="store_true", help="Explicit opt-in: apply Reporter capacity options also to Coder/Researcher; no role selection changes")
    parser.add_argument("--reporter-max-tokens", type=int, default=None, help="Trusted opt-in Reporter completion cap; other roles unchanged")
    parser.add_argument("--reporter-request-timeout", type=float, default=None, help="Trusted opt-in Reporter HTTP and model request deadline seconds")
    parser.add_argument("--researcher-no-thinking", action="store_true", help="Trusted opt-in: disable thinking only for Researcher")
    parser.add_argument("--evidence-max-tokens", type=int, default=None, help="Independent Coder/Researcher completion cap")
    parser.add_argument("--evidence-request-timeout", type=float, default=None, help="Independent Coder/Researcher request timeout")
    parser.add_argument("--reporter-delivery", action="store_true", help="Opt-in streamed Reporter output, no thinking, lossless references and bounded delivery")
    parser.add_argument("--central-efficiency", action="store_true", help="Trusted opt-in Central-owned recovery, low cloud effort and concise role inputs")
    parser.add_argument("--resilient-delivery", action="store_true", help="Trusted opt-in complete reads and Central limited-report choice")
    parser.add_argument("--project", type=Path, help="Prepared originals directory containing manifest.json")
    parser.add_argument("--rules", type=Path, help="Exported generic rule catalog")
    parser.add_argument("--config", type=Path, help="Model/runtime YAML")
    parser.add_argument("--output-root", type=Path, help="Directory for new batch outputs")
    parser.add_argument("--ragflow-env-file", type=Path, help="Server-side RAGFlow credentials")
    args = parser.parse_args()
    if args.config:
        os.environ["COLLUSION_CONFIG"] = str(args.config.resolve())
        os.environ["DEER_FLOW_CONFIG_PATH"] = str(args.config.resolve())
    if args.ragflow_env_file:
        os.environ["COLLUSION_RAGFLOW_ENV_FILE"] = str(args.ragflow_env_file.resolve())
    return worker(args) if args.worker_directory else batch(args)


if __name__ == "__main__":
    raise SystemExit(main())
