"""Native SP delivery extraction and independent benchmark scoring.

This module never chooses a specialist's answer on behalf of Central and owns
no agent loop. The graph's accepted FINISH is the only delivery authority.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from dcc_harness.data import Task

PROTOCOL = "sp-native-benchmarks-v2"
MAX_DELIVERY_BYTES = 16 * 1024 * 1024


class DeliveryError(ValueError):
    def __init__(self, status: str, detail: str):
        self.status = status
        super().__init__(f"{status}: {detail}")


def extract_delivery(state: dict, *, thread_id: str, run_id: str) -> tuple[str, dict | None]:
    if state.get("sp_pending_human_interaction"):
        raise DeliveryError("ask_human", "native SP requests human input")
    if state.get("sp_current_stage") != "finished" or (state.get("sp_last_handler_result") or {}).get("next_step") != "finish":
        raise DeliveryError("unfinished", "native SP did not accept FINISH")
    selected = state.get("sp_last_final_artifact_ref")
    if not selected:
        text = str(state.get("sp_last_run_summary") or "").strip()
        if not text:
            raise DeliveryError("final_parse_error", "accepted text delivery is empty")
        return text, None
    refs = state.get("sp_current_artifact_refs") or {}
    candidates = [r for key, r in refs.items() if key != "_history" and isinstance(r, dict)]
    candidates.extend(r for r in refs.get("_history", []) if isinstance(r, dict))
    ref = next((r for r in candidates if selected in (r.get("artifact_id"), r.get("artifact_url"), r.get("virtual_path"))), None)
    if ref is None:
        raise DeliveryError("final_parse_error", "selected artifact is not registered")
    if ref.get("thread_id") != thread_id or ref.get("run_id") != run_id or ref.get("is_current") is False:
        raise DeliveryError("final_parse_error", "selected artifact is outside the current thread/run")
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
        raise DeliveryError("final_parse_error", "unsafe or unavailable native artifact path")
    try:
        if resolved.stat().st_size > MAX_DELIVERY_BYTES:
            raise DeliveryError("final_parse_error", "delivery exceeds 16 MiB; refusing truncation")
        before = resolved.stat()
        with resolved.open("rb") as handle:
            raw = handle.read(MAX_DELIVERY_BYTES + 1)
        after = resolved.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise DeliveryError("final_parse_error", "artifact changed during delivery extraction")
        if len(raw) > MAX_DELIVERY_BYTES or b"\x00" in raw:
            raise DeliveryError("final_parse_error", "invalid or oversized text artifact")
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise DeliveryError("final_parse_error", "selected native artifact cannot be read") from exc
    observed_hash = hashlib.sha256(raw).hexdigest()
    if ref.get("content_hash") and observed_hash != ref["content_hash"]:
        raise DeliveryError("final_parse_error", "selected artifact integrity check failed")
    selected_ref = dict(ref)
    if not ref.get("content_hash"):
        metadata = ref.get("metadata") or {}
        if not metadata.get("registered_existing_file"):
            raise DeliveryError("final_parse_error", "native artifact lacks integrity metadata")
        preview = metadata.get("finalization_preview")
        if preview is not None and not metadata.get("finalization_preview_truncated") and text != preview:
            raise DeliveryError("final_parse_error", "artifact differs from native finalization preview")
        # SP registers native write_file outputs without an expected hash. Do
        # not manufacture one: retain this as an observed snapshot explicitly.
        selected_ref.update(observed_sha256=observed_hash, integrity="native_existing_file_snapshot")
    return text, selected_ref


def parse_answer(task: Task, text: str) -> list:
    if task.benchmark == "widesearch":
        from dcc_harness.widesearch_metrics import extract_table

        table = extract_table(text, required_columns=task.metadata["evaluation"]["required"])
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
        if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"].strip() and isinstance(item.get("confidence"), (int, float)) and not isinstance(item["confidence"], bool) and math.isfinite(item["confidence"]) and 0 <= item["confidence"] <= 1:
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
    return evaluate_widesearch(text, task.gold, task.metadata["evaluation"], judge=judge)


def summarize(rows: list[dict], *, expected_tasks: int) -> dict:
    counts = Counter(row["status"] for row in rows)
    result = {
        "protocol": PROTOCOL,
        "tasks": expected_tasks,
        "predictions": len(rows),
        "prediction_coverage": len(rows) / expected_tasks if expected_tasks else 0.0,
        "completion_rate": sum(r["status"] in {"completed", "judge_error"} for r in rows) / expected_tasks if expected_tasks else 0.0,
        "status_counts": dict(counts),
    }
    events = [e for row in rows for e in row.get("tool_events", [])]
    provider_errors = 0
    actual_backends = Counter()
    for event in events:
        observation = event.get("result") or {}
        if event.get("tool") == "web_search":
            if observation.get("backend"):
                actual_backends[observation["backend"]] += 1
            provider_errors += sum("provider error" in reason or "challenge" in reason or "cooldown" in reason for reason in str(observation.get("fallback_reason") or "").split(";"))
    failures = sum(bool((e.get("result") or {}).get("error")) for e in events)
    result["retrieval"] = {"tool_calls": len(events), "tool_failures": failures, "tool_failure_rate": failures / len(events) if events else None, "provider_errors": provider_errors, "actual_search_backends": dict(actual_backends)}
    for benchmark in sorted({row["benchmark"] for row in rows}):
        selected = [r for r in rows if r["benchmark"] == benchmark]
        if not selected:
            continue
        scored = [r for r in selected if r["status"] != "judge_error"]
        keys = [f"{metric}_by_{unit}" for unit in ("row", "item") for metric in ("precision", "recall", "f1")] + ["score"] if benchmark == "widesearch" else ["f1", "precision", "recall"] if benchmark == "cuad" else sorted({key for row in selected for key, value in row.get("metrics", {}).items() if isinstance(value, (int, float)) and not isinstance(value, bool)})
        result[benchmark] = {key: sum(r.get("metrics", {}).get(key, 0.0) for r in scored) / len(scored) if scored else None for key in keys}
        result[benchmark].update(tasks=len(selected), metric_denominator=len(scored), judge_failures=len(selected) - len(scored), evaluation_protocol=next((r.get("metrics", {}).get("evaluation_protocol") for r in selected if r.get("metrics")), None))
    return result
