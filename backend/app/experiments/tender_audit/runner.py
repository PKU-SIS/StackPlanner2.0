"""Native SP2 graph runs with local artifacts, isolated attempts and telemetry."""

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
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .cases import DELIVERY_VALIDATION_POLICY, AuditCase, build_cases, validate_result
from .context_projection import CONTEXT_PROJECTION_POLICY
from .observability import AuditTrace
from .profile import AuditProfile
from .ragflow import VISIBLE_FIELDS_POLICY, RagflowClient, RagflowSettings
from .request_projection import REQUEST_PROJECTION_POLICY
from .timing import scale_config_timeouts, scaled_timeout, timeout_multiplier, timeout_multiplier_environment

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUT = PROJECT_ROOT / ".deer-flow/experiments/tender-audit"


@contextmanager
def request_deadline_environment(seconds: float, *, no_progress_timeout: float):
    """Enable the existing native async request deadline only in this CLI."""
    seconds = float(seconds)
    if not math.isfinite(seconds) or seconds < 0 or (seconds > 0 and seconds >= no_progress_timeout):
        raise ValueError("Request deadline must be finite, nonnegative, and shorter than the no-progress watchdog (0 disables it)")
    key = "DEER_FLOW_LLM_REQUEST_TIMEOUT_SECONDS"
    previous = os.environ.get(key)
    os.environ[key] = str(seconds)
    try:
        yield seconds
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def runtime_model_settings(config, *, thinking: bool) -> dict:
    """Verify real factory/client values without invoking the provider."""
    from deerflow.models import create_chat_model

    selected = config.models[0]
    if config.get_model_config(selected.name) is not selected:
        raise ValueError("Experiment model name index differs from its saved snapshot")
    model = create_chat_model(
        name=selected.name,
        thinking_enabled=thinking,
        app_config=config,
        attach_tracing=False,
    )
    settings = {
        "model": model.model_name,
        "provider_class": type(model).__name__,
        "lookup_matches_snapshot": True,
        "sdk_max_retries": model.max_retries,
        "client_max_retries": model.root_async_client.max_retries,
        "http_timeout_seconds": model.request_timeout,
        "max_tokens": model.max_tokens,
        "thinking_enabled": thinking,
        "native_request_deadline_seconds": float(os.getenv("DEER_FLOW_LLM_REQUEST_TIMEOUT_SECONDS", "0") or 0),
        "connection_header": model.root_async_client.default_headers.get("Connection"),
        "stream_chunk_timeout_seconds": model.stream_chunk_timeout,
        "timeout_multiplier": timeout_multiplier(),
    }
    if settings["sdk_max_retries"] != 0 or settings["client_max_retries"] != 0 or settings["http_timeout_seconds"] != scaled_timeout(120) or settings["max_tokens"] != selected.model_extra.get("max_tokens"):
        raise ValueError("Constructed model/client settings differ from the experiment overrides")
    if settings["connection_header"] != "close":
        raise ValueError("Experiment client must close connections after every response")
    return settings


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def project_output(path: Path) -> Path:
    path = path.resolve()
    if path == PROJECT_ROOT or not path.is_relative_to(PROJECT_ROOT) or any(part in {".git", ".agents", ".codex", ".aws"} for part in path.relative_to(PROJECT_ROOT).parts):
        raise ValueError("Experiment outputs must be a dedicated directory inside SP2")
    return path


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def select_final_report(state: dict, run_id: str) -> dict | None:
    """Resolve the exact Artifact accepted by native FINISH, never AI prose."""
    selected = state.get("sp_last_final_artifact_ref")
    if state.get("sp_current_stage") != "finished" or not selected:
        return None
    for key, ref in (state.get("sp_current_artifact_refs") or {}).items():
        if key not in {"report", "report_revision", "final_report"} or not isinstance(ref, dict):
            continue
        if (
            ref.get("run_id") == run_id
            and (ref.get("metadata") or {}).get("target_agent") == "reporter"
            and ref.get("is_current", True)
            and selected
            in {
                ref.get("artifact_id"),
                ref.get("virtual_path"),
                ref.get("workspace_path"),
            }
        ):
            return ref
    return None


