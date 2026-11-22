"""Build or verify an API-disabled single-case pilot packet. No start/execute option."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import platform
import subprocess
from functools import wraps
from pathlib import Path

from report_pilot_audit import RateCard, money
from validate_report_multiturn_cases import load_public_cases, validate_suite

ENGINE_ROOTS = (
    "backend/packages/harness/deerflow/sp",
    "backend/packages/harness/deerflow/models",
    "backend/packages/harness/deerflow/subagents",
    "backend/packages/harness/deerflow/config",
    "backend/packages/harness/deerflow/agents",
    "backend/packages/harness/deerflow/sandbox",
    "backend/packages/harness/deerflow/community/deepseek_tools",
)
SKILLS = (
    "report-outline",
    "report-research",
    "report-node-writing",
    "report-word-budget",
)
ROLE_NAMES = (
    "central",
    "git_agent",
    "outline",
    "researcher",
    "writer",
    "memory_recaller",
    "memory_updater",
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def profile():
    """Public projection of the approved one-band historical configuration."""
    return {
        "schema_version": 1,
        "model": {
            "name": "deepseek-flash",
            "use": "deerflow.models.patched_deepseek:PatchedChatDeepSeek",
            "model": "deepseek-flash",
            "base_url": "https://api.deepseek.com/v1",
            "credential_env_name": "LLM_API_KEY",
            "max_tokens": 8192,
            "max_retries": 2,
            "request_timeout": 180,
            "supports_thinking": True,
            "supports_vision": False,
            "supports_reasoning_effort": False,
            "when_thinking_enabled": {"extra_body": {"thinking": {"type": "enabled"}}},
            "when_thinking_disabled": {
                "extra_body": {"thinking": {"type": "disabled"}}
            },
        },
        "memory": {"token_counting": "char"},
        "sandbox": {
            "use": "deerflow.sandbox.local:LocalSandboxProvider",
            "allow_host_bash": False,
        },
        "tool_bindings": [
            {
                "name": "web_search",
                "use": "deerflow.community.deepseek_tools.tools:web_search_tool",
                "timeout": 30,
                "max_results": 10,
            },
            {
                "name": "web_fetch",
                "use": "deerflow.community.deepseek_tools.tools:web_fetch_tool",
                "timeout": 30,
            },
            {
                "name": "image_search",
                "use": "deerflow.community.image_search.tools:image_search_tool",
                "max_results": 5,
            },
            {"name": "ls", "use": "deerflow.sandbox.tools:ls_tool"},
            {"name": "read_file", "use": "deerflow.sandbox.tools:read_file_tool"},
            {
                "name": "glob",
                "use": "deerflow.sandbox.tools:glob_tool",
                "max_results": 200,
            },
            {
                "name": "grep",
                "use": "deerflow.sandbox.tools:grep_tool",
                "max_results": 100,
            },
            {"name": "write_file", "use": "deerflow.sandbox.tools:write_file_tool"},
            {"name": "str_replace", "use": "deerflow.sandbox.tools:str_replace_tool"},
        ],
        "tool_credential_env_names": {
            "web_search": "LLM_API_KEY",
            "web_fetch": "LLM_API_KEY",
        },
        "runtime": {
            "thinking_enabled": True,
            "sp_subagent_thinking_enabled": True,
            "max_concurrent_subagents": 6,
            "sp_subagent_timeout_seconds": 1470,
            "sp_central_timeout_seconds": 90,
            "sp_central_retry_timeout_seconds": 60,
            "recursion_limit": 1600,
            "worker_no_proxy_append": ["127.0.0.1", "localhost", "api.deepseek.com"],
        },
        "source": "Historical stability batch 20261121/075632Z/repeat-17-a/17: public model/tool/runtime projection; no credentials copied",
        "model_version": {
            "request_alias": "deepseek-flash",
            "historical_reported_name": "deepseek-flash",
            "historical_backend_version": None,
            "documented_version_on_2026_11_22": "DeepSeek-V4.1-Flash-1017",
            "immutable_weight_pin_available": False,
            "accept_response_alias_with_unverified_backend_version": True,
        },
    }


def role_matrix(public_profile):
    from deerflow.config.app_config import AppConfig
    from deerflow.models.factory import create_chat_model
    from langchain_core.messages import HumanMessage

    model = {
        k: v for k, v in public_profile["model"].items() if k != "credential_env_name"
    }
    model["api_key"] = "offline-payload-probe-not-a-credential"
    app = AppConfig.model_validate(
        {
            "models": [model],
            "sandbox": public_profile["sandbox"],
            "memory": public_profile["memory"],
        }
    )
    from deerflow.sp.runtime import _resolve_report_role_thinking

    rows = {}
    for role in ROLE_NAMES:
        from deerflow.sp.runtime import REPORT_OUTLINE_OUTPUT_MAX_TOKENS

        overrides = (
            {"max_tokens": REPORT_OUTLINE_OUTPUT_MAX_TOKENS}
            if role == "outline"
            else {}
        )
        thinking = (
            False
            if role == "memory_updater"
            else _resolve_report_role_thinking(
                role, True, public_profile["runtime"], public_profile["runtime"]
            )
        )
        client = create_chat_model(
            name=model["name"],
            thinking_enabled=thinking,
            app_config=app,
            attach_tracing=False,
            **overrides,
        )
        # Serialization only: invoke/stream/client.send are never called.
        payload = client._get_request_payload(
            [HumanMessage(content="offline request-shape probe")]
        )
        rows[role] = {
            "model_alias": payload["model"],
            "max_tokens": payload.get("max_tokens")
            or payload.get("max_completion_tokens"),
            "thinking": payload.get("extra_body", {}).get("thinking"),
            "sdk_max_retries": client.max_retries,
            "executor": "perception"
            if role == "git_agent"
            else "background memory updater"
            if role == "memory_updater"
            else role,
            "reasoning_effort_request": payload.get("reasoning_effort"),
            "reasoning_effort_documented_default": "high" if thinking else None,
        }
    return rows


def frozen_engine(root):
    names = subprocess.check_output(
        ["git", "-C", str(root), "ls-files", "--", *ENGINE_ROOTS], text=True
    ).splitlines()
    names += [f"skills/public/{skill}/SKILL.md" for skill in SKILLS]
    names += [
        str(p.relative_to(root))
        for p in sorted((root / "skills/report-multiturn").glob("*/SKILL.md"))
    ]
    names += [
        "scripts/report_multiturn_preflight.py",
        "scripts/report_pilot_audit.py",
        "scripts/report_pilot_worker.py",
        "scripts/report_pilot_entry.py",
        "scripts/report_pilot_admission.py",
        "docs/research-report/multiturn-pilot-preparation-20261122/ADMISSION_REVIEW.json",
        "scripts/validate_report_multiturn_cases.py",
    ]
    return {name: digest(root / name) for name in sorted(set(names))}


def source_projection_proof(public_profile):
    """Verify only known public fields of the old batch, never export secrets."""
    path = Path(
        "/data/sp/yzb/StackPlanner2/StackPlanner2.0_Report/evaluation_runs/report_stability_10k_20261121/075632Z/repeat-17-a/17/config.yaml"
    )
    if not path.exists():
        return {
            "verified_here": False,
            "reason": "historical config is not present; public projection must be reviewed independently",
        }
    import yaml

    raw = yaml.safe_load(path.read_text())
    old = raw["models"][0]
    for key in (
        "name",
        "use",
        "model",
        "max_tokens",
        "max_retries",
        "request_timeout",
        "supports_thinking",
        "supports_vision",
        "when_thinking_enabled",
        "when_thinking_disabled",
    ):
        if old.get(key) != public_profile["model"][key]:
            raise ValueError("public model projection differs from historical source")
    if old.get("api_key") != "$" + public_profile["model"]["credential_env_name"]:
        raise ValueError(
            "model credential indirection needs separate review; value omitted"
        )
    http_source = path.with_name("http-requests.jsonl")
    observed = {
        (row.get("host"), row.get("path"))
        for line in http_source.read_text().splitlines()
        if line.strip()
        and (row := json.loads(line)).get("path", "").endswith("/chat/completions")
    }
    if observed != {("api.deepseek.com", "/v1/chat/completions")}:
        raise ValueError("historical model endpoint does not match public projection")
    expected = [
        {
            key: row[key]
            for key in ("name", "use", "timeout", "max_results")
            if key in row
        }
        for row in raw["tools"]
    ]
    if (
        expected != public_profile["tool_bindings"]
        or raw["sandbox"] != public_profile["sandbox"]
        or raw["memory"] != public_profile["memory"]
    ):
        raise ValueError(
            "public tool/sandbox/memory projection differs from historical source"
        )
    for name in ("web_search", "web_fetch"):
        item = next(row for row in raw["tools"] if row["name"] == name)
        if (
            item.get("api_key")
            != "$" + public_profile["tool_credential_env_names"][name]
        ):
            raise ValueError(
                "tool credential indirection requires separate review; secret value omitted"
            )
    return {
        "verified_here": True,
        "source_file": str(path),
        "source_sha256": digest(path),
        "endpoint_observation_sha256": digest(http_source),
        "values_exported": "public whitelist only",
        "credential_values_exported": False,
    }


def environment_snapshot():
    versions = {}
    for name in (
        "langchain",
        "langchain-core",
        "langchain-deepseek",
        "langchain-openai",
        "langgraph",
        "httpx",
        "pydantic",
        "tiktoken",
    ):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    modules = {}
    for name in ("langchain_deepseek.chat_models", "langchain_openai.chat_models.base"):
        module = importlib.import_module(name)
        modules[name] = digest(module.__file__)
    return {
        "python": platform.python_version(),
        "packages": versions,
        "sdk_source_sha256": modules,
    }


def offline_only(function):
    """Deny workspace network connections even if an SDK changes initialization."""

    @wraps(function)
    def wrapped(*args, **kwargs):
        import socket
        from unittest.mock import patch

        def denied(*args, **kwargs):
            raise RuntimeError("network connections are forbidden in pilot preparation")

        with (
            patch.object(socket.socket, "connect", denied),
            patch.object(socket.socket, "connect_ex", denied),
        ):
            return function(*args, **kwargs)

    return wrapped


@offline_only
def build_packet(root, destination, *, budget_cny=None, admission_review=False):
    root, destination = Path(root).resolve(), Path(destination)
    if budget_cny is not None:
        budget_cny = str(money(budget_cny))
        if money(budget_cny) == 0:
            raise ValueError("cost ceiling must be positive")
    if destination.exists():
        raise FileExistsError("pilot packet destination already exists")
    suite = root / "docs/research-report/multiturn-evaluation-20261122"
    validate_suite(suite)
    selected = next(
        case
        for case in load_public_cases(suite / "requests.json")["cases"]
        if case["case_id"] == "library-rag"
    )
    from deerflow.sp.report import length

    if not Path(length.__file__).resolve().is_relative_to(root):
        raise ValueError("PYTHONPATH must point to the new checkout")
    public_profile = profile()
    source_proof = source_projection_proof(public_profile)
    roles = role_matrix(public_profile)
    limits = {
        "currency": "CNY",
        "max_cost_cny": budget_cny,
        "budget_status": "preference_only_not_execution_authorization"
        if budget_cny is not None
        else "pending_user_preference",
        "proposed_max_cost_cny": "100.00",
        "max_input_tokens": 20_000_000,
        "max_output_tokens": 1_000_000,
        "max_direct_tool_attempts": 2000,
        "max_model_http_attempts": 600,
        "turn_timeout_seconds": 1500,
        "suite_timeout_seconds": 7200,
        "no_progress_timeout_seconds": 300,
        "outer_automatic_restarts": 0,
        "native_retry_policy_changed": False,
        "unknown_usage_or_transport_outcome": "stop_and_keep_reservation",
        "account_billing_currency_confirmed": False,
        "provider_input_context_ceiling_tokens": None,
        "provider_input_context_ceiling_verified": False,
    }
    reviewed = None
    if admission_review:
        from report_pilot_admission import reviewed_limits

        extra_limits, reviewed = reviewed_limits(
            root, budget_cny, public_profile, roles
        )
        limits.update(extra_limits)
    rates = {
        "currency": "CNY",
        "basis": "peak-rate conservative upper estimate; cache reads included in input",
        **RateCard().__dict__,
        "verified_on": "2026-11-22",
        "pricing_source": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
        "tool_billing_source": "https://api-docs.deepseek.com/zh-cn/guides/search/",
        "thinking_source": "https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/",
        "direct_tool_rule": "HTTP 200 plus nonempty result/body; charged per request, not result count; transport-unknown is not known-free",
    }
    from deerflow.sp.report.length import contract_for_band

    packet = {
        "schema_version": 1,
        "api_experiments_authorized": False,
        "selected_case_id": selected["case_id"],
        "turns": len(selected["turns"]),
        "runtime_engine_commit": subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip(),
        "engine_sha256": frozen_engine(root),
        "environment": environment_snapshot(),
        "suite_manifest_sha256": digest(suite / "manifest.json"),
        "roles": roles,
        "native_length_contract": contract_for_band(10000),
        "limits": limits,
        "profile": public_profile,
        "historical_source_projection": source_proof,
        "execution_entrypoint": None,
        "provider_calls": 0,
        "credentials_loaded": False,
        "preparation_scope": "offline packet and numeric audit interfaces; actual worker/transport installation must pass the separate launch gate",
        "remaining_launch_requirements": [
            "explicit API execution authorization",
            "confirmed cost ceiling and account currency",
            "current rate/model-version review",
            "live worker admission review after offline transport wiring",
            "verified provider input-context ceiling for reservations",
        ],
    }
    if reviewed is not None:
        packet["admission_review"] = reviewed
        packet["remaining_launch_requirements"] = [
            "explicit API execution authorization",
            "confirmed account billing currency",
            "provider rate/model-version recheck at launch",
            "live worker admission review after offline transport wiring",
        ]
    destination.mkdir(parents=True)
    write(destination / "agent-input.json", selected)
    write(destination / "profile.json", public_profile)
    write(destination / "limits.json", limits)
    write(destination / "rate-card.json", rates)
    write(destination / "preflight.json", packet)
    write(
        destination / "manifest.json",
        {
            "schema_version": 1,
            "api_experiments_authorized": False,
            "sha256": {p.name: digest(p) for p in sorted(destination.glob("*.json"))},
        },
    )
    return packet


@offline_only
def verify_packet(root, destination):
    root, destination = Path(root).resolve(), Path(destination)
    manifest = json.loads((destination / "manifest.json").read_text())
    if manifest.get("api_experiments_authorized") is not False:
        raise ValueError("preparation packet cannot authorize an API experiment")
    for name, value in manifest["sha256"].items():
        if Path(name).name != name or digest(destination / name) != value:
            raise ValueError("prepared packet checksum mismatch")
    if {p.name for p in destination.iterdir()} != set(manifest["sha256"]) | {
        "manifest.json"
    }:
        raise ValueError("unexpected file in immutable preparation packet")
    packet = json.loads((destination / "preflight.json").read_text())
    if environment_snapshot() != packet["environment"]:
        raise ValueError("dependency environment checksum/version mismatch")
    for name, value in packet["engine_sha256"].items():
        if (
            not (root / name).resolve().is_relative_to(root)
            or digest(root / name) != value
        ):
            raise ValueError("engine checksum mismatch")
    if (
        digest(
            root / "docs/research-report/multiturn-evaluation-20261122/manifest.json"
        )
        != packet["suite_manifest_sha256"]
    ):
        raise ValueError("suite checksum mismatch")
    return {
        "selected_case_id": packet["selected_case_id"],
        "turns": packet["turns"],
        "provider_calls": 0,
        "api_experiments_authorized": False,
        "offline_packet_verified": True,
        "api_execution_ready": False,
        "remaining_launch_requirements": packet["remaining_launch_requirements"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--budget-cny", help="Budget preference only; never enables execution"
    )
    parser.add_argument(
        "--admission-review",
        action="store_true",
        help="Apply the frozen 50 CNY public review; never enables execution",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if not args.verify:
        build_packet(
            root,
            args.output,
            budget_cny=args.budget_cny,
            admission_review=args.admission_review,
        )
    print(json.dumps(verify_packet(root, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
