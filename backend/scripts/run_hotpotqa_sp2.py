"""Run a small, reproducible HotpotQA evaluation through the SP2 gateway.

The benchmark questions and contexts are read exclusively from the local
HotpotQA cache.  No gold answer or supporting-fact annotation is included in
the prompt sent to the agent.

Example:
    SP2_EXPERIMENT_EMAIL=... SP2_EXPERIMENT_PASSWORD=... \
      uv run python scripts/run_hotpotqa_sp2.py --limit 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import string
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from datasets import load_from_disk

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.compare_sp2_deerflow_qwen import DEPLOYMENTS, GatewaySession


DEFAULT_SAMPLE = Path("/data/sp/jxk/xwx/data/hotpotqa/experience_sample_7500.jsonl")
DEFAULT_RAW = Path("/data/sp/jxk/xwx/data/hotpotqa/raw")
DEFAULT_OUTPUT = Path("/data/sp/jxk/xwx/data/hotpotqa/sp2_results/qwen_smoke.json")


def _normalize_answer(value: str) -> str:
    value = value.lower()
    value = "".join(ch for ch in value if ch not in string.punctuation)
    value = re.sub(r"\b(a|an|the)\b", " ", value)
    return " ".join(value.split())


def _scores(prediction: str, aliases: list[str]) -> dict[str, float]:
    normalized_prediction = _normalize_answer(prediction)
    exact_match = max(
        (float(normalized_prediction == _normalize_answer(alias)) for alias in aliases),
        default=0.0,
    )
    prediction_tokens = normalized_prediction.split()
    best_f1 = 0.0
    for alias in aliases:
        answer_tokens = _normalize_answer(alias).split()
        overlap = Counter(prediction_tokens) & Counter(answer_tokens)
        common = sum(overlap.values())
        if not prediction_tokens or not answer_tokens:
            candidate = float(prediction_tokens == answer_tokens)
        elif common == 0:
            candidate = 0.0
        else:
            precision = common / len(prediction_tokens)
            recall = common / len(answer_tokens)
            candidate = 2 * precision * recall / (precision + recall)
        best_f1 = max(best_f1, candidate)
    return {"exact_match": exact_match, "f1": best_f1}


def _load_samples(path: Path, *, offset: int, limit: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index < offset:
                continue
            if len(selected) >= limit:
                break
            selected.append(json.loads(line))
    if len(selected) != limit:
        raise ValueError(f"Requested {limit} rows at offset {offset}, found {len(selected)}")
    return selected


def _prompt(row: dict[str, Any]) -> str:
    context = row["context"]
    documents: list[str] = []
    for title, sentences in zip(context["title"], context["sentences"], strict=True):
        documents.append(f"[{title}]\n{''.join(sentences).strip()}")
    evidence = "\n\n".join(documents)
    return (
        "请仅根据下面给出的本地资料回答问题，不要联网，不要调用搜索。"
        "答案必须尽量简短，只输出答案本身，不要解释、不要重复问题。\n\n"
        f"资料：\n{evidence}\n\n问题：{row['question']}"
    )


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    email = os.getenv("SP2_EXPERIMENT_EMAIL", "").strip()
    password = os.getenv("SP2_EXPERIMENT_PASSWORD", "")
    if not email or not password:
        raise SystemExit("Set SP2_EXPERIMENT_EMAIL and SP2_EXPERIMENT_PASSWORD")

    sampled = _load_samples(args.sample_path, offset=args.offset, limit=args.limit)
    dataset = load_from_disk(str(args.raw_path))
    deployment = DEPLOYMENTS["sp2"]
    session = GatewaySession(deployment, email, password)
    results: list[dict[str, Any]] = []
    if args.resume and args.output.exists():
        try:
            previous = json.loads(args.output.read_text(encoding="utf-8"))
            previous_results = previous.get("results", []) if isinstance(previous, dict) else []
            results = [item for item in previous_results if isinstance(item, dict)]
        except (OSError, json.JSONDecodeError):
            results = []
    completed_ids = {str(item.get("sample_id")) for item in results}

    def checkpoint(status: str) -> None:
        completed = [item for item in results if "error" not in item]
        payload = {
            "status": status,
            "system": "SP2.0",
            "assistant_id": deployment.assistant_id,
            "model": deployment.context["model_name"],
            "data_mode": "local full context; no web search",
            "offset": args.offset,
            "requested": args.limit,
            "processed": len(results),
            "completed": len(completed),
            "failed": len(results) - len(completed),
            "exact_match": sum(item["exact_match"] for item in completed) / len(completed) if completed else 0.0,
            "f1": sum(item["f1"] for item in completed) / len(completed) if completed else 0.0,
            "results": results,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        temporary.replace(args.output)

    started = time.monotonic()
    try:
        await session.healthcheck()
        await session.login()
        for position, sample in enumerate(sampled, 1):
            if sample["sample_id"] in completed_ids:
                continue
            source_index = int(sample["metadata"]["source_row_index"])
            row = dataset[source_index]
            if row["id"] != sample["metadata"]["source_fields"]["id"]:
                raise ValueError(f"Source row mismatch for {sample['sample_id']}")
            thread_id = await session.create_thread(
                f"hotpotqa-{sample['sample_id']}",
                f"HotpotQA smoke {sample['sample_id']}",
            )
            print(f"[{position}/{len(sampled)}] {sample['sample_id']} thread={thread_id}", flush=True)
            try:
                turn = await session.run_turn(
                    thread_id=thread_id,
                    prompt=_prompt(row),
                    upload_info=[],
                )
                aliases = list(sample.get("ground_truth_aliases") or [sample["ground_truth"]])
                score = _scores(turn["answer"], aliases)
                result = {
                    "sample_id": sample["sample_id"],
                    "source_row_index": source_index,
                    "question": sample["question"],
                    "ground_truth": sample["ground_truth"],
                    "ground_truth_aliases": aliases,
                    "prediction": turn["answer"],
                    **score,
                    "elapsed_seconds": turn["elapsed_seconds"],
                    "thread_id": thread_id,
                    "thread_url": f"{deployment.public_url}/workspace/chats/{thread_id}",
                    "run": turn["run"],
                    "event_summary": turn["event_summary"],
                }
                print(
                    f"    EM={score['exact_match']:.0f} F1={score['f1']:.3f} "
                    f"elapsed={turn['elapsed_seconds']:.1f}s prediction={turn['answer']!r}",
                    flush=True,
                )
            except Exception as exc:
                result = {
                    "sample_id": sample["sample_id"],
                    "source_row_index": source_index,
                    "question": sample["question"],
                    "ground_truth": sample["ground_truth"],
                    "prediction": "",
                    "exact_match": 0.0,
                    "f1": 0.0,
                    "thread_id": thread_id,
                    "thread_url": f"{deployment.public_url}/workspace/chats/{thread_id}",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                print(f"    FAILED: {type(exc).__name__}: {exc}", flush=True)
            results.append(result)
            completed_ids.add(str(sample["sample_id"]))
            if len(results) % args.checkpoint_every == 0 or position == len(sampled):
                checkpoint("running")
    finally:
        await session.close()

    completed = [item for item in results if "error" not in item]
    summary = {
        "status": "completed",
        "system": "SP2.0",
        "assistant_id": deployment.assistant_id,
        "model": deployment.context["model_name"],
        "data_mode": "local full context; no web search",
        "offset": args.offset,
        "requested": args.limit,
        "completed": len(completed),
        "failed": len(results) - len(completed),
        "exact_match": sum(item["exact_match"] for item in completed) / len(completed) if completed else 0.0,
        "f1": sum(item["f1"] for item in completed) / len(completed) if completed else 0.0,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "results": results,
    }
    checkpoint("completed")
    # Preserve total wall time in the final summary without changing resume data.
    final_payload = json.loads(args.output.read_text(encoding="utf-8"))
    final_payload["elapsed_seconds"] = summary["elapsed_seconds"]
    args.output.write_text(json.dumps(final_payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-path", type=Path, default=DEFAULT_SAMPLE)
    parser.add_argument("--raw-path", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--checkpoint-every", type=int, default=25)
    parser.add_argument("--no-resume", dest="resume", action="store_false")
    parser.set_defaults(resume=True)
    args = parser.parse_args()
    if args.offset < 0 or args.limit <= 0 or args.checkpoint_every <= 0:
        parser.error("--offset must be non-negative; --limit and --checkpoint-every must be positive")
    summary = asyncio.run(_run(args))
    print(json.dumps({key: value for key, value in summary.items() if key != "results"}, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
