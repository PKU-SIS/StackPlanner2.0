"""Explicitly gated official DeepSeek pilot. Default command is offline precheck.

No service or default runtime imports this script. Never run --start without a
separate user execution instruction. Credentials stay in the isolated child.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
import socket
import ssl
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import partial, wraps
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

import httpx
from dotenv import dotenv_values
from report_pilot_audit import AuditError
from report_pilot_worker import OfflinePilotWorker, supervise

_NATIVE_CONNECT = socket.socket.connect
_NATIVE_CONNECT_EX = socket.socket.connect_ex


@dataclass(frozen=True)
class Credentials:
    api_key: str = field(repr=False)
    base_url: str = field(repr=False)
    model_basic: str = field(repr=False)


def load_credentials(path):
    # Parse only; no shell expansion, os.environ mutation, or load_dotenv().
    values = dotenv_values(path, interpolate=False)
    selected = [
        values.get(name) for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL_BASIC")
    ]
    if not all(isinstance(value, str) and value.strip() for value in selected):
        raise ValueError("required credential variables are missing")
    key, base, model = (value.strip() for value in selected)
    try:
        url = urlsplit(base)
        allowed = (
            url.scheme == "https"
            and url.hostname == "api.deepseek.com"
            and url.port in (None, 443)
            and not url.username
            and not url.password
            and not url.query
            and not url.fragment
            and url.path.rstrip("/") in ("", "/v1")
        )
    except ValueError:
        allowed = False
    if not allowed:
        raise ValueError("credential endpoint is not the approved official endpoint")
    return Credentials(key, base, model)


def credential_status(path):
    credentials = load_credentials(path)
    return {
        "source_file": str(Path(path).resolve()),
        "required_variables_present": True,
        "official_endpoint": True,
        "default_model_matches_frozen": credentials.model_basic == "deepseek-flash",
        "selected_experiment_model": "deepseek-flash",
        "credential_values_exported": False,
    }


@dataclass(frozen=True)
class LivePermit:
    start: bool
    currency: str

    @classmethod
    def create(cls, *, start=False, currency=None):
        if start is not True or currency != "CNY":
            raise ValueError(
                "explicit execution instruction and CNY billing confirmation required"
            )
        return cls(True, "CNY")


class OfficialPilotWorker(OfflinePilotWorker):
    """Same reservation/receipt code; direct verified TLS transport only."""

    def __init__(self, *args, permit, credentials, resolver=None, **kwargs):
        if (
            not isinstance(permit, LivePermit)
            or permit.start is not True
            or permit.currency != "CNY"
        ):
            raise ValueError("execution permit missing")
        super().__init__(*args, **kwargs)
        self.permit, self.credentials = permit, credentials
        self.wire_allowed = ContextVar("pilot_official_wire", default=False)
        self.resolver = resolver or self._resolve_official

    @staticmethod
    def _resolve_official():
        return {
            row[4][0]
            for row in socket.getaddrinfo(
                "api.deepseek.com", 443, type=socket.SOCK_STREAM
            )
        }

    def _check_transport(self, client, request):
        transport = client._transport_for_url(request.url)
        pool = getattr(transport, "_pool", None)
        context = getattr(pool, "_ssl_context", None)
        if (
            type(transport) not in (httpx.HTTPTransport, httpx.AsyncHTTPTransport)
            or type(pool).__name__ not in ("ConnectionPool", "AsyncConnectionPool")
            or getattr(pool, "_retries", None) != 0
            or context is None
            or context.verify_mode != ssl.CERT_REQUIRED
            or not context.check_hostname
        ):
            self.stop("unsafe_official_transport")
            raise AuditError("proxy, unchecked TLS or hidden transport retry rejected")
        expected = "Bearer " + self.credentials.api_key
        if not hmac.compare_digest(request.headers.get("authorization", ""), expected):
            self.stop("credential_binding_mismatch")
            raise AuditError("request credential does not match approved binding")

    def _send_options(self, options):
        return {**options, "follow_redirects": False}

    @contextmanager
    def _wire_scope(self):
        token = self.wire_allowed.set(True)
        try:
            yield
        finally:
            self.wire_allowed.reset(token)

    @contextmanager
    def _network_scope(self):
        addresses = self.resolver()
        if not addresses or any(
            not ipaddress.ip_address(value).is_global for value in addresses
        ):
            raise AuditError("official DNS did not resolve exclusively to public IPs")

        def check(address):
            if (
                not self.wire_allowed.get()
                or not isinstance(address, tuple)
                or len(address) < 2
                or address[0] not in addresses
                or address[1] != 443
            ):
                self.stop("unaudited_socket")
                raise AuditError("unaudited or unapproved socket blocked")

        def connect(sock, address):
            check(address)
            return _NATIVE_CONNECT(sock, address)

        def connect_ex(sock, address):
            check(address)
            return _NATIVE_CONNECT_EX(sock, address)

        with (
            patch.object(socket.socket, "connect", connect),
            patch.object(socket.socket, "connect_ex", connect_ex),
        ):
            yield

    def finalize(self):
        result = super().finalize()
        result.update(
            api_experiments_authorized=self.permit.start, live_network_enabled=True
        )
        return result


def run_turn_sequence(requests, execute):
    results = []
    for number, request in enumerate(requests, 1):
        if request["turn_index"] != number or not request.get("message_id"):
            raise ValueError("frozen turns must be ordered and uniquely identified")
        row = execute(request)
        results.append(row)
        if not row.get("passed"):
            return {"completed": False, "turns": results}
    return {"completed": len(results) == 7, "turns": results}


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def safe_failure(function):
    @wraps(function)
    def wrapped(packet_dir, env_file, directory, channel):
        try:
            return function(packet_dir, env_file, directory, channel)
        except Exception as error:
            import traceback

            dump(
                directory / "startup-failure.json",
                {
                    "error_type": type(error).__name__,
                    "frames": [
                        {
                            "file": Path(row.filename).name,
                            "line": row.lineno,
                            "function": row.name,
                        }
                        for row in traceback.extract_tb(error.__traceback__)
                    ],
                },
            )
            raise

    return wrapped


@safe_failure
def _execute_child(packet_dir, env_file, directory, channel):
    """Executed only under the spawn supervisor, after the CLI execution gate."""
    import logging
    from uuid import uuid4
    from types import SimpleNamespace

    logging.disable(
        logging.CRITICAL
    )  # Numeric journal/native files replace raw SDK exception logs.
    root = Path(__file__).resolve().parents[1]
    os.environ.update(
        PYTHON_DOTENV_DISABLED="1",
        LANGSMITH_TRACING="false",
        LANGCHAIN_TRACING_V2="false",
        DEER_FLOW_PROJECT_ROOT=str(root),
        DEER_FLOW_HOME=str(directory / "runtime"),
    )
    creds = load_credentials(env_file)
    os.environ["LLM_API_KEY"] = creds.api_key
    for key in ("NO_PROXY", "no_proxy"):
        os.environ[key] = (
            os.environ.get(key, "") + ",api.deepseek.com,localhost,127.0.0.1"
        )
    base = OfflinePilotWorker.from_preflight(
        root, packet_dir, turn=1, emit=channel.send
    )
    worker = OfficialPilotWorker(
        base.audit,
        base.guard,
        turn=1,
        input_upper_bound=base.input_upper_bound,
        max_model_attempts=base.max_model_attempts,
        max_tool_attempts=base.max_tool_attempts,
        permit=LivePermit.create(start=True, currency="CNY"),
        credentials=creds,
        emit=channel.send,
    )
    packet = json.loads((packet_dir / "preflight.json").read_text())
    public = packet["profile"]
    from deerflow.config.app_config import AppConfig, set_app_config

    model = {
        key: value
        for key, value in public["model"].items()
        if key != "credential_env_name"
    }
    model["api_key"] = creds.api_key
    tools = []
    for row in public["tool_bindings"]:
        row = dict(row)
        row["group"] = (
            "web"
            if row["name"] in {"web_search", "web_fetch", "image_search"}
            else "file:write"
            if row["name"] in {"write_file", "str_replace"}
            else "file:read"
        )
        if row["name"] in ("web_search", "web_fetch"):
            row["api_key"] = creds.api_key
        tools.append(row)
    app = AppConfig.model_validate(
        {
            "models": [model],
            "sandbox": public["sandbox"],
            "memory": public["memory"],
            "tools": tools,
        }
    )
    set_app_config(app)
    from deerflow.config.paths import get_paths
    from deerflow.runtime.user_context import set_current_user
    from deerflow.sp.runtime import make_sp_agent
    from deerflow.agents.memory import queue as queue_module, updater
    import deerflow.subagents.executor as executor_module
    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.sqlite import SqliteSaver
    from deerflow.sp.report.turns.store import ReportTurnStore
    from deerflow.sp.report.length import contract_for_band

    user_id = "report-pilot"
    thread_id = "pilot-" + uuid4().hex
    set_current_user(SimpleNamespace(id=user_id, role="admin"))
    outputs = get_paths().sandbox_outputs_dir(thread_id, user_id=user_id)
    store = ReportTurnStore(outputs, user_id=user_id, thread_id=thread_id)
    frozen = {}
    original_report = None
    dump(
        directory / "runtime-binding.json",
        {"thread_id": thread_id, "user_id": user_id, "outputs": str(outputs)},
    )
    latest = {}
    public_requests = json.loads((packet_dir / "agent-input.json").read_text())["turns"]
    with SqliteSaver.from_conn_string(
        str(directory / "checkpoints.sqlite")
    ) as checkpoint:
        initial = {
            "configurable": {"thread_id": thread_id, "model_name": "deepseek-flash"}
        }
        graph = make_sp_agent(initial, app_config=app)
        graph.checkpointer = checkpoint

        def execute(request):
            nonlocal latest, original_report
            number = request["turn_index"]
            run_id = f"pilot-t{number}-" + uuid4().hex[:12]
            runtime = {
                **public["runtime"],
                "thread_id": thread_id,
                "run_id": run_id,
                "user_id": user_id,
                "model_name": "deepseek-flash",
                "__run_abort_event": worker.guard.abort_event,
            }
            extra = (
                {"report_length": contract_for_band(10000)}
                if number == 1
                else {
                    "report_dialogue": {"enabled": True, "report_id": original_report}
                }
            )
            message = HumanMessage(
                content=request["query"],
                id=request["message_id"],
                additional_kwargs=extra,
            )

            def operation(callback):
                nonlocal latest
                config = {
                    "configurable": {
                        k: v for k, v in runtime.items() if k != "__run_abort_event"
                    },
                    "recursion_limit": public["runtime"]["recursion_limit"],
                    "callbacks": [callback],
                }
                for value in graph.stream(
                    {"messages": [message]},
                    config=config,
                    context=runtime,
                    stream_mode="values",
                ):
                    latest = value
                return latest

            calls_before = set(worker.audit.calls)
            worker.run_turn(
                operation,
                turn=number,
                executor_module=executor_module,
                updater_module=updater,
                memory_queue=queue_module.get_memory_queue(),
            )
            row = inspect_turn(
                outputs,
                store,
                request,
                run_id,
                original_report,
                frozen,
                worker.audit,
                calls_before,
            )
            if number == 1 and row["passed"]:
                original_report = row["native_report_id"]
            dump(directory / f"turn-{number}.json", row)
            return row

        try:
            with worker.installed():
                result = run_turn_sequence(public_requests, execute)
        except Exception as exc:
            worker.stop("case_execution_failed")
            import traceback

            result = {
                "completed": False,
                "error_type": type(exc).__name__,
                "frames": [
                    {
                        "file": Path(row.filename).name,
                        "line": row.lineno,
                        "function": row.name,
                    }
                    for row in traceback.extract_tb(exc.__traceback__)
                ],
            }
        finally:
            dump(directory / "usage.json", worker.finalize())
            dump(directory / "result.json", result)


def inspect_turn(
    outputs, store, request, run_id, original_report, frozen, audit, calls_before
):
    """Native acceptance + SHA checks; evaluator expectations are never model input."""
    from deerflow.sp.report import store as native
    from deerflow.sp.report.ledger import ReportLedger
    from deerflow.sp.report.turns.store import file_manifest
    from deerflow.sp.report.assembly import final_binding_error

    number = request["turn_index"]
    unchanged = all(
        file_manifest(outputs / "report" / rid) == hashes
        for rid, hashes in frozen.items()
    )
    pointer = native.read_json(native.pointer_path(outputs))
    rid = pointer["report_id"]
    ledger = ReportLedger.load(native.ReportPaths(outputs, rid))
    delivered = (
        ledger.payload.get("status") == "delivered"
        and (ledger.payload.get("delivered") or {}).get("run_id") == run_id
    )
    roles = [row["role"] for key, row in audit.calls.items() if key not in calls_before]
    turn_id = None
    turn = None
    if number > 1:
        turn_id = store.index()["requests"].get(request["message_id"])
        turn = store.inspect(turn_id) if turn_id else None
    if delivered:
        valid = not final_binding_error(ledger)
        length = ledger.payload.get("final_report", {}).get("length", {})
        valid = valid and length.get("in_estimated_range") is True
    else:
        valid = (
            number in (5, 6)
            and turn is not None
            and turn["status"] == ("read_only" if number == 5 else "exact_reuse")
        )
    if number in (5, 6):
        valid = valid and not set(roles) & {"researcher", "writer"} and not delivered
    if number == 1:
        valid = valid and "git_agent" not in roles
    if delivered and unchanged:
        frozen[rid] = file_manifest(outputs / "report" / rid)
    return {
        "user_turn": number,
        "message_id": request["message_id"],
        "native_report_id": rid,
        "native_delivery_created": delivered,
        "dialogue_turn_id": turn_id,
        "roles": roles,
        "old_version_sha_unchanged": unchanged,
        "passed": bool(valid and unchanged),
        "semantic_intent_verified": False,
        "factual_quality_verified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", required=True, type=Path)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--billing-currency", choices=["CNY"])
    args = parser.parse_args()
    from report_multiturn_preflight import verify_packet

    root = Path(__file__).resolve().parents[1]
    verified = verify_packet(root, args.packet)
    status = credential_status(args.env_file)
    if not args.start:
        print(
            json.dumps(
                {
                    "offline_precheck": verified,
                    "credentials": status,
                    "provider_calls": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    LivePermit.create(start=args.start, currency=args.billing_currency)
    if args.run_dir is None:
        parser.error("--run-dir is required for an authorized start")
    allowed = root / ".deer-flow/pilot-live"
    directory = args.run_dir.resolve()
    if (
        not directory.is_relative_to(allowed.resolve())
        or directory == allowed.resolve()
    ):
        parser.error("run directory must be a fresh child of .deer-flow/pilot-live")
    directory.mkdir(parents=True, exist_ok=False)
    dump(
        directory / "launch.json",
        {
            "api_execution_requested": True,
            "currency": "CNY",
            "total_ceiling_cny": "50",
            "operating_ceiling_cny": "48",
            "credential_metadata": status,
            "packet_manifest_sha256": hashlib.sha256(
                (args.packet / "manifest.json").read_bytes()
            ).hexdigest(),
        },
    )
    target = partial(
        _execute_child, args.packet.resolve(), args.env_file.resolve(), directory
    )
    result = supervise(
        target, directory / "numeric-events.jsonl", api_execution_authorized=True
    )
    dump(directory / "supervisor.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
