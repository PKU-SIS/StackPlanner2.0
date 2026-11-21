"""Isolated node-count calibration. Never edit the stable SP2 implementation."""

from __future__ import annotations

import argparse
import ast
from copy import deepcopy
from dataclasses import replace
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

OUT = Path(__file__).resolve().parent
ROOT = OUT.parent.parent
TIMEOUT = 2100
PROTOCOL_VERSION = "auto-outline-body4000-arguments4-max6"


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temp.replace(path)


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def cells():
    return [
        {
            "name": f"case{case_id}-auto-n-body4000-cards1",
            "case_id": case_id,
            "task_cards": True,
            "body_target_words": 4000,
            "body_max_words": 4000,
            "argument_target": 4,
            "argument_max": 6,
            "contract": {
                "min_words": 1,
                "target_words": 100000,
                "max_words": 100000,
                "compact_outline": True,
                "writer_task_cards": True,
                "length_auto_revision": False,
            },
        }
        for case_id in [31, 11, 25]
    ]


def cell_directory(cell):
    return OUT / "cells" / cell["name"] / "native" / str(cell["case_id"])


def score_inputs_id(cell, query):
    if query.get("id") != cell["case_id"]:
        raise ValueError("query_case_id_mismatch")
    return cell["case_id"]


def prompt_constant(name):
    source = ROOT / "backend/packages/harness/deerflow/sp/report/prompts.py"
    for stmt in ast.parse(source.read_text()).body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in stmt.targets
        ):
            return ast.literal_eval(stmt.value.func.value).strip()
    raise ValueError("missing_prompt_constant")


def outline_instruction(cell, original=None):
    text = original or prompt_constant("REPORT_COMPACT_OUTLINE_PROMPT")
    old = "Normally aim for 6-8 chapters with 1-2 concise core arguments each."
    assert old in text
    text = text.replace(
        old,
        "Choose the number of writable nodes yourself from the original user requirements "
        "and the total body generation budget of 4000 words. There is no fixed chapter count. "
        "Aim for 4 arguments per writable node, with at most 6 entries in argument[]. "
        "This applies to initial planning and post-research consolidation. Three or four "
        "arguments are normally sufficient; fewer are allowed. Do not pad to reach a count. "
        "Use a fifth or sixth argument only for necessary coverage. Each entry develops "
        "one primary judgment with essential support; do not bundle independent tasks or "
        "several stand-alone case studies into an entry to evade the cap. Do not hide excess "
        "tasks in question, conclusion, constraints or internal headings. Preserve every "
        "explicit user requirement and requested depth. Reduce optional detail first; "
        "report a genuine conflict if the controls are infeasible. After supplementary "
        "research strengthen selected evidence without expanding unrequested tasks.",
    )
    old = (
        "Their sums must fit the supplied body target/maximum, including the host's\n"
        "reference and heading reserves."
    )
    assert old in text
    text = text.replace(
        old,
        "Allocate the 4000-word body budget across writable nodes according to importance. "
        "The sum of all node target_words and the sum of all node max_words must each "
        "be at most 4000. Titles and final references are counted separately from this "
        "internal body budget. The broad host envelope is not a writing target.",
    )
    return text + (
        "\nAUTONOMOUS OUTLINE: this is an internal first-draft generation budget, "
        "not a claim that the report will actually measure 4000 words. Writer receives "
        "your per-node allocations through the normal task cards. Keep research, evidence "
        "and native content review intact. Do not truncate prose or add a length-rewrite loop."
    )


def model_length_view(original, cell):
    return {
        "body_target_words": cell["body_target_words"],
        "body_max_words": cell["body_max_words"],
        "argument_target": cell["argument_target"],
        "argument_max": cell["argument_max"],
        "budget_scope": "body_generation_only; headings and final references measured separately",
        "measurement_mode": "estimate",
        "counter_version": "mixed-word-estimate-v1",
        **{k: deepcopy(original[k]) for k in ["node", "generation"] if k in original},
    }


