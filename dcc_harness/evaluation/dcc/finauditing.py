"""Adapters to the pinned official FinAuditing Starter Kit evaluators."""

from __future__ import annotations

import ast
import contextlib
import io
import json
import tempfile
from collections import Counter
from pathlib import Path

from . import Evaluator
from .models import model_for

VERSION = "finauditing-startkit-ccf99d9"


def _options(options: dict, allowed: set[str]) -> None:
    if set(options) - allowed:
        raise ValueError("unsupported FinAuditing evaluator options")


def _finre_score(_sample, label, text, options, _context):
    from dcc_harness.evaluation.dcc_official import finre

    _options(options, set())
    gold = finre.normalize_findre_gold(label["gold"]["answer"])
    prediction = finre.normalize_findre_pred(text)
    if gold == finre.INVALID_LABEL:
        raise ValueError("FinRE reference answer is not a valid official class")
    return {"gold_label": gold, "predicted_label": prediction, "prediction_valid": prediction != finre.INVALID_LABEL, "accuracy": float(gold == prediction)}


def _finre_aggregate(metrics, _options, _context):
    from dcc_harness.evaluation.dcc_official import finre

    # Run the actual official JSONL wrapper, keeping stdout JSON-only for CLI users.
    with tempfile.TemporaryDirectory(prefix="finre-eval-") as temporary:
        path = Path(temporary) / "predictions.jsonl"
        path.write_text("".join(json.dumps({"prediction": row["predicted_label"], "ground_truth": row["gold_label"]}) + "\n" for row in metrics))
        report = io.StringIO()
        with contextlib.redirect_stdout(report), contextlib.redirect_stderr(io.StringIO()):
            result = finre.evaluate_findre(str(path))
    return {"official_metrics": result, "classification_report": report.getvalue(), "metric_scale": "fraction"}


def _gold_items(value):
    # Source labels are JSON-encoded Python-list strings. Decode that storage
    # representation before passing labels to the official JSONL API; never eval.
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = ast.literal_eval(value)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("FinSM references must decode to a list of strings")
    return value


def _finsm_parameters(options):
    _options(options, {"ks", "ignore_empty_gold"})
    ks = options.get("ks", [1, 5, 10, 20])
    ignore = options.get("ignore_empty_gold", True)
    if not isinstance(ks, list) or not ks or any(type(k) is not int or k < 1 for k in ks) or len(set(ks)) != len(ks) or type(ignore) is not bool:
        raise ValueError("FinSM ks must be unique positive integers and ignore_empty_gold a Boolean")
    return ks, ignore


def _finsm_score(_sample, label, text, options, _context):
    from dcc_harness.evaluation.dcc_official import finsm

    _finsm_parameters(options)
    return {"gold_items": finsm.parse_gold_to_list(_gold_items(label["gold"]["answer"])), "predicted_items": finsm.parse_pred_to_list(text)}


def _finsm_aggregate(metrics, options, _context):
    from dcc_harness.evaluation.dcc_official import finsm

    ks, ignore = _finsm_parameters(options)
    result = finsm.evaluate_metrics([row["gold_items"] for row in metrics], [row["predicted_items"] for row in metrics], ks=ks, ignore_empty_gold=ignore)
    return {
        "official_metrics": result,
        "metric_values": {key: float(value.removesuffix("%")) for key, value in result.items()},
        "metric_scale": "percent",
        "n": len(metrics),
        "n_empty_gold": sum(not row["gold_items"] for row in metrics),
        "n_empty_predictions": sum(not row["predicted_items"] for row in metrics),
        "ks": ks,
        "ignore_empty_gold": ignore,
    }


def _finmr_score(_sample, label, text, options, context):
    from dcc_harness.evaluation.dcc_official import finmr

    _options(options, {"judge"})
    gold = finmr.normalize_two_key_obj(finmr.parse_json_object_best_effort(label["gold"]["answer"]))
    if gold is None:
        return {"judge_label": None, "gold_invalid": True, "judge_response": None}
    judge = model_for(options.get("judge"), context, "finauditing_finmr.judge", default_model="gpt-5-mini")
    prompt = finmr.get_prompt(gold, text)
    api = judge.config.get("api", "chat" if judge.model.lower().startswith(("gpt-4o", "gpt-4.1")) else "responses")
    if api == "responses":
        response, request_hash = judge("responses", input=prompt, reasoning={"effort": "minimal"}, text={"verbosity": "low"})
    elif api == "chat":
        response, request_hash = judge(
            "chat", messages=[{"role": "system", "content": "You are a strict evaluator."}, {"role": "user", "content": prompt}], temperature=0.0, max_tokens=4
        )
    else:
        raise ValueError("FinMR judge api must be chat or responses")
    return {
        "judge_label": finmr.normalize_judge_label(response),
        "gold_invalid": False,
        "judge_response": response,
        "judge_request_sha256": request_hash,
        "evaluation_protocol": "official_prompt_custom_judge" if judge.provenance["custom_model"] or judge.callback else "official_startkit",
    }


def _finmr_aggregate(metrics, _options, context):
    # Exact A/S/E/C denominators and two-decimal percentage rounding from
    # evaluate_finmr_with_judge. Judge parse failures are explicitly disclosed.
    counts = Counter(row["judge_label"] for row in metrics)
    total = len(metrics)
    evaluated = sum(counts[label] for label in "ASEC")
    invalid_gold = sum(row["gold_invalid"] for row in metrics)

    def pct(n, d):
        return round(100.0 * n / d, 2) if d > 0 else 0.0

    result = {
        "total": total,
        "evaluated": evaluated,
        "Parsing success rate(%)": pct(evaluated, total),
        "ACC(%)": pct(counts["A"], evaluated),
        "Structural error rate(%)": pct(counts["S"], evaluated),
        "Extraction error rate(%)": pct(counts["E"], evaluated),
        "Calculating error rate(%)": pct(counts["C"], evaluated),
        "gold_invalid_count": invalid_gold,
        "judge_output_unparsed_count": total - evaluated - invalid_gold,
    }
    return {
        "official_metrics": result,
        "metric_scale": "percent",
        "label_counts": {label: counts[label] for label in "ASEC"},
        "judge": context.provenance.get("models", {}).get("finauditing_finmr.judge"),
    }


def finre_evaluator():
    return Evaluator("finauditing_finre", VERSION + "-finre", _finre_score, _finre_aggregate)


def finsm_evaluator():
    return Evaluator("finauditing_finsm", VERSION + "-finsm", _finsm_score, _finsm_aggregate)


def finmr_evaluator():
    return Evaluator("finauditing_finmr", VERSION + "-finmr", _finmr_score, _finmr_aggregate)
