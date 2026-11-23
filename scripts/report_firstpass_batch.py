"""Fixed first-pass report conversations and token reports. No automatic retries."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def token_summary(report):
    records = report["usage"].get("model_call_records", {})
    receipts = {
        r["source_id"]: r
        for r in report.get("http_attempts", [])
        if r.get("kind") == "model" and r.get("source_id")
    }
    groups = {"all": []}
    for identity, row in records.items():
        usage = row.get("usage") if row.get("status") == "complete" else None
        source = receipts.get(identity, {})
        cache = (
            source.get("usage", {}).get("cache_read_tokens")
            if source.get("usage")
            else None
        )
        if cache is None and usage:
            cache = usage.get("cache_read_tokens")
        item = {
            "usage": usage,
            "cache": cache,
            "role": row["role"],
            "turn": row["turn"],
        }
        groups["all"].append(item)
        groups.setdefault("role:" + row["role"], []).append(item)
        groups.setdefault("turn:" + str(row["turn"]), []).append(item)

    def totals(rows):
        known = [r for r in rows if r["usage"] is not None]
        complete = len(known) == len(rows)
        values = {
            k: sum(r["usage"][k] for r in known)
            for k in ("input_tokens", "output_tokens", "total_tokens")
        }
        cache_missing = sum(r["cache"] is None for r in known)
        cache = sum(r["cache"] for r in known if r["cache"] is not None)
        return {
            "model_calls": len(rows),
            "usage_complete": complete,
            "missing_usage_calls": len(rows) - len(known),
            **{k: value if complete else None for k, value in values.items()},
            **{"observed_" + k: value for k, value in values.items()},
            "cache_read_tokens": cache if not cache_missing and complete else None,
            "observed_cache_read_tokens": cache,
            "cache_missing_calls": cache_missing,
            "cache_is_subset_of_input": True,
        }

    result = totals(groups["all"])
    result["by_role"] = {
        key[5:]: totals(value)
        for key, value in groups.items()
        if key.startswith("role:")
    }
    result["by_turn"] = {
        key[5:]: totals(value)
        for key, value in groups.items()
        if key.startswith("turn:")
    }
    return result


def firstpass_summary(rows, *, planned_per_case=6):
    return {
        "conversation_completed": sum(bool(r["completed"]) for r in rows),
        "conversation_denominator": len(rows),
        "clean_conversation_completed": sum(
            bool(r["completed"])
            and r["tool_errors"] == 0
            and r.get("subagent_guard_stops", 0) == 0
            and r.get("tool_trace_complete", True)
            for r in rows
        ),
        "planned_turns": planned_per_case * len(rows),
        "attempted_turns": sum(r["attempted"] for r in rows),
        "successful_turns": sum(r["succeeded"] for r in rows),
        "not_attempted_turns": planned_per_case * len(rows)
        - sum(r["attempted"] for r in rows),
    }


def summarize_case(path, case):
    path = Path(path)
    result = (
        json.loads((path / "result.json").read_text())
        if (path / "result.json").exists()
        else {}
    )
    usage = (
        json.loads((path / "usage.json").read_text())
        if (path / "usage.json").exists()
        else None
    )
    turns = [json.loads(p.read_text()) for p in sorted(path.glob("turn-*.json"))]
    events = (
        [
            json.loads(line)
            for line in (path / "numeric-events.jsonl").read_text().splitlines()
            if line.strip()
        ]
        if (path / "numeric-events.jsonl").exists()
        else []
    )
    attempted = sorted({r["turn"] for r in events if r.get("event") == "turn_started"})
    errors = (
        [
            row
            for row in usage["usage"].get("tool_events", [])
            if row.get("type") == "tool" and row.get("failed")
        ]
        if usage
        else []
    )
    stops = (
        [
            row
            for row in usage["usage"].get("tool_events", [])
            if row.get("type") == "execution_end"
            and (row.get("stop_reason") is not None or row.get("status") != "completed")
        ]
        if usage
        else []
    )
    tokens = token_summary(usage) if usage else None
    stop_reason = usage.get("stop_reason") if usage else None
    if not result.get("completed"):
        binding = (
            json.loads((path / "runtime-binding.json").read_text())
            if (path / "runtime-binding.json").exists()
            else None
        )
        if binding:
            sidecar = Path(binding["outputs"]) / "report_dialogue/turns"
            for record in sorted(sidecar.glob("*/turn.json")):
                item = json.loads(record.read_text())
                if item.get("status") == "failed":
                    stop_reason = (
                        (item.get("diagnostic") or {}).get("stop_reason")
                        or stop_reason
                        or "resolution_failure"
                    )
            if stop_reason is None:
                from deerflow.sp.report.store import pointer_path

                active = pointer_path(Path(binding["outputs"]))
                if active.exists():
                    pointer = json.loads(active.read_text())
                    ledger = (
                        Path(binding["outputs"])
                        / "report"
                        / pointer["report_id"]
                        / "ledger.json"
                    )
                    if ledger.exists():
                        stop_reason = (
                            json.loads(ledger.read_text()).get("acceptance") or {}
                        ).get("status")
        stop_reason = (
            stop_reason or result.get("error_type") or "functional_check_failed"
        )
    return {
        "case_id": case["case_id"],
        "topic": case["topic"],
        "style": case["style"],
        "directory": str(path),
        "completed": bool(result.get("completed")),
        "attempted": len(attempted),
        "attempted_user_turns": attempted,
        "succeeded": sum(bool(t["passed"]) for t in turns),
        "tool_errors": len(errors),
        "subagent_guard_stops": len(stops),
        "subagent_stop_reasons": [
            row.get("stop_reason") or row.get("status") for row in stops
        ],
        "tool_trace_complete": bool(
            usage is not None
            and "tool_events" in usage["usage"]
            and not usage["usage"].get("observation_errors")
        ),
        "tool_error_codes": dict(
            __import__("collections").Counter(
                r.get("error_code") or "unknown" for r in errors
            )
        ),
        "tool_observation_errors": usage["usage"].get("observation_errors", [])
        if usage
        else ["usage_missing"],
        "stop_reason": stop_reason,
        "turns": turns,
        "tokens": tokens,
        "trial_billing_complete": usage["usage"]["billing_complete"]
        if usage
        else False,
        "known_cumulative_peak_cny": usage["budget"]["spent_cost_cny"]
        if usage
        else None,
        "held_cny": usage["budget"]["reserved_cost_cny"] if usage else None,
    }


def export_reports(destination, rows, planned_cases):
    destination = Path(destination)
    metrics = firstpass_summary(rows)
    metrics["planned_conversations"] = planned_cases
    write(
        destination / "summary.json",
        {
            "metrics": metrics,
            "cases": rows,
            "guard_false_positive_rate": "not inferred automatically; tool rejection is not necessarily a false positive",
            "native_internal_retries_preserved": True,
            "outer_restarts": 0,
        },
    )
    with (destination / "tokens.csv").open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "case_id",
                "topic",
                "style",
                "level",
                "key",
                "model_calls",
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "cache_read_tokens",
                "observed_total_tokens",
                "usage_complete",
                "cache_missing_calls",
            ],
        )
        writer.writeheader()
        for row in rows:
            if not row["tokens"]:
                continue
            for level, parts in [
                ("case", {"all": row["tokens"]}),
                ("role", row["tokens"]["by_role"]),
                ("turn", row["tokens"]["by_turn"]),
            ]:
                for key, t in parts.items():
                    writer.writerow(
                        {
                            **{k: row[k] for k in ("case_id", "topic", "style")},
                            "level": level,
                            "key": key,
                            **{k: t.get(k) for k in writer.fieldnames if k in t},
                        }
                    )
    lines = [
        "# 固定代码首次完成率与Token记录",
        "",
        f"已执行会话 {len(rows)}/{planned_cases}；完整完成 {metrics['conversation_completed']}/{len(rows)}；无可见工具错误且完成 {metrics['clean_conversation_completed']}/{len(rows)}。",
        f"后续请求计划 {metrics['planned_turns']}，实际尝试 {metrics['attempted_turns']}，通过 {metrics['successful_turns']}，因前置失败未执行 {metrics['not_attempted_turns']}。",
        "",
        "每个会话一次启动，原生内部纠正与SDK重试保留；外层不重试，不在批次中改运行代码。",
        "",
        "| case | 完成 | 尝试/通过 | 输入(万) | 输出(万) | 总量(万) | 缓存读(万，已含输入) | 工具错误 | 停止原因 |",
        "|---|---|---|---:|---:|---:|---:|---:|---|",
    ]

    def w(value):
        return "未知" if value is None else f"{value / 10000:.4f}"

    for row in rows:
        t = row["tokens"] or {}
        lines.append(
            f"|{row['case_id']}|{row['completed']}|{row['attempted']}/{row['succeeded']}|{w(t.get('input_tokens'))}|{w(t.get('output_tokens'))}|{w(t.get('total_tokens'))}|{w(t.get('cache_read_tokens'))}|{row['tool_errors']}|{row['stop_reason'] or ''}|"
        )
    lines += [
        "",
        "完整数值与按角色/轮次拆分见 tokens.csv / summary.json。缓存不重复相加，失败消耗保留，缺usage不是0。",
        "工具拒绝原因与原生验收停止需逐项判断，不将所有拦截自动视为误拦。",
        "主题数与会话数均很小，不据此宣称通用可靠率。",
    ]
    (destination / "REPORT.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--start", action="store_true")
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    root = Path(__file__).resolve().parents[1]
    for relative, digest in plan["frozen_sha256"].items():
        if hashlib.sha256((root / relative).read_bytes()).hexdigest() != digest:
            raise ValueError("frozen batch checksum changed")
    if not args.start:
        print(
            json.dumps(
                {
                    "cases": len(plan["cases"]),
                    "planned_followups": len(plan["cases"]) * 6,
                    "provider_calls": 0,
                    "ready_for_explicit_start": True,
                }
            )
        )
        return
    args.output.mkdir(parents=True, exist_ok=False)
    write(args.output / "plan.json", plan)
    prior = root / plan["prior_usage"]
    rows = []
    for case in plan["cases"]:
        for relative, digest in plan["frozen_sha256"].items():
            if hashlib.sha256((root / relative).read_bytes()).hexdigest() != digest:
                raise ValueError("code/data changed during fixed batch")
        target = args.output / case["case_id"]
        command = [
            sys.executable,
            str(root / "scripts/report_pilot_entry.py"),
            "--packet",
            str(root / case["packet"]),
            "--env-file",
            str(args.env_file.resolve()),
            "--run-dir",
            str(target.resolve()),
            "--prefix-source",
            str(root / case["prepared_outputs"]),
            "--prefix-through",
            "1",
            "--prior-usage",
            str(prior),
            "--carry-unknown-exposure",
            "--experiment-limits",
            str(root / plan["experiment_limits"]),
            "--evaluator-checks",
            str(root / case["checks"]),
            "--start",
            "--billing-currency",
            "CNY",
        ]
        began = time.monotonic()
        with (args.output / (case["case_id"] + ".log")).open("w") as log:
            process = subprocess.run(
                command,
                cwd=root,
                stdout=log,
                stderr=log,
                env={
                    **os.environ,
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHON_DOTENV_DISABLED": "1",
                    "LANGSMITH_TRACING": "false",
                    "LANGCHAIN_TRACING_V2": "false",
                },
            )
        row = summarize_case(target, case)
        row.update(
            process_exit_code=process.returncode,
            wall_seconds=round(time.monotonic() - began, 3),
        )
        rows.append(row)
        export_reports(args.output, rows, len(plan["cases"]))
        print(
            json.dumps(
                {
                    "case_id": case["case_id"],
                    "completed": row["completed"],
                    "attempted": row["attempted"],
                    "succeeded": row["succeeded"],
                    "stop_reason": row["stop_reason"],
                    "total_tokens": (row["tokens"] or {}).get("total_tokens"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if not (target / "usage.json").exists():
            break  # no defensible cumulative wallet
        prior = target / "usage.json"
    export_reports(args.output, rows, len(plan["cases"]))


if __name__ == "__main__":
    main()