def control_audit(tree, cell):
    nodes = [n for n in (tree or {}).get("nodes", []) if n.get("kind") == "writable"]
    violations = []
    target_sum = maximum_sum = 0
    for node in nodes:
        c = node.get("constraints") or {}
        target, maximum = c.get("target_words"), c.get("max_words")
        if (
            type(target) is not int
            or type(maximum) is not int
            or not 0 < target <= maximum
        ):
            violations.append({"node_id": node.get("node_id"), "constraints": c})
        else:
            target_sum += target
            maximum_sum += maximum
    counts = {n.get("node_id"): len(n.get("argument") or []) for n in nodes}
    argument_violations = [
        {"node_id": key, "count": count, "maximum": cell["argument_max"]}
        for key, count in counts.items()
        if count > cell["argument_max"]
    ]
    valid = bool(nodes) and not violations and not argument_violations
    valid = (
        valid
        and target_sum <= cell["body_target_words"]
        and maximum_sum <= cell["body_max_words"]
    )
    return {
        "node_count_policy": "autonomous",
        "actual_writable_nodes": len(nodes),
        "node_target_sum": target_sum,
        "node_max_sum": maximum_sum,
        "body_target_words": cell["body_target_words"],
        "body_max_words": cell["body_max_words"],
        "node_budget_violations": violations,
        "argument_target": cell["argument_target"],
        "argument_max": cell["argument_max"],
        "argument_total": sum(counts.values()),
        "argument_counts": counts,
        "argument_count_violations": argument_violations,
        "valid": valid,
    }


def normalize_judgments(parsed, criteria):
    result, changes = deepcopy(parsed), []

    def key(s):
        return s.strip().translate(
            str.maketrans(
                {"“": None, "”": None, "‘": None, "’": None, '"': None, "'": None}
            )
        )

    for dim, expected in criteria.items():
        items = result.get(dim, [])
        labels = {key(i["criterion"]): i["criterion"] for i in expected}
        if len(labels) != len(expected) or len(items) != len(expected):
            raise ValueError("criterion_count_mismatch")
        for item in items:
            original = item["criterion"]
            if key(original) not in labels:
                raise ValueError("criterion_label_mismatch")
            item["criterion"] = labels[key(original)]
            if original != item["criterion"]:
                changes.append(
                    {
                        "dimension": dim,
                        "original": original,
                        "canonical": item["criterion"],
                    }
                )
            for field in ["article_1_score", "article_2_score"]:
                if not 0 <= float(item[field]) <= 10:
                    raise ValueError("invalid_score")
        if len({i["criterion"] for i in items}) != len(expected):
            raise ValueError("duplicate_criterion")
    return result, changes


def generation_usage(state, events):
    central = sum(
        (msg.get("usage_metadata") or {}).get("total_tokens", 0)
        for msg in state.get("messages", [])
        if isinstance(msg, dict)
    )
    completed = {}
    for event in events:
        data = event.get("data", {})
        if (
            event.get("mode") == "custom"
            and isinstance(data, dict)
            and data.get("type") == "task_completed"
            and data.get("usage")
        ):
            completed[data.get("task_id") or data.get("a2a_task_id")] = data["usage"]
    specialist = sum(u.get("total_tokens", 0) for u in completed.values())
    return {
        "central_tokens": central,
        "subagent_tokens": specialist,
        "total_tokens": central + specialist,
        "completed_subagent_tasks": len(completed),
        "scope": "central message usage plus deduplicated task_completed usage; includes cached input",
        "billing_cost": False,
    }


