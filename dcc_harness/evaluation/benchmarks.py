"""Native SP delivery extraction and independent benchmark scoring.

This module never chooses a specialist's answer on behalf of Central and owns
no agent loop. Collect accepted FINISH or the native graph's visible text return.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from dcc_harness.data import PublicTask, Task

PROTOCOL = "sp-native-benchmarks-v3"
DELIVERY_CONTRACT = "native-finish-or-text-v1"
MAX_DELIVERY_BYTES = 16 * 1024 * 1024


class DeliveryError(ValueError):
    def __init__(self, status: str, detail: str):
        self.status = status
        super().__init__(f"{status}: {detail}")


def extract_delivery(
    state: dict, *, thread_id: str, run_id: str, native_returned: bool = False
) -> tuple[str, dict | None]:
    if state.get("sp_pending_human_interaction"):
        raise DeliveryError("ask_human", "native SP requests human input")
    if (
        state.get("sp_current_stage") != "finished"
        or (state.get("sp_last_handler_result") or {}).get("next_step") != "finish"
    ):
        return _native_text_delivery(
            state, run_id=run_id, native_returned=native_returned
        ), None
    selected = state.get("sp_last_final_artifact_ref")
    if not selected:
        text = str(state.get("sp_last_run_summary") or "").strip()
        if not text:
            raise DeliveryError("final_parse_error", "accepted text delivery is empty")
        return text, None
    refs = state.get("sp_current_artifact_refs") or {}
    candidates = [
        r for key, r in refs.items() if key != "_history" and isinstance(r, dict)
    ]
    candidates.extend(r for r in refs.get("_history", []) if isinstance(r, dict))
    ref = next(
        (
            r
            for r in candidates
            if selected
            in (r.get("artifact_id"), r.get("artifact_url"), r.get("virtual_path"))
        ),
        None,
    )
    if ref is None:
        raise DeliveryError("final_parse_error", "selected artifact is not registered")
    if (
        ref.get("thread_id") != thread_id
        or ref.get("run_id") != run_id
        or ref.get("is_current") is False
    ):
        raise DeliveryError(
            "final_parse_error", "selected artifact is outside the current thread/run"
        )
    if (ref.get("metadata") or {}).get("completion_status", "complete") != "complete":
        raise DeliveryError("unfinished", "selected artifact is incomplete")
    virtual_path = str(ref.get("virtual_path") or "")
    thread_data = state.get("thread_data") or {}
    resolved = None
    for area in ("outputs", "workspace"):
        prefix = f"/mnt/user-data/{area}/"
        if virtual_path.startswith(prefix) and thread_data.get(f"{area}_path"):
            root = Path(thread_data[f"{area}_path"]).resolve()
            relative = PurePosixPath(virtual_path[len(prefix) :])
            if ".." in relative.parts or relative.is_absolute():
                break
            resolved = (root / Path(*relative.parts)).resolve()
            if not resolved.is_relative_to(root):
                resolved = None
            break
    if resolved is None:
        raise DeliveryError(
            "final_parse_error", "unsafe or unavailable native artifact path"
        )
    try:
        if resolved.stat().st_size > MAX_DELIVERY_BYTES:
            raise DeliveryError(
                "final_parse_error", "delivery exceeds 16 MiB; refusing truncation"
            )
        before = resolved.stat()
        with resolved.open("rb") as handle:
            raw = handle.read(MAX_DELIVERY_BYTES + 1)
        after = resolved.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise DeliveryError(
                "final_parse_error", "artifact changed during delivery extraction"
            )
        if len(raw) > MAX_DELIVERY_BYTES or b"\x00" in raw:
            raise DeliveryError(
                "final_parse_error", "invalid or oversized text artifact"
            )
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise DeliveryError(
            "final_parse_error", "selected native artifact cannot be read"
        ) from exc
    observed_hash = hashlib.sha256(raw).hexdigest()
    if ref.get("content_hash") and observed_hash != ref["content_hash"]:
        raise DeliveryError(
            "final_parse_error", "selected artifact integrity check failed"
        )
    selected_ref = dict(ref)
    if not ref.get("content_hash"):
        metadata = ref.get("metadata") or {}
        if not metadata.get("registered_existing_file"):
            raise DeliveryError(
                "final_parse_error", "native artifact lacks integrity metadata"
            )
        preview = metadata.get("finalization_preview")
        if (
            preview is not None
            and not metadata.get("finalization_preview_truncated")
            and text != preview
        ):
            raise DeliveryError(
                "final_parse_error", "artifact differs from native finalization preview"
            )
        # SP registers native write_file outputs without an expected hash. Do
        # not manufacture one: retain this as an observed snapshot explicitly.
        selected_ref.update(
            observed_sha256=observed_hash, integrity="native_existing_file_snapshot"
        )
    return text, selected_ref


def _native_text_delivery(state: dict, *, run_id: str, native_returned: bool) -> str:
    # Only the inference caller can attest that astream exhausted normally.
    # A saved/intermediate state or a timed-out graph is not a text completion.
    if not native_returned or state.get("sp_loop_run_id") != run_id:
        raise DeliveryError(
            "unfinished", "native SP did not return a current-run text delivery"
        )
    if (
        state.get("__interrupt__")
        or state.get("sp_current_action")
        or state.get("sp_active_delegate_id")
    ):
        raise DeliveryError(
            "unfinished", "native SP still has interrupted or active work"
        )
    from deerflow.sp.agent_tools import SPFinishAvailabilityMiddleware
    from langchain_core.messages import AIMessage

    messages = state.get("messages") or []
    latest = messages[-1] if messages else None
    if not isinstance(latest, AIMessage):
        raise DeliveryError("unfinished", "native return has no final Central message")
    handler = state.get("sp_last_handler_result") or {}
    if (
        handler.get("next_step") != "continue"
        or handler.get("action_type") != "THINK"
        or not latest.id
        or handler.get("action_id") != latest.id
    ):
        raise DeliveryError(
            "unfinished", "text is not the current native Central return"
        )
    extra = latest.additional_kwargs
    if extra.get("error_type"):
        raise DeliveryError("infrastructure_error", "native model returned an error")
    if latest.invalid_tool_calls:
        raise DeliveryError(
            "protocol_error", "native return contains invalid tool calls"
        )
    if (
        latest.tool_calls
        or extra.get("hide_from_ui")
        or (extra.get("stackplanner") or {}).get("status")
        in {"incomplete", "recovery_required", "action_capped", "blocked"}
    ):
        raise DeliveryError(
            "unfinished", "native return is pending, hidden, or explicitly incomplete"
        )
    if latest.response_metadata.get("finish_reason") in {
        "length",
        "content_filter",
        "tool_calls",
    }:
        raise DeliveryError(
            "unfinished", "native model did not complete its text response"
        )
    budget = state.get("sp_max_loop_iterations")
    if (
        isinstance(budget, int)
        and int(state.get("sp_loop_iteration") or 0) >= budget - 1
    ):
        raise DeliveryError(
            "unfinished",
            "native action budget reserved the final turn for incomplete status",
        )
    if (
        state.get("sp_last_final_artifact_ref")
        or SPFinishAvailabilityMiddleware._requires_artifact_delivery(state)
        or SPFinishAvailabilityMiddleware._incomplete_current_report(state) is not None
    ):
        raise DeliveryError(
            "unfinished", "native artifact delivery still requires accepted FINISH"
        )
    content = latest.content
    text = (
        content
        if isinstance(content, str)
        else "".join(
            block if isinstance(block, str) else block.get("text", "")
            for block in content
            if isinstance(block, str)
            or (
                isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            )
        )
    )
    if "DSML" in text:
        raise DeliveryError(
            "protocol_error", "native response contains unrecovered tool syntax"
        )
    if (
        not text.strip()
        or "\x00" in text
        or len(text.encode("utf-8")) > MAX_DELIVERY_BYTES
    ):
        raise DeliveryError(
            "final_parse_error", "native text delivery is empty, invalid, or oversized"
        )
    return text


def parse_answer(task: Task | PublicTask, text: str) -> list:
    if isinstance(task, Task):
        task = task.public_input()
    if task.benchmark == "widesearch":
        from dcc_harness.widesearch_metrics import extract_table

        table = extract_table(text, required_columns=task.metadata["answer_columns"])
        if table is None:
            raise ValueError("native final delivery contains no answer table")
        # Match the scorer's table parser; do not invent missing rows/columns.
        return table.astype(object).where(table.notna(), None).to_dict(orient="records")
    match = re.fullmatch(r"\s*```(?:json)?\s*([\s\S]*?)\s*```\s*", text)
    value = json.loads(match.group(1) if match else text)
    if not isinstance(value, list):
        raise ValueError("CUAD final answer must be a JSON array")
    for item in value:
        if isinstance(item, str) and item.strip():
            continue
        if (
            isinstance(item, dict)
            and isinstance(item.get("text"), str)
            and item["text"].strip()
            and isinstance(item.get("confidence"), (int, float))
            and not isinstance(item["confidence"], bool)
            and math.isfinite(item["confidence"])
            and 0 <= item["confidence"] <= 1
        ):
            continue
        raise ValueError("malformed CUAD span or confidence")
    return value


def score_task(task: Task, text: str, judge=None) -> dict[str, Any]:
    answer = parse_answer(task, text)
    if task.benchmark == "cuad":
        from dcc_harness.metrics import cuad_task_metrics

        return cuad_task_metrics(task.id, task.gold, answer)
    from dcc_harness.widesearch_metrics import evaluate_widesearch

    # Pass the original delivery through the existing parser exactly once in
    # scoring; parsed records are saved only for inspection (no row repair).
    return evaluate_widesearch(
        text, task.gold, task.metadata["evaluation"], judge=judge
    )


def summarize(rows: list[dict], *, expected_tasks: int) -> dict:
    """Compatibility name for inference diagnostics, never benchmark scores."""
    from .offline import inference_summary

    return inference_summary(rows, expected_tasks)


def retrieval_diagnostics(rows: list[dict]) -> dict:
    events = [e for row in rows for e in row.get("tool_events", [])]
    provider_errors = 0
    actual_backends = Counter()
    for event in events:
        observation = event.get("result") or {}
        if event.get("tool") == "web_search":
            if observation.get("backend"):
                actual_backends[observation["backend"]] += 1
            provider_errors += sum(
                "provider error" in reason
                or "challenge" in reason
                or "cooldown" in reason
                for reason in str(observation.get("fallback_reason") or "").split(";")
            )
    failures = sum(bool((e.get("result") or {}).get("error")) for e in events)
    return {
        "tool_calls": len(events),
        "tool_failures": failures,
        "tool_failure_rate": failures / len(events) if events else None,
        "provider_errors": provider_errors,
        "actual_search_backends": dict(actual_backends),
    }
