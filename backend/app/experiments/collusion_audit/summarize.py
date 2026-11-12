"""Collect accepted native Markdown artifacts without editing model reports."""

import argparse
import hashlib
import json
import re
import shutil
from collections import Counter

from .runner import HOME, write


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--output-name", default="chengyi-qwen-15")
    parser.add_argument("--batches", nargs="+")
    args = parser.parse_args()
    output = HOME / "results" / args.output_name
    if output.exists():
        raise ValueError("Refuse to overwrite a collected result")
    batches = [HOME / "runs" / name for name in args.batches] if args.batches else sorted((HOME / "runs").iterdir())
    selected = {}
    attempts = []
    for batch in batches:
        for path in sorted(batch.glob("M*/attempt_*/metrics.json")):
            metrics = json.loads(path.read_text())
            attempts.append({"path": str(path.parent), "status": metrics.get("status"), "rule_id": metrics.get("rule_id")})
            if metrics.get("status") == "completed" and metrics.get("native_finish") and metrics.get("report_valid"):
                selected[metrics["rule_id"]] = (path.parent, metrics)
    expected = [f"M{i:02d}" for i in range(1, 16)]
    missing = [rule for rule in expected if rule not in selected]
    if missing:
        raise ValueError("Missing accepted rule reports: " + ", ".join(missing))
    (output / "reports").mkdir(parents=True)
    (output / "cases").mkdir()
    rows = []
    totals = Counter()
    integrity = []
    lines = [
        "# 诚屹项目 · Qwen 围串标风险审核",
        "",
        "15条规则各自通过原生SP2自主调度，报告直接复制原生FINISH选中的产物，不改写模型结论。",
        "历史分析报告和规则库项目经验未作为模型输入或GT；此处不计算审核准确率。",
        "",
        "| 规则 | 报告 | 原生完成 | 代码状态 | 模型调用 | 总tokens | 工具调用 | 耗时(秒) |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ]
    for rule in expected:
        directory, metrics = selected[rule]
        source = directory / "report.md"
        destination = output / "reports" / (rule + ".md")
        shutil.copy2(source, destination)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == digest
        (output / "cases" / rule).symlink_to(directory, target_is_directory=True)
        calls = metrics["llm_totals"]
        row = {
            "rule_id": rule,
            "rule_name": metrics["rule_name"],
            "report": str(destination),
            "source_attempt": str(directory),
            "report_sha256": digest,
            "native_finish": True,
            "code_tool_run_ids": metrics["code_tool_run_ids"],
            "code_tool_statuses": metrics["code_tool_statuses"],
            "model_calls": calls["calls"],
            "input_tokens": calls["input_tokens"],
            "output_tokens": calls["output_tokens"],
            "total_tokens": calls["total_tokens"],
            "business_tool_calls": metrics["business_tool_calls"],
            "duration_seconds": metrics["duration_seconds"],
            "token_usage_complete": metrics["token_usage_complete"],
        }
        for key in ["model_calls", "input_tokens", "output_tokens", "total_tokens", "business_tool_calls", "duration_seconds"]:
            totals[key] += row[key]
        report = source.read_text()
        evidence_ids = set(re.findall(r"\bE\d{6}\b", report))
        known_ids = set()
        for run_id in metrics["code_tool_run_ids"]:
            for evidence_file in (directory / "runtime").rglob(run_id + "/evidence.jsonl"):
                known_ids.update(json.loads(line)["evidence_id"] for line in evidence_file.read_text().splitlines())
        unknown_ids = sorted(evidence_ids - known_ids)
        # This is provenance validation only, never a repair or semantic judge.
        integrity.append({"rule_id": rule, "report_hash_preserved": True, "claimed_evidence_ids": sorted(evidence_ids), "unknown_evidence_ids": unknown_ids, "model_report_not_rewritten": True})
        rows.append(row)
        lines.append(
            f"| {rule} {row['rule_name']} | [{rule}.md](reports/{rule}.md) | 是 | {', '.join(row['code_tool_statuses'])} | {row['model_calls']} | {row['total_tokens']:,} | {row['business_tool_calls']} | {row['duration_seconds']:.2f} |"
        )
    write(
        output / "summary.json",
        {
            "rules": rows,
            "totals_audit_only": dict(totals),
            "rule_count": 15,
            "source_batches": [str(batch) for batch in batches],
            "all_attempts": attempts,
            "notes": [
                "Code statuses partial/not_applicable are not risk verdicts.",
                "No historical reports or project-specific workbook experience in model inputs.",
                "Failed/cancelled pilots and preflights are preserved separately, excluded from selected audit-only totals.",
                "Reports are model opinions for review, not legal findings or an accuracy measurement.",
            ],
        },
    )
    write(output / "integrity.json", {"reports": integrity, "all_report_hashes_preserved": True, "report_count": 15, "unknown_evidence_ids_present": any(row["unknown_evidence_ids"] for row in integrity)})
    lines += [
        "",
        "## 查看轨迹与开销",
        "",
        "`cases/Mxx/` 链接到该规则的完整输入、模型调用、工具结果、原生事件、有效配置、最终状态和计算原始集合。",
        "`summary.json` 给出逐规则模型调用、输入/输出token、业务工具次数、耗时和来源；`integrity.json` 只检查产物哈希和证据ID，不改写报告。",
        "",
        "正式汇总排除预检和失败试运行，它们仍保留在 `collusion_audit/runs/`、各预检目录中。扫描、图像核验、模板或关系材料缺口需以每条报告原文为准。",
    ]
    (output / "README.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"result_directory": str(output), "reports": 15, "totals": dict(totals)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