def install_overlay(cell, directory):
    """Process-local prompt/packet overlay; native validators are unchanged."""
    from deerflow.sp.report import hooks, packets, prompts

    prompts.REPORT_COMPACT_OUTLINE_PROMPT = outline_instruction(
        cell, prompts.REPORT_COMPACT_OUTLINE_PROMPT
    )
    save(
        directory / "experimental-outline-prompt.json",
        {"prompt": prompts.REPORT_COMPACT_OUTLINE_PROMPT},
    )
    for name in [
        "build_outline_packet",
        "build_writer_packet",
        "build_integrate_review_packet",
    ]:
        original = getattr(packets, name)

        def projected(ledger, _original=original, _name=name, **kwargs):
            if _name == "build_writer_packet":
                audit = control_audit(packets.load_current_tree(ledger), cell)
                save(directory / "pre-writer-control-audit.json", audit)
                if not audit["valid"]:
                    save(directory / "control-violation.json", audit)
                    raise SystemExit("experimental_node_control_mismatch_no_retry")
            result = _original(ledger, **kwargs)
            if "length" in result:
                result["length"] = model_length_view(result["length"] or {}, cell)
            return result

        setattr(packets, name, projected)
    original_configure = hooks.configure_report_subagent

    def configure(*args, **kwargs):
        config, tools, role = original_configure(*args, **kwargs)
        # This skill asks Outline to divide a whole-report target and has a
        # 6–10-node default: contradictory to an independent node-count test.
        # Native report-outline / report-node-writing skills are kept.
        if role in {"outline", "writer"}:
            config = replace(
                config,
                skills=[s for s in config.skills or [] if s != "report-word-budget"],
            )
        return config, tools, role

    hooks.configure_report_subagent = configure
    save(
        directory / "overlay.json",
        {
            "installed_at": now(),
            "stable_files_modified": False,
            "global_model_budget_hidden": True,
            "word_budget_skill_omitted": True,
            "native_validators_changed": False,
            "spec": cell,
        },
    )


def prepare():
    path = OUT / "manifest.json"
    if path.exists():
        return json.loads(path.read_text())
    env = dict(
        os.environ,
        PYTHONPATH=str(ROOT / "backend") + ":" + str(ROOT / "backend/packages/harness"),
    )
    for cell in cells():
        target = OUT / "cells" / cell["name"] / "native"
        target.parent.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            str(ROOT / "backend/scripts/evaluate_report_full.py"),
            "--ids",
            str(cell["case_id"]),
            "--output",
            str(target),
            "--not-before",
            "2026-11-11T00:00:00+00:00",
            "--word-length",
            "1",
            "100000",
            "100000",
            "--compact-outline",
            "--no-length-revision",
        ]
        if cell["task_cards"]:
            command.append("--writer-task-cards")
        subprocess.run(command, cwd=ROOT, env=env, check=True)
        save(target.parent / "spec.json", cell)
    files = [
        ROOT / "backend/packages/harness/deerflow/sp/report/prompts.py",
        ROOT / "backend/packages/harness/deerflow/sp/report/task_card.py",
        ROOT / "backend/packages/harness/deerflow/sp/report/packets.py",
        ROOT / "backend/packages/harness/deerflow/sp/subagents/dr2_adapter.py",
    ]
    baseline_hashes = {
        str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in files
    }
    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "prepared_at": now(),
        "case_ids": [c["case_id"] for c in cells()],
        "cells": cells(),
        "stable_source_hashes": baseline_hashes,
        "prior_reports_reused_as_experimental_observations": False,
        "design": "three original baseline tasks, same 4000-word total body generation budget, arguments target4 max6",
        "host_contract_is_not_output_target": True,
        "measurement": "mixed-word-estimate-v1, not measured Microsoft Word",
        "stage_workflow": "native initial research, Outline, supplementary research, consolidation, Writer, content review",
        "stop_policy": "stop queue on control mismatch, no progress, timeout, missing candidate or content failure; no auto restart",
    }
    save(path, manifest)
    return manifest


def worker(name):
    cell = next(c for c in cells() if c["name"] == name)
    cell_dir = OUT / "cells" / name
    directory = cell_directory(cell)
    frozen = cell_dir / "native/code"
    os.environ.update(
        DEER_FLOW_PROJECT_ROOT=str(ROOT), DEER_FLOW_HOME=str(directory / "runtime")
    )
    sys.path[:0] = [
        str(frozen / "backend/scripts"),
        str(frozen / "backend"),
        str(frozen / "backend/packages/harness"),
    ]
    import evaluate_report_full as native

    native.ROOT = ROOT
    install_overlay(cell, directory)
    native.worker(directory, 0, TIMEOUT)


