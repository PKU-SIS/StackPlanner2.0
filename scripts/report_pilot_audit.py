"""Experiment-only usage accounting and budget reservations. No network or launcher."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from threading import Event, RLock


class AuditError(RuntimeError):
    pass


def money(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise AuditError("invalid money amount") from None
    if not result.is_finite() or result < 0:
        raise AuditError("invalid money amount")
    return result


def integer(value):
    if type(value) is not int or value < 0:
        raise AuditError("invalid token count")
    return value


@dataclass(frozen=True)
class RateCard:
    """CNY peak-rate upper estimate, checked against official docs 2026-11-22."""

    input_miss_per_million: str = "2"
    input_hit_per_million: str = "0.04"
    output_per_million: str = "8"
    direct_tool_per_success: str = "0.005"

    def model_cost(self, inp, out, cache=None):
        hit = 0 if cache is None else integer(cache)
        if hit > inp:
            raise AuditError("cache is not a subset of input")
        return (
            (inp - hit) * money(self.input_miss_per_million)
            + hit * money(self.input_hit_per_million)
            + out * money(self.output_per_million)
        ) / Decimal(1_000_000)


class PilotUsageAudit:
    """Count real callback starts, preserve missing usage and deduplicate source IDs."""

    def __init__(self, rates):
        self.rates = rates
        self.abort_event = Event()
        self.calls, self.tools, self.anomalies = {}, {}, []
        self.http_coverage_verified = False
        self.lock = RLock()

    def _error(self, code):
        with self.lock:
            self.abort_event.set()
            self.anomalies.append(code)
            raise AuditError(code)

    def start(self, source_id, *, turn, role, model):
        if not source_id or role not in {
            "central",
            "git_agent",
            "outline",
            "researcher",
            "writer",
            "memory_recaller",
            "memory_updater",
        }:
            self._error("unattributed model call")
        with self.lock:
            identity = {"turn": turn, "role": role, "configured_model": model}
            previous = self.calls.get(str(source_id))
            if previous is not None:
                if any(previous[key] != value for key, value in identity.items()):
                    self._error("conflicting source identity")
                return
            self.calls[str(source_id)] = {
                **identity,
                "status": "started",
                "usage": None,
                "reported_model": None,
            }

    def end(self, source_id, usage, *, reported_model=None):
        with self.lock:
            row = self.calls.get(str(source_id))
            if row is None:
                self._error("usage has no observed request start")
            if usage is None or not {"input_tokens", "output_tokens"} <= set(usage):
                if row["status"] == "complete":
                    self._error("conflicting duplicate usage")
                row.update(status="missing_usage", reported_model=reported_model)
                self.abort_event.set()
                return
            try:
                inp, out = (
                    integer(usage["input_tokens"]),
                    integer(usage["output_tokens"]),
                )
                total = integer(usage.get("total_tokens", inp + out))
                if total != inp + out:
                    raise AuditError("input/output/total mismatch")
                detail = usage.get("input_token_details") or {}
                cache = detail.get("cache_read") if isinstance(detail, dict) else None
                if cache is not None and integer(cache) > inp:
                    raise AuditError("cache is not a subset of input")
            except AuditError as error:
                self._error(str(error))
            normalized = {
                "input_tokens": inp,
                "output_tokens": out,
                "total_tokens": total,
                "cache_read_tokens": cache,
            }
            if row["status"] == "complete":
                if (
                    row["usage"] != normalized
                    or row["reported_model"] != reported_model
                ):
                    self._error("conflicting duplicate usage")
                return
            row.update(
                status="complete", usage=normalized, reported_model=reported_model
            )
            if reported_model not in {
                "deepseek-flash",
                "DeepSeek-V4.1-Flash-1017",
                "deepseek-v4.1-flash-1017",
            }:
                self.anomalies.append("unverified reported model")
                self.abort_event.set()

    def tool_attempt(self, attempt_id, *, turn, tool, status, has_result):
        if tool not in {"search", "fetch"}:
            self._error("unpriced direct tool")
        with self.lock:
            record = {
                "turn": turn,
                "tool": tool,
                "status": status,
                "has_result": has_result,
            }
            if attempt_id in self.tools and self.tools[attempt_id] != record:
                self._error("conflicting tool attempt identity")
            if (
                status is not None
                and (type(status) is not int or not 100 <= status <= 599)
                or has_result is not None
                and type(has_result) is not bool
            ):
                self._error("invalid direct-tool outcome")
            self.tools[attempt_id] = record
            if status is None or status == 200 and has_result is None:
                self.abort_event.set()

    def reconcile_model_requests(
        self, observed_source_ids, *, unknown_http_attempts=()
    ):
        """Require a separate HTTP/SDK request inventory, not only usage callbacks."""
        with self.lock:
            if set(observed_source_ids) != set(self.calls) or unknown_http_attempts:
                self.http_coverage_verified = False
                self._error(
                    "HTTP request inventory does not reconcile with model callbacks"
                )
            self.http_coverage_verified = True

    def snapshot(self):
        with self.lock:
            valid = [row for row in self.calls.values() if row["status"] == "complete"]
            missing = sorted(
                key for key, row in self.calls.items() if row["status"] != "complete"
            )
            observed = {
                key: sum(row["usage"][key] for row in valid)
                for key in ("input_tokens", "output_tokens", "total_tokens")
            }
            tools_known = all(
                row["status"] is not None
                and (row["status"] != 200 or row["has_result"] is not None)
                for row in self.tools.values()
            )
            tool_successes = sum(
                row["status"] == 200 and row["has_result"] is True
                for row in self.tools.values()
            )
            tool_cost = tool_successes * money(self.rates.direct_tool_per_success)
            usage_complete = not missing and not self.anomalies
            complete = usage_complete and tools_known and self.http_coverage_verified
            priced = [
                row
                for row in valid
                if row["reported_model"]
                in {
                    "deepseek-flash",
                    "DeepSeek-V4.1-Flash-1017",
                    "deepseek-v4.1-flash-1017",
                }
            ]
            known_cost = (
                sum(
                    (
                        self.rates.model_cost(
                            row["usage"]["input_tokens"],
                            row["usage"]["output_tokens"],
                            row["usage"]["cache_read_tokens"],
                        )
                        for row in priced
                    ),
                    Decimal(0),
                )
                + tool_cost
            )
            groups = {}
            for field in ("role", "turn"):
                buckets = {}
                for value in {row[field] for row in self.calls.values()}:
                    members = [
                        row for row in self.calls.values() if row[field] == value
                    ]
                    known = [row for row in members if row["status"] == "complete"]
                    counts = {
                        key: sum(row["usage"][key] for row in known)
                        for key in ("input_tokens", "output_tokens", "total_tokens")
                    }
                    buckets[str(value)] = {
                        "calls": len(members),
                        "usage_complete": len(known) == len(members),
                        **{
                            key: total if len(known) == len(members) else None
                            for key, total in counts.items()
                        },
                        **{"observed_" + key: total for key, total in counts.items()},
                    }
                groups["tokens_by_" + field] = buckets
            return {
                **groups,
                "logical_model_calls": len(self.calls),
                "calls_with_usage": len(valid),
                "usage_complete": usage_complete,
                "billing_complete": complete,
                **{
                    key: value if usage_complete else None
                    for key, value in observed.items()
                },
                **{"observed_" + key: value for key, value in observed.items()},
                "http_coverage_verified": self.http_coverage_verified,
                "cache_read_tokens_observed": sum(
                    row["usage"]["cache_read_tokens"] or 0 for row in valid
                ),
                "cache_reporting_complete": all(
                    row["usage"]["cache_read_tokens"] is not None for row in valid
                ),
                "estimated_cost_cny_upper": str(known_cost) if complete else None,
                "observed_cost_cny_upper_subtotal": str(known_cost),
                "cost_basis": "official peak rates; missing cache priced as miss; not an invoice",
                "missing_usage_sources": missing,
                "anomalies": list(self.anomalies),
                "by_role": dict(Counter(row["role"] for row in self.calls.values())),
                "http_tool_attempts": len(self.tools),
                "direct_tool_successes": tool_successes,
                "direct_tool_cost_cny_observed": str(tool_cost),
                "abort_requested": self.abort_event.is_set(),
            }


class BudgetGuard:
    """Thread-safe pre-call reservations; unknown outcomes never release exposure."""

    def __init__(self, *, max_cost_cny, max_input_tokens, max_output_tokens):
        self.maximum = None if max_cost_cny is None else money(max_cost_cny)
        self.max_input, self.max_output = (
            integer(max_input_tokens),
            integer(max_output_tokens),
        )
        self.abort_event = Event()
        self.lock = RLock()
        self.pending, self.completed = {}, {}
        self.spent = Decimal(0)
        self.inp = self.out = 0

    def reserve(self, identity, *, input_tokens, output_tokens, cost_cny):
        with self.lock:
            if self.maximum is None:
                raise AuditError("unconfirmed cost limit")
            if self.abort_event.is_set():
                raise AuditError("budget guard has stopped")
            record = {
                "input_tokens": integer(input_tokens),
                "output_tokens": integer(output_tokens),
                "cost": money(cost_cny),
            }
            if identity in self.pending or identity in self.completed:
                raise AuditError(
                    "reservation identity must be unique per actual attempt"
                )
            if (
                self.spent
                + sum(r["cost"] for r in self.pending.values())
                + record["cost"]
                > self.maximum
                or self.inp
                + sum(r["input_tokens"] for r in self.pending.values())
                + record["input_tokens"]
                > self.max_input
                or self.out
                + sum(r["output_tokens"] for r in self.pending.values())
                + record["output_tokens"]
                > self.max_output
            ):
                self.abort_event.set()
                raise AuditError("budget would be exceeded including inflight exposure")
            self.pending[identity] = record

    def settle(self, identity, *, input_tokens, output_tokens, cost_cny):
        with self.lock:
            actual = {
                "input_tokens": integer(input_tokens),
                "output_tokens": integer(output_tokens),
                "cost": money(cost_cny),
            }
            if identity in self.completed:
                if self.completed[identity] != actual:
                    self.abort_event.set()
                    raise AuditError("conflicting settlement")
                return
            reserved = self.pending.pop(identity)
            self.completed[identity] = actual
            self.spent += actual["cost"]
            self.inp += actual["input_tokens"]
            self.out += actual["output_tokens"]
            if any(actual[key] > reserved[key] for key in actual):
                self.abort_event.set()
                raise AuditError(
                    "actual usage exceeded reservation; preserved actual charge"
                )

    def unknown(self, identity):
        with self.lock:
            if identity not in self.pending:
                raise AuditError("unknown reservation")
            self.abort_event.set()

    def snapshot(self):
        with self.lock:
            return {
                "spent_cost_cny": str(self.spent),
                "reserved_cost_cny": str(
                    sum((r["cost"] for r in self.pending.values()), Decimal(0))
                ),
                "input_tokens": self.inp,
                "output_tokens": self.out,
                "inflight": sorted(self.pending),
                "abort_requested": self.abort_event.is_set(),
            }


def native_role(name, tool_names=()):
    if name == "sp-perception":
        if set(tool_names) == {
            "report_inspect_version",
            "report_prepare_turn",
            "report_reuse_version",
        }:
            return "git_agent"
        raise AuditError("perception is not the host-bound GitAgent profile")
    roles = {
        "sp-outline": "outline",
        "sp-researcher": "researcher",
        "sp-reporter": "writer",
        "sp-memory-recaller": "memory_recaller",
    }
    if name not in roles:
        raise AuditError("unattributed native executor")
    return roles[name]


# Imported here rather than in the offline packet generator.
try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError:
    BaseCallbackHandler = object


class PilotCallback(BaseCallbackHandler):
    """Numeric-only callback. No prompts, credentials, or private reasoning persisted."""

    run_inline = True

    def __init__(self, audit, *, turn, role, model="deepseek-flash"):
        self.audit, self.turn, self.role, self.model = audit, turn, role, model

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self.audit.start(str(run_id), turn=self.turn, role=self.role, model=self.model)

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        self.on_chat_model_start(serialized, [], run_id=run_id)

    def on_llm_end(self, response, *, run_id, **kwargs):
        generations = response.generations
        message = generations[0][0].message if generations and generations[0] else None
        metadata = getattr(message, "response_metadata", None) or {}
        self.audit.end(
            str(run_id),
            getattr(message, "usage_metadata", None),
            reported_model=metadata.get("model_name") or metadata.get("model"),
        )

    def on_llm_error(self, error, *, run_id, **kwargs):
        self.audit.end(str(run_id), None)


def observe_native_subagents(executor_module, audit, *, turn):
    """Experiment-scoped callback bridge; restore native methods on exit.

    DR2's synchronous execute path preserves ContextVars into its isolated loop.
    The original collector and existing observer continue receiving unchanged data.
    """
    from contextlib import contextmanager
    from contextvars import ContextVar
    from unittest.mock import patch

    scope = ContextVar("pilot_numeric_callback", default=None)
    original_execute = executor_module.SubagentExecutor.execute
    original_collector = executor_module.SubagentTokenCollector

    def execute(executor, *args, **kwargs):
        role = native_role(executor.config.name, executor.config.tools or ())
        token = scope.set(PilotCallback(audit, turn=turn, role=role))
        try:
            return original_execute(executor, *args, **kwargs)
        finally:
            scope.reset(token)

    class ObservedCollector(original_collector):
        run_inline = True

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.pilot = scope.get()
            if self.pilot is None:
                audit._error("native collector missing experiment role binding")

        def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
            self.pilot.on_chat_model_start(serialized, messages, run_id=run_id)

        def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
            self.pilot.on_chat_model_start(serialized, [], run_id=run_id)

        def on_llm_end(self, response, *, run_id, **kwargs):
            super().on_llm_end(response, run_id=run_id, **kwargs)
            self.pilot.on_llm_end(response, run_id=run_id)

        def on_llm_error(self, error, *, run_id, **kwargs):
            self.pilot.on_llm_error(error, run_id=run_id)

    @contextmanager
    def installed():
        with (
            patch.object(executor_module.SubagentExecutor, "execute", execute),
            patch.object(executor_module, "SubagentTokenCollector", ObservedCollector),
        ):
            yield

    return installed()