def aggregate_metrics(batch: Path) -> dict:
    records = [json.loads(path.read_text()) for path in batch.glob("case_*/attempt_*/metrics.json")]

    def sum_records(items):
        return {
            "attempts": len(items),
            "duration_seconds": sum(item.get("duration_seconds", 0) for item in items),
            "token_usage_complete": bool(items) and all(item.get("token_usage_complete", False) for item in items),
            **{key: sum(item.get("llm_totals", {}).get(key, 0) for item in items) for key in ("calls", "input_tokens", "output_tokens", "total_tokens")},
            "ragflow_calls": sum(item.get("tools", {}).get("ragflow_calls", 0) for item in items),
        }

    return {
        "all_attempts": sum_records(records),
        "successful_attempts": sum_records([record for record in records if record.get("status") == "completed"]),
        "preflight_included": False,
    }


def recover_failed_attempt(directory: Path, *, case_id: int, returncode: int, duration_seconds: float) -> dict:
    """Retain known costs even when a worker cannot write its final summary."""
    trace = AuditTrace(directory)

    def records(filename):
        path = directory / filename
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # A killed worker can leave one partial trailing record.
                    continue

    trace.model_records = list(records("model_calls.jsonl"))
    trace.finished = {record["call_id"] for record in trace.model_records}
    trace.tool_records = list(records("tool_calls.jsonl"))
    for request in records("model_requests.jsonl"):
        if request["call_id"] not in trace.finished:
            elapsed = max(
                0,
                (datetime.now(UTC) - datetime.fromisoformat(request["started_at"])).total_seconds(),
            )
            trace.pending[request["call_id"]] = {
                **request,
                "started_perf": time.monotonic() - elapsed,
            }
    stop_reason = "worker_timeout" if returncode == 124 else "worker_failed"
    trace.close_pending(stop_reason)
    metrics = {
        **trace.metrics(duration_seconds),
        "case_id": case_id,
        "status": "failed",
        "stop_reason": stop_reason,
        "returncode": returncode,
    }
    write_json(directory / "metrics.json", metrics)
    return {
        "case_id": case_id,
        "status": "failed",
        "stop_reason": stop_reason,
        "returncode": returncode,
        "attempt_dir": str(directory),
        "llm_totals": metrics["llm_totals"],
        "duration_seconds": duration_seconds,
    }


def rag_settings() -> RagflowSettings:
    return RagflowSettings(
        os.environ["RAGFLOW_API_URL"],
        os.environ["RAGFLOW_API_KEY"],
        page_size=int(os.getenv("RAGFLOW_PAGE_SIZE", "15")),
        timeout=scaled_timeout(float(os.getenv("RAGFLOW_TIMEOUT", "120"))),
        similarity_threshold=float(os.getenv("RAGFLOW_SIMILARITY_THRESHOLD", "0")),
        vector_similarity_weight=float(os.getenv("RAGFLOW_VECTOR_SIMILARITY_WEIGHT", "0.7")),
        top_k=int(os.getenv("RAGFLOW_TOP_K", "1024")),
        keyword=os.getenv("RAGFLOW_KEYWORD", "false").lower() == "true",
        highlight=os.getenv("RAGFLOW_HIGHLIGHT", "false").lower() == "true",
    )