def measure(cell):
    directory = cell_directory(cell)
    ledgers = list((directory / "runtime").rglob("report/r1/ledger.json"))
    if not ledgers:
        return {"name": cell["name"], "valid": False, "reason": "no_ledger"}
    ledger = json.loads(ledgers[0].read_text())
    tree_record = ledger.get("tree") or {}
    tree = (
        json.loads(Path(tree_record["path"]).read_text())
        if tree_record.get("path")
        else {}
    )
    audit = control_audit(tree, cell)
    report = ledger.get("candidate") or {}
    text = (
        Path(report["path"]).read_text()
        if report.get("path") and Path(report["path"]).exists()
        else ""
    )
    from deerflow.sp.report.citation_display import render_final_citations
    from deerflow.sp.report.length import estimate_words

    rendered, _ = (
        render_final_citations(text, report.get("source_table") or [])
        if text
        else ("", {})
    )
    if rendered:
        (directory / "evaluated-report.md").write_text(rendered)
    review = ledger.get("review") or {}
    blocking = [
        f for f in review.get("findings", []) if f.get("severity") == "blocking"
    ]
    state_path = directory / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    event_path = directory / "events.jsonl"
    events = (
        [
            json.loads(line)
            for line in event_path.read_text().splitlines()
            if line.strip()
        ]
        if event_path.exists()
        else []
    )
    row = {
        "name": cell["name"],
        "case_id": cell["case_id"],
        "target_whole_words": 10000,
        "target_tolerance_words": 300,
        "word_deviation": estimate_words(rendered) - 10000 if rendered else None,
        "within_target_band": bool(rendered)
        and abs(estimate_words(rendered) - 10000) <= 300,
        "requested_nodes": None,
        "task_cards": cell["task_cards"],
        "control": audit,
        "words": estimate_words(rendered) if rendered else None,
        "report_sha256": hashlib.sha256(rendered.encode()).hexdigest()
        if rendered
        else None,
        "delivered": bool(ledger.get("delivered")),
        "blocking_findings": blocking,
        "generation_usage": generation_usage(state, events),
        "valid": audit["valid"]
        and bool(rendered)
        and bool(ledger.get("delivered"))
        and not blocking,
    }
    save(directory / "observation.json", row)
    return row


def race_score(cell):
    """Same pinned DRB/DeepSeek protocol as the completed paired evaluation."""
    prior = ROOT / "artifacts/drbench-paired-20261114"
    spec = importlib.util.spec_from_file_location(
        "paired_race_transport", prior / "run.py"
    )
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    target = OUT / "race" / cell["name"]
    target.mkdir(parents=True, exist_ok=True)
    api.OUT = target
    query = json.loads((cell_directory(cell) / "query.json").read_text())
    text = (cell_directory(cell) / "evaluated-report.md").read_text()
    case_id = score_inputs_id(cell, query)
    raw = {"id": case_id, "prompt": query["prompt"], "article": text}
    client = api.DeepSeekClient()
    cleaned_file = target / "cleaned.json"
    if cleaned_file.exists():
        cleaned = json.loads(cleaned_file.read_text())
    else:
        cleaned = api.ArticleCleaner(client).clean_single(raw, language="zh")
        if not cleaned or cleaned.get("error"):
            raise RuntimeError("race_cleaning_failed")
        save(cleaned_file, cleaned)
    criteria = next(
        r
        for r in api.jsonl(prior / "upstream/data/criteria_data/criteria.jsonl")
        if r["id"] == case_id
    )
    reference = next(
        r
        for r in api.jsonl(
            prior / "upstream/data/test_data/cleaned_data/reference.jsonl"
        )
        if r["id"] == case_id
    )
    prompt = api.generate_merged_score_prompt.format(
        task_prompt=query["prompt"],
        article_1=cleaned["article"],
        article_2=reference["article"],
        criteria_list=api.format_criteria_list(criteria),
    )
    parsed = json.loads(api.extract_json_from_markdown(client.generate(prompt)))
    parsed, changes = normalize_judgments(parsed, criteria["criterions"])
    weighted = api.calculate_weighted_scores(parsed, criteria, "zh")

    def ratio(a, b):
        return a / (a + b) if a + b > 0 else 0

    scores = {
        dim: ratio(
            weighted["target"]["dims"][dim + "_weighted_avg"],
            weighted["reference"]["dims"][dim + "_weighted_avg"],
        )
        for dim in api.DIMS
    }
    scores["overall_score"] = ratio(
        weighted["target"]["total"], weighted["reference"]["total"]
    )
    save(
        target / "result.json",
        {
            "scores": scores,
            "criterion_judgments": parsed,
            "label_normalizations": changes,
            "weighted": weighted,
            "judge": api.MODEL,
            "official_leaderboard_comparable": False,
            "report_sha256": hashlib.sha256(text.encode()).hexdigest(),
        },
    )
    return scores


