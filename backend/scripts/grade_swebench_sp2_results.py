"""Incrementally grade SP2 SWE-bench result files with the official harness."""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any


def _safe(value: str) -> str:
    return "".join(character if character.isalnum() or character in "._-" else "-" for character in value)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _load_result(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _summary(results_root: Path, grade_root: Path, *, expected_count: int) -> dict[str, Any]:
    result_paths = sorted(results_root.glob("*.result.json"))
    grades = [payload for path in sorted(grade_root.glob("*.grade.json")) if (payload := _load_result(path))]
    return {
        "expected_instances": expected_count,
        "generated_instances": len(result_paths),
        "graded_instances": len(grades),
        "resolved_instances": sum(1 for grade in grades if grade.get("resolved") is True),
        "unresolved_instances": sum(1 for grade in grades if grade.get("resolved") is False),
        "harness_error_instances": sum(1 for grade in grades if grade.get("harness_error")),
        "skipped_invalid_patch_instances": sum(1 for grade in grades if grade.get("skipped_grading") is True),
        "scored_instances": sum(1 for grade in grades if grade.get("skipped_grading") is not True),
        "resolved_rate_over_graded": (
            sum(1 for grade in grades if grade.get("resolved") is True) / len(grades)
            if grades
            else None
        ),
        "resolved_rate_over_scored": (
            sum(1 for grade in grades if grade.get("resolved") is True)
            / sum(1 for grade in grades if grade.get("skipped_grading") is not True)
            if any(grade.get("skipped_grading") is not True for grade in grades)
            else None
        ),
    }


def _pregrade_rejection(result: dict[str, Any]) -> str | None:
    """Return a safe reason to keep malformed/unverified patches out of grader."""
    quality = result.get("patch_quality")
    if not isinstance(quality, dict) or quality.get("valid_source_patch") is not True:
        return "invalid_source_patch"
    if result.get("patch_status") in {"none", "partial", "needs_revision"}:
        return str(result.get("patch_status"))
    gate = result.get("verification_gate")
    if isinstance(gate, dict) and gate.get("eligible") is False:
        return str(gate.get("reason") or "verification_gate_rejected")
    return None


def main(args: argparse.Namespace) -> None:
    project_root = Path(args.harness_project).resolve()
    sys.path.insert(0, str(project_root))
    from evaluation.common.swe_harness import SWEHarness

    results_root = Path(args.results_root).resolve()
    grade_root = Path(args.grade_root).resolve()
    harness = SWEHarness(
        workspace=Path(args.workspace).resolve(),
        timeout_s=args.timeout,
        cache_level="env",
        use_cache=True,
    )

    while True:
        for result_path in sorted(results_root.glob("*.result.json")):
            result = _load_result(result_path)
            if result is None:
                continue
            instance_id = str(result.get("instance_id") or "").strip()
            if not instance_id:
                continue
            grade_path = grade_root / f"{_safe(instance_id)}.grade.json"
            if grade_path.exists():
                continue
            patch = str(result.get("model_patch") or "")
            rejection = _pregrade_rejection(result)
            if rejection is not None:
                grade = {
                    "instance_id": instance_id,
                    "resolved": False,
                    "patch_extracted": bool(patch.strip()),
                    "patch_apply_ok": False,
                    "harness_error": f"pregrader rejected patch: {rejection}",
                    "failure_kind": rejection,
                    "skipped_grading": True,
                    "verification_gate": result.get("verification_gate"),
                    "patch_quality": result.get("patch_quality"),
                }
                _write_json_atomic(grade_path, grade)
                print(json.dumps(grade, ensure_ascii=False), flush=True)
                continue
            try:
                grade = harness.verify_patch(
                    instance_id,
                    patch,
                    model_name=args.model_name,
                    run_id=f"{args.run_id_prefix}_{_safe(instance_id)}",
                ).to_dict()
            except Exception as exc:  # isolate one grader failure from the batch
                grade = {
                    "instance_id": instance_id,
                    "resolved": False,
                    "patch_extracted": bool(patch.strip()),
                    "patch_apply_ok": None,
                    "harness_error": "".join(traceback.format_exception(exc)).strip(),
                }
            _write_json_atomic(grade_path, grade)
            print(json.dumps(grade, ensure_ascii=False), flush=True)

        summary = _summary(results_root, grade_root, expected_count=args.expected_count)
        _write_json_atomic(grade_root / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        if not args.watch or summary["graded_instances"] >= args.expected_count:
            return
        time.sleep(max(1.0, args.poll_interval))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", required=True)
    parser.add_argument("--grade-root", required=True)
    parser.add_argument("--harness-project", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--expected-count", type=int, default=50)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--model-name", default="sp2-deepseek-v4-flash")
    parser.add_argument("--run-id-prefix", default="sp2_canonical50")
    parser.add_argument("--poll-interval", type=float, default=20.0)
    parser.add_argument("--watch", action=argparse.BooleanOptionalAction, default=True)
    main(parser.parse_args())