def experiment_config(model_name: str, *, max_tokens: int = 8192):
    from deerflow.config.app_config import get_app_config
    from deerflow.config.model_config import ModelConfig
    from deerflow.config.subagents_config import SubagentOverrideConfig
    from deerflow.sp.runtime import SP_SUBAGENT_REGISTRY_NAMES
    from deerflow.subagents.registry import get_subagent_config

    config = get_app_config().model_copy(deep=True)
    config = type(config).model_validate(scale_config_timeouts(config.model_dump()))
    if model_name == "aliyun-qwen3.8-27b":
        from app.experiments.collusion_audit.aliyun import model_config

        model = model_config(max_tokens=max_tokens)
    elif model_name == "deepseek-flash":
        model = ModelConfig(
            name="deepseek-flash",
            display_name="DeepSeek Flash",
            use="langchain_openai:ChatOpenAI",
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            supports_thinking=True,
            when_thinking_enabled={"extra_body": {"thinking": {"type": "enabled"}}},
            when_thinking_disabled={"extra_body": {"thinking": {"type": "disabled"}}},
            timeout=120.0,
            max_retries=1,
            max_tokens=max_tokens,
        )
    else:
        model = config.get_model_config(model_name)
        if model is None:
            raise ValueError(f"Model {model_name!r} is not configured")
        model = model.model_copy(deep=True, update={"max_tokens": max_tokens})
    config.models = [model]
    # Let the existing native error middleware own retries. Hidden SDK retries
    # can otherwise outlive the experiment's observable-progress watchdog.
    headers = {key: value for key, value in (model.model_extra.get("default_headers") or {}).items() if key.lower() != "connection"}
    stream_timeout = model.stream_chunk_timeout if model.stream_chunk_timeout is not None else scaled_timeout(240)
    if model.use == "deerflow.models.vllm_provider:VllmChatModel":
        # The native factory removes this config field for this adapter. Its
        # actual SDK default is controlled by the scoped LangChain env above.
        stream_timeout = None
    config.models[0] = model.model_copy(deep=True, update={"max_retries": 0, "timeout": scaled_timeout(120.0), "stream_chunk_timeout": stream_timeout, "default_headers": {**headers, "Connection": "close"}})
    config.memory = config.memory.model_copy(update={"enabled": False, "injection_enabled": False})
    config.title = config.title.model_copy(update={"enabled": False})
    config.tool_output = config.tool_output.model_copy(update={"exempt_tools": list(dict.fromkeys([*config.tool_output.exempt_tools, "local_search_tool"]))})
    config.tools = []  # Execution capabilities come exclusively from the scoped extension.
    for registry_name in SP_SUBAGENT_REGISTRY_NAMES.values():
        previous = config.subagents.agents.get(registry_name)
        override = previous.model_dump() if previous is not None else {}
        config.subagents.agents[registry_name] = SubagentOverrideConfig(**{**override, "model": model.name})
        assert get_subagent_config(registry_name, app_config=config).model == model.name
    # AppConfig caches name lookups at validation time. A deep copy followed by
    # list edits leaves those indexes pointing at the pre-override models/tools.
    # Revalidate only this experiment copy so the native factory sees exactly
    # the settings recorded in its snapshot, without changing global config.
    return type(config).model_validate(config.model_dump())


async def model_preflight(config, thinking: bool, trace: AuditTrace) -> dict:
    from langchain_core.messages import HumanMessage
    from langchain_core.tools import tool

    from deerflow.models import create_chat_model

    @tool
    def audit_probe(value: str) -> str:
        """Return a harmless connectivity marker."""
        return value

    model = create_chat_model(
        name=config.models[0].name,
        thinking_enabled=thinking,
        app_config=config,
        attach_tracing=False,
        max_tokens=2048,
    )
    reply = await model.ainvoke(
        [HumanMessage(content="只回复 OK")],
        config={"callbacks": [trace], "tags": ["middleware:preflight"]},
    )
    if "OK" not in str(reply.content):
        raise RuntimeError("Model preflight did not return the connectivity marker")
    response = await model.bind_tools([audit_probe]).ainvoke(
        [HumanMessage(content="Call audit_probe once with value OK. Do not answer in text.")],
        config={"callbacks": [trace], "tags": ["middleware:preflight"]},
    )
    if not response.tool_calls or response.tool_calls[0]["name"] != "audit_probe" or response.tool_calls[0]["args"] != {"value": "OK"}:
        raise RuntimeError("Model preflight did not produce the requested tool call")
    return {
        "model": config.models[0].model,
        "text": "passed",
        "tools": "passed",
        "thinking_enabled": thinking,
    }


