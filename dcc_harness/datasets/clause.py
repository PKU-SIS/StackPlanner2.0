"""CLAUSE EVAL3 In-Text plugin. Dataset identity and gold never enter agent inputs."""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path

from dcc_harness.data import PublicTask, Task
from dcc_harness.clause_metrics import (
    CONFIG,
    CATEGORIES,
    SOURCES,
    protocol,
    validate_gold,
)


@lru_cache(maxsize=2)
def prompt_text(variant="l1"):
    if variant not in {"l1", "l2"}:
        raise ValueError("CLAUSE prompt_variant must be l1 or l2")
    return (CONFIG / f"{variant}_system.txt").read_text(encoding="utf-8")


def ids_hash(ids):
    return hashlib.sha256(
        json.dumps(sorted(ids), ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def prepare_task(public: PublicTask):
    return public.prompt + (
        "\n\nCLAUSE EVAL3 in-text task. The complete document is the uploaded file "
        "/mnt/user-data/uploads/document.txt. Read it using file tools; review the entire document, "
        "including distant definitions and cross-references. Preserve verbatim complete sentences. "
        "Deliver only the requested JSON array, preferably as /mnt/user-data/outputs/clause_answer.json. "
        "Select that complete answer as the final artifact and finish through native FINISH. "
        "Notes, plans, summaries and this task brief are not the final answer."
    )


def validate_dataset(tasks: list[Task]):
    if len({t.split for t in tasks}) != 1:
        raise ValueError("Run one CLAUSE split per experiment")
    variants = set()
    for task in tasks:
        if (
            task.benchmark != "clause"
            or task.document
            or len(task.files) != 1
            or task.files[0].name != "document.txt"
        ):
            raise ValueError("CLAUSE requires one neutral document.txt file")
        if not task.files[0].text.strip() or "<*$p$*>" in task.files[0].text:
            raise ValueError("CLAUSE input is empty or contains an answer marker")
        variant = task.public_metadata.get("prompt_variant")
        if set(task.public_metadata) != {
            "prompt_variant"
        } or task.prompt != prompt_text(variant):
            raise ValueError("CLAUSE public prompt does not match its pinned variant")
        variants.add(variant)
        if task.gold is not None:
            perturbations = validate_gold(task.gold)
            meta = task.metadata
            if (
                meta.get("protocol") != protocol()["protocol"]
                or meta.get("source") not in SOURCES
                or meta.get("category") not in CATEGORIES
            ):
                raise ValueError("CLAUSE evaluation metadata is missing or invalid")
            if (
                meta.get("input_sha256")
                != hashlib.sha256(task.files[0].text.encode()).hexdigest()
            ):
                raise ValueError(
                    "CLAUSE document hash differs from annotation metadata"
                )
            if meta.get("perturbations") != len(perturbations):
                raise ValueError("CLAUSE annotation count differs from metadata")
    if len(variants) != 1:
        raise ValueError("Do not mix CLAUSE L1 and L2 within one experiment")


def parse_answer(public: PublicTask, text: str):
    value = json.loads(text)
    if not isinstance(value, list):
        raise ValueError("CLAUSE prediction must be a JSON array")
    for item in value:
        if not isinstance(item, dict) or set(item) != {"text", "explanation"}:
            raise ValueError("CLAUSE items require exactly text/explanation")
        if (
            any(not isinstance(item[key], str) for key in ("text", "explanation"))
            or not item["text"].strip()
        ):
            raise ValueError("CLAUSE fields must be strings with nonblank text")
    return value


def setup_evaluation(context):
    from dcc_harness.clause_metrics import TextMetrics

    options = context.options
    allowed = {
        "resource_root",
        "bert_model_dir",
        "without_bert",
        "device",
        "batch_size",
    }
    if set(options) - allowed:
        raise ValueError("unknown CLAUSE evaluation option")
    without_bert = options.get("without_bert", False)
    batch_size = options.get("batch_size", 8)
    if (
        not isinstance(without_bert, bool)
        or isinstance(batch_size, bool)
        or not isinstance(batch_size, int)
        or batch_size < 1
    ):
        raise ValueError("invalid CLAUSE metric mode/batch_size")
    resource_root = Path(
        options.get("resource_root")
        or os.environ.get("DCC_CLAUSE_RESOURCE_ROOT")
        or CONFIG.parents[1] / ".benchmark-data/clause/resources"
    ).resolve()
    model_dir = Path(
        options.get("bert_model_dir") or resource_root / "models/roberta-large"
    ).resolve()
    context.provenance.update(
        protocol=protocol()["protocol"],
        input_mode="sp2-files",
        llm_judge_enabled=False,
        metrics_complete=not without_bert,
        explanation_scored=False,
    )
    try:
        calc = TextMetrics(
            nltk_data=resource_root / "nltk_data",
            without_bert=without_bert,
            model_dir=model_dir,
            device=options.get("device", "cpu"),
            batch_size=batch_size,
        )
    except Exception as exc:
        context.provenance["setup_error"] = str(exc)
        raise
    context.resources["text_metrics"] = calc
    context.provenance.update(calc.provenance)


def score_task(task: Task, text: str, context):
    from dcc_harness.clause_metrics import align, average

    if task.gold is None:
        raise ValueError(
            "CLAUSE evaluation requires the private manifest containing annotations"
        )
    validate_gold(task.gold)
    calc = context.resources["text_metrics"]
    alignment = align(
        task.gold, parse_answer(task.public_input(), text), calc.sentence_tokenize
    )
    for group in alignment["groups"]:
        raw = calc.score(group["gt_text"], group["pred_text"])
        group["text_scores"] = {
            key: value for key, value in raw.items() if not key.startswith("_")
        }
        group["bert_truncated"] = raw.get("_bert_truncated", False)
    groups = alignment.pop("groups")
    context.provenance["bert"]["truncated_groups"] = calc.truncated_groups
    return {
        **alignment,
        "record_id": task.id,
        "source": task.metadata["source"],
        "category": task.metadata["category"],
        "matched_groups": len(groups),
        "text_scores": average([g["text_scores"] for g in groups]) if groups else None,
        "alignments": groups,
    }


def aggregate(tasks, rows, context):
    from dcc_harness.clause_metrics import aggregate_documents

    split = tasks[0].split
    expected = protocol()["splits"][split]
    complete = (
        len(tasks) == expected["documents"]
        and ids_hash(t.id for t in tasks) == expected["ids_sha256"]
    )
    if not complete and not context.diagnostic_partial:
        raise ValueError(
            "CLAUSE scores require the complete pinned split; use --allow-partial only for diagnostics"
        )
    calc = context.resources["text_metrics"]
    return {
        "protocol": protocol()["protocol"],
        "input_mode": "sp2-files",
        "split": split,
        "prompt_variant": tasks[0].public_metadata["prompt_variant"],
        "llm_judge_enabled": False,
        "explanation_scored": False,
        "complete_benchmark_split": complete,
        "expected_documents": expected["documents"],
        "scored_documents": len(tasks),
        "metrics_complete": calc.bert is not None,
        "metrics_enabled": ["miss", "extra", "rouge-1", "rouge-2", "rouge-l", "meteor"]
        + (["bert_score_f1"] if calc.bert is not None else []),
        "bert_truncated_groups": calc.truncated_groups,
        **aggregate_documents([row["metrics"] for row in rows]),
    }


def create_plugin():
    from .benchmark_plugins import BenchmarkPlugin, stage_files

    return BenchmarkPlugin(
        "clause",
        prepare_task,
        stage_files,
        CONFIG.parents[0] / "stackplanner_skills/clause",
        parse_answer=parse_answer,
        score_task=score_task,
        aggregate=aggregate,
        validate_dataset=validate_dataset,
        setup_evaluation=setup_evaluation,
        evaluation_version="intext-yes-whole-document-v1/GPT-DFS/no-Judge/sp2-files",
        evaluation_resources=tuple(
            CONFIG / name
            for name in (
                "protocol.json",
                "l1_system.txt",
                "l2_system.txt",
                "prompt_provenance.json",
            )
        ),
    )