def target_verdict(rows):
    expected = {31, 11, 25}
    complete = len(rows) == 3 and {r.get("case_id") for r in rows} == expected
    within = complete and all(abs((r.get("words") or 0) - 10000) <= 300 for r in rows)
    generated = complete and all(r.get("valid") for r in rows)
    scored = complete and all(r.get("race_scores") for r in rows)
    return {
        "target_whole_words": 10000,
        "tolerance_words": 300,
        "complete": complete,
        "all_in_word_band": within,
        "all_generation_valid": generated,
        "fully_scored": scored,
        "initial_cross_task_pass": bool(within and generated and scored),
        "repeated_run_stability_verified": False,
        "per_task": [
            {
                "case_id": r.get("case_id"),
                "words": r.get("words"),
                "deviation": (r.get("words") or 0) - 10000,
            }
            for r in rows
        ],
    }


def write_observations(rows):
    save(OUT / "observations.json", rows)
    save(OUT / "target-band-summary.json", target_verdict(rows))
    lines = [
        "# 写作节点数与篇幅：探索性标定",
        "",
        "同题、正文生成总预算4000字、Outline自主选节点和分配预算；实际篇幅为本地混合字数估计。",
        "全文宽范围仅供宿主兼容验收，不是模型输出目标。没有事后压缩或硬截断。",
        "",
        "| 条件 | 任务卡 | 实际节点 | 全文字数 | 生成 Token | RACE ×100 | 条件有效 |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        score = row.get("race_scores", {}).get("overall_score")
        display = f"{score * 100:.2f}" if score is not None else "待评分"
        lines.append(
            f"| {row['name']} | {row.get('task_cards')} | "
            f"{row.get('control', {}).get('actual_writable_nodes', '—')} | "
            f"{row.get('words', '—')} | {row.get('generation_usage', {}).get('total_tokens', '—')} | "
            f"{display} | {row['valid']} |"
        )
    lines.extend(
        [
            "",
            "三个原baseline题目各一次；未控制研究检索的随机差异，不能据此声称普适线性或任务卡的稳定因果收益。",
            "RACE 使用与此前配对评测相同的固定官方流程和 DeepSeek 评委，不是官方榜单成绩；FACT 未运行。",
            "生成 Token 包含中央和子任务、缓存输入；评测 Token 单独保存在 race/，不是实际账单金额。",
        ]
    )
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n")


def has_existing_attempt(directory):
    return any(
        (directory / name).exists()
        for name in ["attempt-started.json", "checkpoints.sqlite", "events.jsonl"]
    )


def reconcile_status():
    path = OUT / "status.json"
    if not path.exists():
        return {"state": "not_started"}
    value = json.loads(path.read_text())
    if value.get("state") not in {"running", "scoring"}:
        return value
    with (OUT / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return value
        value.update(
            state="interrupted_unknown_cause",
            updated_at=now(),
            reason="supervisor lock is no longer held; exit cause not established",
            automatic_retry=False,
        )
        save(path, value)
    return value


def select_cells(all_cells, name=None):
    selected = [c for c in all_cells if name is None or c["name"] == name]
    if not selected:
        raise ValueError("unknown_cell")
    return selected


def supervise(only_cell=None):
    manifest = prepare()
    selected = select_cells(manifest["cells"], only_cell)
    lock = (OUT / "run.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    observations = []
    for cell in selected:
        directory = cell_directory(cell)
        if (directory / "observation.json").exists():
            row = json.loads((directory / "observation.json").read_text())
        else:
            if has_existing_attempt(directory):
                save(
                    OUT / "status.json",
                    {
                        "state": "existing_attempt_requires_recovery",
                        "active_cell": cell["name"],
                        "updated_at": now(),
                        "automatic_retry": False,
                        "reason": "Existing checkpoint/events cannot be restarted with a new query.",
                    },
                )
                return
            save(
                OUT / "status.json",
                {
                    "state": "running",
                    "active_cell": cell["name"],
                    "updated_at": now(),
                    "completed_cells": len(observations),
                    "total_cells": len(selected),
                },
            )
            command = [
                sys.executable,
                "-u",
                str(Path(__file__)),
                "--worker",
                cell["name"],
            ]
            with (directory / "worker.log").open("a") as log:
                save(
                    directory / "attempt-started.json",
                    {"started_at": now(), "command": command},
                )
                child = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
                start = progress = time.monotonic()
                heartbeat = 0
                stamp = None
                reason = None
                try:
                    while child.poll() is None:
                        events = directory / "events.jsonl"
                        if events.exists():
                            st = events.stat()
                            new = (st.st_mtime_ns, st.st_size)
                            if new != stamp:
                                stamp, progress = new, time.monotonic()
                        if (directory / "control-violation.json").exists():
                            reason = "node_control_mismatch"
                        elif time.monotonic() - start > TIMEOUT + 90:
                            reason = "hard_timeout"
                        elif time.monotonic() - progress > 300:
                            reason = "no_progress_timeout"
                        if reason:
                            break
                        if time.monotonic() - heartbeat >= 15:
                            save(
                                OUT / "status.json",
                                {
                                    "state": "running",
                                    "active_cell": cell["name"],
                                    "updated_at": now(),
                                    "completed_cells": len(observations),
                                    "total_cells": len(selected),
                                    "elapsed_seconds": round(
                                        time.monotonic() - start, 1
                                    ),
                                    "seconds_since_event": round(
                                        time.monotonic() - progress, 1
                                    ),
                                },
                            )
                            heartbeat = time.monotonic()
                        time.sleep(2)
                finally:
                    if child.poll() is None:
                        os.killpg(child.pid, signal.SIGTERM)
                        try:
                            child.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            os.killpg(child.pid, signal.SIGKILL)
                            child.wait()
                save(
                    directory / "process-result.json",
                    {
                        "returncode": child.returncode,
                        "stop_reason": reason,
                        "elapsed_seconds": round(time.monotonic() - start, 2),
                    },
                )
            row = measure(cell)
            if reason or child.returncode:
                row.update(valid=False, execution_error=reason or "worker_nonzero_exit")
                save(directory / "observation.json", row)
        observations.append(row)
        write_observations(observations)
        if not row["valid"]:
            save(
                OUT / "status.json",
                {
                    "state": "stopped_on_invalid_cell",
                    "active_cell": cell["name"],
                    "updated_at": now(),
                    "completed_cells": len(observations),
                    "reason": row,
                },
            )
            return
        save(
            OUT / "status.json",
            {
                "state": "scoring",
                "active_cell": cell["name"],
                "updated_at": now(),
                "completed_generation_cells": len(observations),
            },
        )
        try:
            row["race_scores"] = race_score(cell)
        except Exception as exc:
            save(
                OUT / "status.json",
                {
                    "state": "stopped_on_scoring_error",
                    "active_cell": cell["name"],
                    "updated_at": now(),
                    "error_type": type(exc).__name__,
                    "no_automatic_retry": True,
                },
            )
            return
        write_observations(observations)
    save(
        OUT / "status.json",
        {"state": "completed", "completed_cells": len(selected), "updated_at": now()},
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--only-cell", choices=[c["name"] for c in cells()])
    parser.add_argument("--worker", choices=[c["name"] for c in cells()])
    args = parser.parse_args()
    if args.status:
        print(json.dumps(reconcile_status(), ensure_ascii=False, indent=2))
    elif args.worker:
        worker(args.worker)
    elif args.prepare_only:
        prepare()
    else:
        supervise(args.only_cell)


if __name__ == "__main__":
    main()