async def preflight(
    cases: list[AuditCase],
    config,
    settings: RagflowSettings,
    directory: Path,
    thinking: bool,
) -> dict:
    started = time.monotonic()
    secrets = (settings.api_key, str(config.models[0].model_extra.get("api_key", "")))
    trace = AuditTrace(directory, secrets=secrets)
    result = {"status": "running", "datasets": []}
    try:
        result["model"] = await asyncio.wait_for(model_preflight(config, thinking, trace), timeout=scaled_timeout(150))
        seen = set()
        for case in cases:
            if case.dataset_id in seen:
                continue
            seen.add(case.dataset_id)
            response = await RagflowClient(settings, case.dataset_id, trace).search(case.rule_names[0])
            if not response["chunks"]:
                raise RuntimeError(f"RAG preflight returned no chunks for dataset {case.dataset_id}")
            result["datasets"].append({"dataset_id": case.dataset_id, "chunk_count": len(response["chunks"])})
        result["status"] = "completed"
    except Exception as exc:
        result.update(status="failed", error=trace.safe(str(exc)))
        raise
    finally:
        trace.close_pending("Preflight ended without a terminal model callback")
        result["metrics"] = trace.metrics(time.monotonic() - started)
        write_json(directory / "preflight.json", result)
    return result


async def run_attempt(
    case: AuditCase,
    config,
    settings: RagflowSettings,
    directory: Path,
    *,
    thinking: bool = True,
    timeout: float = 900,
    no_progress_timeout: float = 180,
) -> dict:
    from langchain_core.messages import HumanMessage

    from deerflow.config.paths import get_paths
    from deerflow.runtime.events.store.jsonl import JsonlRunEventStore
    from deerflow.runtime.journal import RunJournal
    from deerflow.sp.runtime import make_sp_agent

    started = time.monotonic()
    thread_id = "audit-" + uuid.uuid4().hex
    run_id = uuid.uuid4().hex
    paths = get_paths()
    paths.ensure_thread_dirs(thread_id, user_id="default")
    outputs = paths.sandbox_outputs_dir(thread_id, user_id="default")
    trace = AuditTrace(
        directory,
        secrets=(
            settings.api_key,
            str(config.models[0].model_extra.get("api_key", "")),
        ),
    )
    actual_model = runtime_model_settings(config, thinking=thinking)
    write_json(directory / "runtime-model-settings.json", actual_model)
    trace.event("audit.runtime_model_settings", **actual_model)
    rag = RagflowClient(settings, case.dataset_id, trace)
    profile = AuditProfile(case, rag, trace, outputs)

    class SanitizedEventStore(JsonlRunEventStore):
        async def put_batch(self, events):
            return await super().put_batch([trace.safe(event) for event in events])

    journal = RunJournal(run_id, thread_id, SanitizedEventStore(directory / "native-events"))
    abort = threading.Event()
    context = {
        "thread_id": thread_id,
        "run_id": run_id,
        "user_id": "default",
        "thinking_enabled": thinking,
        "sp_subagent_thinking_enabled": thinking,
        "sp_central_timeout_seconds": scaled_timeout(90),
        "sp_central_retry_timeout_seconds": scaled_timeout(60),
        "debug_trace_enabled": True,
        "__run_journal": journal,
        "__run_abort_event": abort,
    }
    run_config = {
        "configurable": {
            "thread_id": thread_id,
            "user_id": "default",
            "model_name": config.models[0].name,
            "thinking_enabled": thinking,
            "sp_subagent_thinking_enabled": thinking,
            "sp_central_timeout_seconds": scaled_timeout(90),
            "sp_central_retry_timeout_seconds": scaled_timeout(60),
        },
        "context": context,
        "callbacks": [trace, journal],
        "tags": ["lead_agent"],
        "recursion_limit": 320,
    }
    write_json(
        directory / "input.json",
        {
            **case.public_dict(),
            "user_input": case.user_input(),
            "task_context": case.task_context(),
            "report_instructions": case.report_instructions(),
            "thread_id": thread_id,
            "run_id": run_id,
        },
    )
    write_json(
        directory / "effective_config.json",
        trace.safe(
            {
                "app_config": config.model_dump(),
                "runtime_model_settings": actual_model,
                "rag_visible_fields_policy": VISIBLE_FIELDS_POLICY,
                "request_evidence_projection_policy": REQUEST_PROJECTION_POLICY,
                "request_context_projection_policy": CONTEXT_PROJECTION_POLICY,
                "timeout_multiplier": timeout_multiplier(),
                "central_timeout_seconds": scaled_timeout(90),
                "central_retry_timeout_seconds": scaled_timeout(60),
                "model_thinking_enabled": thinking,
                "sp_subagent_thinking_enabled": thinking,
                "case_timeout_seconds": timeout,
                "no_progress_timeout_seconds": no_progress_timeout,
                "reporter_evidence_max_chars": 96000,
                "native_scheduling": True,
                "extra_json_repair": False,
                "validation_policy": DELIVERY_VALIDATION_POLICY,
                "evidence_evaluation": "offline_only",
            }
        ),
    )
    last_state, error, stop_reason = {}, None, None
    iterator = None
    try:
        graph = make_sp_agent(run_config, app_config=config, extensions=profile.extensions())
        iterator = graph.astream(
            {"messages": [HumanMessage(content=case.user_input())]},
            config=run_config,
            context=context,
            stream_mode=["values", "custom"],
        ).__aiter__()
        async with asyncio.timeout(timeout):
            # A bounded polling interval checks progress without cancelling a
            # healthy long model stream; callbacks also update last_progress.
            next_event = asyncio.create_task(anext(iterator))
            try:
                while True:
                    done, _ = await asyncio.wait({next_event}, timeout=1)
                    if not done:
                        if time.monotonic() - trace.last_progress > no_progress_timeout:
                            stop_reason = "no_progress_timeout"
                            raise TimeoutError("No observable model/tool/graph progress")
                        continue
                    try:
                        mode, payload = next_event.result()
                    except StopAsyncIteration:
                        break
                    if mode == "values":
                        last_state = payload
                    else:
                        trace.event("native.custom", payload=payload)
                    next_event = asyncio.create_task(anext(iterator))
            finally:
                if not next_event.done():
                    next_event.cancel()
                    await asyncio.gather(next_event, return_exceptions=True)
    except Exception as exc:
        error = trace.safe(f"{type(exc).__name__}: {exc}")
        stop_reason = stop_reason or ("timeout" if isinstance(exc, TimeoutError) else "execution_error")
        trace.event("audit.failed", error=error, stop_reason=stop_reason)
    finally:
        abort.set()
        if iterator is not None:
            try:
                await iterator.aclose()
            except Exception:
                pass
        trace.close_pending(error or "Run ended without a terminal model callback")
        await journal.flush()

    raw, selected = "", select_final_report(last_state, run_id)
    if selected is not None:
        virtual_path = str(selected.get("virtual_path") or "")
        try:
            source = profile._path(virtual_path)
            raw = source.read_text(encoding="utf-8")
            destination = directory / "artifacts" / source.name
            destination.parent.mkdir(exist_ok=True)
            shutil.copyfile(source, destination)
        except (OSError, ValueError) as exc:
            error = error or trace.safe(str(exc))
    validation = validate_result(raw, case)
    if selected is not None:
        validation["artifact_completion_status"] = (selected.get("metadata") or {}).get("completion_status")
    native_finished = last_state.get("sp_current_stage") == "finished" and selected is not None
    roles = {record["agent"] for record in trace.model_records}
    technical_complete = not error and native_finished and selected is not None and bool(rag.chunks) and {"researcher", "reporter"}.issubset(roles) and validation["valid"]
    status = "completed" if technical_complete else "failed" if error else "invalid_result"
    for record in trace.model_records:
        if record["agent"] == "reporter":
            trace.append("reporter_raw.jsonl", record)
    (directory / "reporter_raw.txt").write_text(
        "\n\n".join(str(message.get("content", "")) for record in trace.model_records if record["agent"] == "reporter" for message in record.get("outputs", []) if isinstance(message, dict)),
        encoding="utf-8",
    )
    (directory / "artifact_raw.json").write_text(raw, encoding="utf-8")
    write_json(directory / "validation.json", validation)
    if validation.get("valid"):
        write_json(directory / "result.json", validation["parsed_result"])
    metrics = trace.metrics(time.monotonic() - started)
    metrics.update(
        case_id=case.case_id,
        status=status,
        error=error,
        stop_reason=stop_reason or ("finished" if native_finished else "native_stop_without_finish"),
        native_finish_observed=native_finished,
        artifact_ref=selected,
        json_valid=validation["json_valid"],
        schema_valid=validation["schema_valid"],
        validation_policy=validation["validation_policy"],
        evidence_evaluated=False,
        evidence_evaluation="offline_only",
    )
    write_json(directory / "metrics.json", metrics)
    # State contains only observable messages/actions; credential and private
    # reasoning filtering applies before persisting diagnostics.
    write_json(directory / "final_state.json", trace.safe(last_state))
    return {
        "case_id": case.case_id,
        "status": status,
        "attempt_dir": str(directory),
        "duration_seconds": metrics["duration_seconds"],
        "stop_reason": metrics["stop_reason"],
        "json_valid": validation["json_valid"],
        "schema_valid": validation["schema_valid"],
        "validation_policy": validation["validation_policy"],
        "evidence_evaluated": False,
        "evidence_evaluation": "offline_only",
        "llm_totals": metrics["llm_totals"],
        "ragflow_calls": metrics["tools"]["ragflow_calls"],
        "error": error,
    }


