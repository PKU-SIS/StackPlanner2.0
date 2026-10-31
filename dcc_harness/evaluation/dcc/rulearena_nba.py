"""Official NBA answer matching and optional model-extracted rule metrics."""

from __future__ import annotations

import json
import math
from pathlib import Path

from dcc_harness.preprocessing.loaders import inside

from . import Evaluator
from .models import ModelCallError, model_for

OFFICIAL = Path(__file__).resolve().parents[1] / "dcc_official"


class ExtractionSchemaError(ModelCallError):
    pass


def extraction_contract() -> dict:
    return json.loads((OFFICIAL / "nba_extraction.json").read_text())


def extraction_schema(contract) -> dict:
    fields = {field["name"]: {"type": "boolean", "description": field["description"]} for field in contract["fields"]}
    return {
        "type": "object",
        "properties": {
            "answer": {"type": "boolean"},
            "applied_rules": {
                "type": "object",
                "description": "If one of the rules are involved in the provided analysis, you should output True for it, otherwise False.",
                "properties": fields,
                "required": list(fields),
                "additionalProperties": False,
            },
        },
        "required": ["answer", "applied_rules"],
        "additionalProperties": False,
    }


def _score(sample, label, text, options, context):
    if set(options) - {"rule_extractor", "extraction_attempts"}:
        raise ValueError("unsupported RuleArena NBA evaluator options")
    gold = label["gold"]
    if type(gold["answer"]) is not bool:
        raise ValueError("NBA labels require Boolean answers; True means violation")
    target = (
        "Answer: False" if not gold["answer"] else f"Answer: True. Illegal Operation: {gold['illegal_operation']}. Problematic Team: {gold['problematic_team']}"
    )
    response = text.replace("**", "")
    result = {"accuracy": float(target in response), "complexity": label["metadata"]["complexity"]}
    if "rule_extractor" not in options:
        result["rule_metrics_enabled"] = False
        return result
    attempts = options.get("extraction_attempts", 5)
    if type(attempts) is not int or not 1 <= attempts <= 5:
        raise ValueError("NBA extraction_attempts must be an integer between 1 and 5")
    contract = extraction_contract()
    extractor = model_for(options["rule_extractor"], context, "rulearena_nba.rule_extractor", default_model=contract["model"])
    if extractor.config.get("api", "chat") != "chat":
        raise ValueError("the official NBA structured extractor uses chat completions")
    bundle = context.resources["dcc"]["bundle"]
    rules = "\n\n".join(inside(bundle, asset["path"]).read_text() for asset in sample["rules"])
    scenario = "\n\n".join(inside(bundle, asset["path"]).read_text() for asset in sample["documents"])
    prompt = contract["prompt_template"].replace("$reference_rules", rules).replace("$question", scenario).replace("$response", response)
    names = [field["name"] for field in contract["fields"]]
    audit = {"attempts": 0, "failures": [], "status": "pending"}
    context.provenance.setdefault("nba_extraction_attempts", {})[sample["id"]] = audit
    # The original driver allows five attempts. Offline evaluation retries only
    # extraction against the SAME saved delivery; never regenerate SP answers.
    for attempt in range(1, attempts + 1):
        audit["attempts"] = attempt
        try:
            prediction, request_hash = extractor(
                "chat",
                messages=[{"role": "system", "content": contract["system_prompt"]}, {"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_schema", "json_schema": {"name": "Response", "strict": True, "schema": extraction_schema(contract)}},
            )
            applied = _parse_prediction(prediction, names)
        except ModelCallError as exc:
            audit["failures"].append({"attempt": attempt, "error_type": type(exc).__name__})
            if attempt == attempts:
                audit["status"] = "failed"
                raise ModelCallError(f"NBA rule extraction failed after {attempts} attempts; no rule metrics were produced") from None
        else:
            audit["status"] = "completed"
            break
    result.update(
        rule_metrics_enabled=True,
        applied_rules=applied,
        relevant_rules=gold["relevant_rules"],
        extraction_request_sha256=request_hash,
        extraction_attempts=audit["attempts"],
        extraction_failures=list(audit["failures"]),
        evaluation_protocol="official_prompt_custom_extractor" if extractor.provenance["custom_model"] or extractor.callback else "official_gpt4o_extractor",
    )
    return result


def _parse_prediction(prediction, names):
    if isinstance(prediction, str):
        try:
            prediction = json.loads(prediction)
        except ValueError:
            raise ExtractionSchemaError("NBA extractor did not return its required JSON object") from None
    if (
        not isinstance(prediction, dict)
        or set(prediction) != {"answer", "applied_rules"}
        or type(prediction["answer"]) is not bool
        or not isinstance(prediction["applied_rules"], dict)
        or set(prediction["applied_rules"]) != set(names)
        or any(type(value) is not bool for value in prediction["applied_rules"].values())
    ):
        raise ExtractionSchemaError("NBA extractor response differs from the original 55-field schema")
    return [name for name in names if prediction["applied_rules"][name]]


def _numbers(value):
    if isinstance(value, dict):
        return {key: _numbers(item) for key, item in value.items()}
    if isinstance(value, float):
        return float(value) if math.isfinite(value) else None
    return value


def _one_group(metrics):
    accuracy = sum(row["accuracy"] for row in metrics) / len(metrics)
    result = {"n": len(metrics), "accuracy": accuracy, "accuracy_formatted": f"{accuracy:.4f}"}
    enabled = [row["rule_metrics_enabled"] for row in metrics]
    if not any(enabled):
        result["rule_metrics"] = {"status": "not_requested", "reason": "configure rule_extractor to run the official model-based rule metrics"}
        return result
    if not all(enabled):
        raise ValueError("cannot mix NBA rule metrics enabled/disabled in one evaluation group")
    from dcc_harness.evaluation.dcc_official.nba_metrics import (
        metrics as official_metrics,
    )

    names = [field["name"] for field in extraction_contract()["fields"]]
    pairs = [(row["applied_rules"], row["relevant_rules"]) for row in metrics]
    result["rule_metrics"] = {
        "status": "computed",
        **_numbers(official_metrics(pairs, names)),
        "metric_denominators": {
            "problem_wise_precision": sum(bool(pred) for pred, _gold in pairs),
            "problem_wise_recall": sum(bool(gold) for _pred, gold in pairs),
        },
        "gold_ids_outside_extractor_schema": sorted({name for _pred, gold in pairs for name in gold} - set(names)),
    }
    return result


def _aggregate(metrics, _options, context):
    return {
        **_one_group(metrics),
        "by_complexity": {
            str(level): _one_group([row for row in metrics if row["complexity"] == level]) for level in sorted({row["complexity"] for row in metrics})
        },
        "extractor": context.provenance.get("models", {}).get("rulearena_nba.rule_extractor"),
    }


def make_evaluator():
    return Evaluator("rulearena_nba", "rulearena-nba-59f60f6", _score, _aggregate)