def _run_cli(args) -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    if args.env_file:
        env_file = args.env_file.resolve()
        if not env_file.is_relative_to(PROJECT_ROOT):
            raise ValueError("Credential file must be inside SP2")
        load_dotenv(env_file, override=False)
    cases = build_cases(args.checks, args.ground_truth)
    if args.case:
        wanted = set(args.case)
        cases = [case for case in cases if case.case_id in wanted]
        if {case.case_id for case in cases} != wanted:
            raise ValueError("Unknown case selection")
    if not args.case and not args.full_batch:
        raise ValueError("Specify --case 1 for a smoke test, or explicitly opt in with --full-batch")
    output_root = project_output(args.output_root)
    if args.worker_input:
        # Parent passes only the model-visible case; GT is never accessible
        # through specialist tools or attached to the run state.
        value = json.loads(args.worker_input.read_text())
        case = AuditCase(**{**value, "rule_names": tuple(value["rule_names"])})
        directory = project_output(args.worker_input.parent)
        os.environ["DEER_FLOW_HOME"] = str(directory / "runtime")
        config = experiment_config(args.model, max_tokens=args.max_tokens)
        summary = asyncio.run(
            run_attempt(
                case,
                config,
                rag_settings(),
                directory,
                thinking=args.thinking,
                timeout=scaled_timeout(args.timeout),
                no_progress_timeout=scaled_timeout(args.no_progress_timeout),
            )
        )
        write_json(directory / "attempt-summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return 0 if summary["status"] == "completed" else 1

    batch = output_root / (args.batch_name or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6])
    batch = project_output(batch)
    batch.mkdir(parents=True, exist_ok=args.resume)
    manifest = {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip(),
        "checks": {
            "path": str(args.checks.resolve()),
            "sha256": file_hash(args.checks),
        },
        "ground_truth": {
            "path": str(args.ground_truth.resolve()),
            "sha256": file_hash(args.ground_truth),
        },
        "lockfiles": {
            str(path.relative_to(PROJECT_ROOT)): file_hash(path)
            for path in (
                PROJECT_ROOT / "backend/uv.lock",
                PROJECT_ROOT / "frontend/pnpm-lock.yaml",
            )
        },
        "case_granularity": "20_ground_truth_rows",
        "case_ids": [case.case_id for case in cases],
        "model_config_name": args.model,
        "thinking_enabled": args.thinking,
        "max_tokens": args.max_tokens,
        "case_timeout_seconds": scaled_timeout(args.timeout),
        "no_progress_timeout_seconds": scaled_timeout(args.no_progress_timeout),
        "extra_json_repair": False,
        "validation_policy": DELIVERY_VALIDATION_POLICY,
        "evidence_evaluation": "offline_only",
        "sdk_max_retries": 0,
        "model_timeout_seconds": scaled_timeout(120),
        "native_request_deadline_seconds": scaled_timeout(args.llm_request_timeout),
        "rag_visible_fields_policy": VISIBLE_FIELDS_POLICY,
        "request_evidence_projection_policy": REQUEST_PROJECTION_POLICY,
        "request_context_projection_policy": CONTEXT_PROJECTION_POLICY,
        "timeout_multiplier": timeout_multiplier(),
        "central_skill_names": ["stackplanner-long-task", "stackplanner-reporting"],
        "connection_header": "close",
        "rule_sha256": {str(case.case_id): hashlib.sha256(case.rule_content.encode()).hexdigest() for case in cases},
        "dry_run": args.dry_run,
    }
    source_files = [
        *Path(__file__).parent.glob("*.py"),
        PROJECT_ROOT / "backend/scripts/run_tender_audit_sp2.py",
        *[
            PROJECT_ROOT / path
            for path in (
                "backend/packages/harness/deerflow/sp/extensions.py",
                "backend/packages/harness/deerflow/sp/runtime.py",
                "backend/packages/harness/deerflow/sp/agent_tools.py",
                "backend/packages/harness/deerflow/sp/actions/handlers/delegate.py",
                "backend/packages/harness/deerflow/sp/artifacts/adapter.py",
                "backend/packages/harness/deerflow/subagents/executor.py",
                "backend/packages/harness/deerflow/agents/middlewares/tool_error_handling_middleware.py",
                "backend/packages/harness/deerflow/agents/middlewares/system_message_coalescing_middleware.py",
                "backend/packages/harness/deerflow/agents/middlewares/dynamic_context_middleware.py",
                "backend/packages/harness/deerflow/models/vllm_provider.py",
                "backend/packages/harness/deerflow/models/factory.py",
                "backend/packages/harness/deerflow/config/app_config.py",
                "backend/packages/harness/deerflow/agents/middlewares/llm_error_handling_middleware.py",
                "backend/packages/harness/deerflow/sp/central/prompt.py",
                "backend/packages/harness/deerflow/sp/central/runtime_context.py",
                "backend/packages/harness/deerflow/utils/runtime_timeouts.py",
                "backend/packages/harness/deerflow/agents/lead_agent/prompt.py",
                "backend/packages/harness/deerflow/subagents/builtins/sp_specialists.py",
                "skills/public/stackplanner-reporting/SKILL.md",
            )
        ],
    ]
    manifest["source_sha256"] = {str(path.relative_to(PROJECT_ROOT)): file_hash(path) for path in source_files}
    manifest_path = batch / "run_manifest.json"
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text())
        if old != manifest:
            raise ValueError("Resume manifest differs; start a new batch to preserve comparability")
    else:
        write_json(manifest_path, manifest)
    write_json(batch / "cases.json", [case.public_dict() for case in cases])
    print(f"Batch directory: {batch}", flush=True)
    if args.dry_run:
        print(f"Dry run validated {len(cases)} cases; no model or RAG calls.", flush=True)
        return 0
    config, settings = (
        experiment_config(args.model, max_tokens=args.max_tokens),
        rag_settings(),
    )
    safe_trace = AuditTrace(
        batch / "preflight",
        secrets=(
            settings.api_key,
            str(config.models[0].model_extra.get("api_key", "")),
        ),
    )
    write_json(
        batch / "preflight/runtime-model-settings.json",
        runtime_model_settings(config, thinking=args.thinking),
    )
    write_json(
        batch / "provider-and-rag.json",
        safe_trace.safe({"model": config.models[0].model_dump(), "rag": settings.public_dict()}),
    )
    try:
        asyncio.run(preflight(cases, config, settings, batch / "preflight", args.thinking))
    except Exception as exc:
        write_json(batch / "preflight-error.json", {"error": safe_trace.safe(str(exc))})
        print(
            "Preflight failed; no audit cases launched. See preflight-error.json.",
            flush=True,
        )
        return 1
    summaries = []
    script = PROJECT_ROOT / "backend/scripts/run_tender_audit_sp2.py"
    for case in cases:
        case_dir = batch / f"case_{case.case_id:02d}"
        attempts = sorted(case_dir.glob("attempt_*/attempt-summary.json"))
        successful = [json.loads(path.read_text()) for path in attempts if json.loads(path.read_text()).get("status") == "completed"]
        if args.resume and successful:
            summaries.append(successful[-1])
            continue
        directory = case_dir / f"attempt_{len(list(case_dir.glob('attempt_*'))) + 1:03d}"
        directory.mkdir(parents=True, exist_ok=False)
        case_input = directory / "case-input.json"
        write_json(case_input, case.public_dict())
        command = [
            sys.executable,
            str(script),
            "--checks",
            str(args.checks.resolve()),
            "--ground-truth",
            str(args.ground_truth.resolve()),
            "--case",
            str(case.case_id),
            "--model",
            args.model,
            "--output-root",
            str(output_root),
            "--worker-input",
            str(case_input),
            "--timeout",
            str(args.timeout),
            "--no-progress-timeout",
            str(args.no_progress_timeout),
            "--llm-request-timeout",
            str(args.llm_request_timeout),
            "--timeout-multiplier",
            str(getattr(args, "timeout_multiplier", 1)),
            "--max-tokens",
            str(args.max_tokens),
            "--thinking" if args.thinking else "--no-thinking",
        ]
        if args.env_file:
            command += ["--env-file", str(args.env_file.resolve())]
        print(f"Running case {case.case_id:02d} ({args.model})", flush=True)
        worker_started = time.monotonic()
        with (directory / "trajectory.log").open("w", encoding="utf-8") as log:
            try:
                completed = subprocess.run(
                    command,
                    cwd=PROJECT_ROOT / "backend",
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=scaled_timeout(args.timeout + 45),
                    check=False,
                )
                returncode = completed.returncode
            except subprocess.TimeoutExpired:
                returncode = 124
        summary_path = directory / "attempt-summary.json"
        summary = (
            json.loads(summary_path.read_text())
            if summary_path.exists()
            else recover_failed_attempt(
                directory,
                case_id=case.case_id,
                returncode=returncode,
                duration_seconds=time.monotonic() - worker_started,
            )
        )
        if not summary_path.exists():
            write_json(summary_path, summary)
        summaries.append(summary)
        write_json(batch / "results.json", summaries)
        write_json(batch / "aggregate_metrics.json", aggregate_metrics(batch))
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    write_json(
        batch / "summary.json",
        {
            "requested": len(cases),
            "completed": sum(item["status"] == "completed" for item in summaries),
            "failed": sum(item["status"] != "completed" for item in summaries),
            "results": summaries,
            "quality_review": "Pending offline assessment; execution success is not semantic correctness.",
        },
    )
    return 0 if all(item["status"] == "completed" for item in summaries) else 1


def run_cli(args) -> int:
    with (
        timeout_multiplier_environment(getattr(args, "timeout_multiplier", 1)),
        request_deadline_environment(
            scaled_timeout(getattr(args, "llm_request_timeout", 150.0)),
            no_progress_timeout=scaled_timeout(args.no_progress_timeout),
        ),
    ):
        return _run_cli(args)
