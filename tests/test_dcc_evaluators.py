from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
import types

import pytest

from dcc_harness.data import load_tasks
from dcc_harness.evaluation.benchmarks import PROTOCOL
from dcc_harness.evaluation.dcc import Evaluator, finauditing, plugin, rulearena_nba
from dcc_harness.evaluation.dcc.models import Model, ModelCallError
from dcc_harness.evaluation.offline import (
    EvaluationContext,
    evaluate_run,
    plugin_identity,
    task_identity,
)
from dcc_harness.preprocessing.models import Asset, Sample
from dcc_harness.preprocessing.pipeline import (
    atomic_json,
    encoded,
    prepare,
    render,
)
from dcc_harness.preprocessing.ragflow import index_bundle

SPECS = ("dcc_harness.preprocessing.native:make_plugin", "dcc_harness.preprocessing.native:make_inline_plugin")


def make_bundle(tmp_path, samples, mode="inline"):
    from test_dcc_preprocessing import FakeRagflow

    cfg = {"document_mode": mode, "ragflow": {"base_url": "https://rag.example", "poll_interval": 0, "parse_timeout": 3}}
    bundle = tmp_path / "bundle"
    prepare(cfg, bundle, samples=samples)
    if mode == "ragflow":
        index_bundle(bundle, cfg["ragflow"], client=FakeRagflow())
    render(bundle, cfg)
    return bundle, load_tasks(bundle / "public/tasks.jsonl")


def sample(identifier, dataset, answer, **metadata):
    return Sample(
        identifier,
        dataset,
        "test",
        "Apply the supplied rules.",
        (Asset("scenario", text=f"Public scenario {identifier}: $1,000."),),
        (Asset("rules", text="PUBLIC_REFERENCE_RULES"),),
        gold=answer,
        metadata={"private": "PRIVATE_METADATA", **metadata},
    )


def inference_run(path, tasks, deliveries):
    path.mkdir()
    atomic_json(path / "manifest.json", {"protocol": PROTOCOL, "phase": "inference", "task_inputs": [task_identity(t) for t in tasks]})
    rows = []
    for task, text in zip(tasks, deliveries, strict=True):
        row = {
            "protocol": PROTOCOL,
            **task_identity(task),
            "status": "completed",
            "delivery": text,
            "delivery_sha256": hashlib.sha256(text.encode()).hexdigest(),
        }
        rows.append(row)
    (path / "predictions.jsonl").write_bytes(b"".join(encoded(row) for row in rows))
    return path


@pytest.fixture
def fake_models(monkeypatch):
    requests, responses = [], []
    module = types.ModuleType("dcc_test_judge")

    def factory(_config):
        def call(request):
            requests.append(request)
            return responses.pop(0)

        return call

    module.factory = factory
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return requests, responses, {"use": module.__name__ + ":factory", "model": "custom-local-judge", "api": "chat"}


def test_finre_uses_official_normalization_and_sklearn_aggregation():
    from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score

    from dcc_harness.evaluation.dcc_official import finre

    evaluator = finauditing.finre_evaluator()
    truths = ["Reversal", "Inappropriateness", "CombinationErr", "Reversal", "CombinationErr"]
    predictions = ['```json\n{"label":"reversal"}\n```', "inappropriateness", "combination error", "no valid category", "Reversal then CombinationErr"]
    metrics = [evaluator.score({}, {"gold": {"answer": g}}, p, {}, None) for g, p in zip(truths, predictions, strict=True)]
    result = evaluator.aggregate(metrics, {}, None)["official_metrics"]
    predicted = [finre.normalize_findre_pred(text) for text in predictions]
    assert result["accuracy"] == accuracy_score(truths, predicted) == 0.6
    for key, function in [("macro_precision", precision_score), ("macro_recall", recall_score), ("macro_f1", f1_score)]:
        assert result[key] == function(truths, predicted, labels=finre.LABELS, average="macro", zero_division=0)
    assert result["n_invalid_pred"] == 1
    assert metrics[-1]["predicted_label"] == "Reversal"  # Original priority, not a rewritten parser.


def test_finsm_keeps_ranking_duplicates_and_official_empty_gold_denominator():
    from dcc_harness.evaluation.dcc_official import finsm

    evaluator = finauditing.finsm_evaluator()
    truths = [["A", "B"], [], ["C"]]
    predictions = ['Answer: ["B", "B", "A", "X"]', "invalid", "not a list"]
    metrics = [evaluator.score({}, {"gold": {"answer": repr(g)}}, p, {}, None) for g, p in zip(truths, predictions, strict=True)]
    result = evaluator.aggregate(metrics, {}, None)
    assert metrics[0]["predicted_items"] == ["B", "B", "A", "X"]
    expected = finsm.evaluate_metrics(truths, [finsm.parse_pred_to_list(p) for p in predictions])
    assert result["official_metrics"] == expected
    assert expected["HR@1"] == "50.00%" and expected["R@1"] == "25.00%"
    assert result["n_empty_gold"] == 1 and result["n_empty_predictions"] == 2
    with pytest.raises((ValueError, SyntaxError)):
        evaluator.score({}, {"gold": {"answer": "__import__('os').system('must_not_execute')"}}, "[]", {}, None)


def test_finmr_uses_official_judge_prompt_labels_and_denominators(fake_models, tmp_path, monkeypatch):
    from dcc_harness.evaluation.dcc_official import finmr

    requests, responses, judge = fake_models
    responses.extend(["A", "S", "E", "C", "unparseable"])
    context = EvaluationContext()
    evaluator = finauditing.finmr_evaluator()
    reference = {"extracted_value": "-1,284", "calculated_value": "1284"}
    predictions = ['{"extracted_value":"-1284","calculated_value":"1284"}', "wrong structure", "wrong extraction", "wrong calculation", "unclear"]
    metrics = [evaluator.score({}, {"gold": {"answer": reference}}, p, {"judge": judge}, context) for p in predictions]
    result = evaluator.aggregate(metrics, {}, context)["official_metrics"]
    assert [row["judge_label"] for row in metrics] == ["A", "S", "E", "C", None]
    assert requests[0]["payload"]["messages"][1]["content"] == finmr.get_prompt(reference, predictions[0])
    assert result["ACC(%)"] == 25.0 and result["Parsing success rate(%)"] == 80.0
    assert result["judge_output_unparsed_count"] == 1
    # Run the original full notebook function with a deterministic transport to
    # compare its returned percentages and denominators, without real API calls.
    path = tmp_path / "finmr.jsonl"
    path.write_text("".join(json.dumps({"ground_truth": reference, "prediction": p}) + "\n" for p in predictions))
    labels = iter(["A", "S", "E", "C", "unparseable"])
    monkeypatch.setattr(finmr, "make_openai_client", lambda **kwargs: None)
    monkeypatch.setattr(finmr, "get_response", lambda *args, **kwargs: next(labels))
    expected = finmr.evaluate_finmr_with_judge(str(path), save_judge_jsonl=False)
    for key, value in result.items():
        assert value == expected[key]
    assert context.provenance["models"]["finauditing_finmr.judge"]["custom_model"]


@pytest.mark.parametrize(
    ("answer", "text", "correct"),
    [
        (False, "**Answer: False.**", 1.0),
        (False, "Answer: false.", 0.0),
        (True, "Answer: True. Illegal Operation: B. Problematic Team: A.", 1.0),
        (True, "Answer: True. Illegal Operation: A. Problematic Team: A.", 0.0),
        (False, "The text quotes Answer: False before a later conclusion.", 1.0),
    ],
)
def test_nba_answer_matching_preserves_official_substring_behavior(answer, text, correct):
    evaluator = rulearena_nba.make_evaluator()
    label = {"gold": {"answer": answer, "illegal_operation": "B", "problematic_team": "A"}, "metadata": {"complexity": 0}}
    metric = evaluator.score({}, label, text, {}, EvaluationContext())
    assert metric["accuracy"] == correct and not metric["rule_metrics_enabled"]


def test_nba_rule_extractor_receives_no_gold_and_uses_original_schema(fake_models, tmp_path):
    requests, responses, model = fake_models
    contract = rulearena_nba.extraction_contract()
    names = [field["name"] for field in contract["fields"]]
    responses.append({"answer": True, "applied_rules": {name: name == names[0] for name in names}})
    gold = {"answer": True, "illegal_operation": "A", "problematic_team": "A", "relevant_rules": [names[0], "PRIVATE_GOLD_RULE"]}
    bundle, _tasks = make_bundle(tmp_path, [sample("nba", "rulearena_nba", gold, complexity=1)])
    context = EvaluationContext(options={"bundle": str(bundle)})
    plugin.setup_evaluation(context)
    context.resources["dcc"]["settings"] = {"rulearena_nba": {"rule_extractor": model}}
    metric = plugin.score_task(_tasks[0], "Use the contract length rule. Answer: True. Illegal Operation: A. Problematic Team: A.", context)
    payload = requests[0]["payload"]
    assert "PRIVATE_GOLD_RULE" not in json.dumps(payload)
    assert "PUBLIC_REFERENCE_RULES" in payload["messages"][1]["content"]
    assert len(payload["response_format"]["json_schema"]["schema"]["properties"]["applied_rules"]["required"]) == 55
    result = rulearena_nba.make_evaluator().aggregate([metric], {}, context)
    assert result["rule_metrics"]["problem_wise_precision"] == 1.0
    assert result["rule_metrics"]["problem_wise_recall"] == 0.5
    assert result["rule_metrics"]["gold_ids_outside_extractor_schema"] == ["PRIVATE_GOLD_RULE"]
    assert "1" in result["by_complexity"]


def test_nba_empty_extraction_keeps_undefined_precision_instead_of_zero():
    metrics = [{"accuracy": 1.0, "complexity": 0, "rule_metrics_enabled": True, "applied_rules": [], "relevant_rules": ["unknown_gold"]}]
    with pytest.warns(RuntimeWarning):
        result = rulearena_nba.make_evaluator().aggregate(metrics, {}, EvaluationContext())
    assert result["rule_metrics"]["problem_wise_precision"] is None
    assert result["rule_metrics"]["problem_wise_recall"] == 0.0
    assert result["rule_metrics"]["metric_denominators"]["problem_wise_precision"] == 0
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("recover", [True, False])
def test_nba_extraction_retries_identical_input_and_preserves_failures(fake_models, tmp_path, recover):
    requests, responses, model = fake_models
    names = [field["name"] for field in rulearena_nba.extraction_contract()["fields"]]
    valid = {"answer": False, "applied_rules": {name: False for name in names}}
    responses.extend([{}, valid if recover else {}])
    gold = {"answer": False, "relevant_rules": [names[0]]}
    bundle, tasks = make_bundle(tmp_path, [sample("nba", "rulearena_nba", gold, complexity=0)])
    context = EvaluationContext(options={"bundle": str(bundle), "datasets": {"rulearena_nba": {"rule_extractor": model, "extraction_attempts": 2}}})
    plugin.setup_evaluation(context)
    delivery = "An analysis of the salary-cap rule. Answer: False."
    if recover:
        metric = plugin.score_task(tasks[0], delivery, context)
        assert metric["accuracy"] == 1.0 and metric["extraction_attempts"] == 2
        assert metric["extraction_failures"] == [{"attempt": 1, "error_type": "ExtractionSchemaError"}]
    else:
        with pytest.raises(ModelCallError, match="after 2 attempts"):
            plugin.score_task(tasks[0], delivery, context)
        assert context.provenance["nba_extraction_attempts"]["nba"]["status"] == "failed"
    assert len(requests) == 2 and requests[0] == requests[1]
    assert delivery in requests[0]["payload"]["messages"][1]["content"]


def test_generic_offline_runner_dispatches_all_finauditing_subtasks(tmp_path, fake_models):
    _requests, responses, judge = fake_models
    responses.append("A")
    samples = [
        sample("re", "finauditing_finre", {"answer": "Reversal"}),
        sample("sm", "finauditing_finsm", {"answer": "['us-gaap:Revenues']"}),
        sample("mr", "finauditing_finmr", {"answer": {"extracted_value": "1", "calculated_value": "1"}}),
    ]
    bundle, tasks = make_bundle(tmp_path, samples, "ragflow")
    run = inference_run(tmp_path / "run", tasks, ["Reversal", '["us-gaap:Revenues"]', '{"extracted_value":"1","calculated_value":"1"}'])
    before = (run / "predictions.jsonl").read_bytes()
    report = evaluate_run(
        tasks,
        run_dir=run,
        output_dir=tmp_path / "eval",
        specs=SPECS,
        options={"dcc": {"bundle": str(bundle), "datasets": {"finauditing_finmr": {"judge": judge}}}},
    )
    assert report["complete_split"]
    groups = report["benchmarks"]["dcc"]["aggregate"]["datasets"]
    assert set(groups) == {"finauditing_finre", "finauditing_finsm", "finauditing_finmr"}
    assert groups["finauditing_finmr"]["official_metrics"]["ACC(%)"] == 100.0
    assert groups["finauditing_finre"]["official_metrics"]["accuracy"] == 1.0
    assert groups["finauditing_finsm"]["official_metrics"]["HR@1"] == "100.00%"
    assert (run / "predictions.jsonl").read_bytes() == before
    assert all(task.gold is None for task in tasks)


def test_official_finmr_never_substitutes_a_numeric_proxy(tmp_path):
    gold = {"answer": {"extracted_value": "1", "calculated_value": "1"}}
    bundle, tasks = make_bundle(tmp_path, [sample("mr", "finauditing_finmr", gold)], "ragflow")
    run = inference_run(tmp_path / "run", tasks, [json.dumps(gold["answer"])])
    report = evaluate_run(tasks, run_dir=run, output_dir=tmp_path / "eval", specs=SPECS, options={"dcc": {"bundle": str(bundle)}})
    assert not report["complete_split"] and report["status_counts"] == {"evaluation_error": 1}
    assert report["benchmarks"]["dcc"]["aggregate"] is None


def test_binding_rejects_foreign_inputs_and_label_tampering(tmp_path):
    bundle, tasks = make_bundle(tmp_path, [sample("re", "finauditing_finre", {"answer": "Reversal"})])
    context = EvaluationContext(options={"bundle": str(bundle)})
    plugin.setup_evaluation(context)
    altered = dataclasses.replace(tasks[0], prompt="Changed experiment input")
    with pytest.raises(ValueError, match="input does not match"):
        plugin.score_task(altered, "Reversal", context)
    path = bundle / "private/gold.jsonl"
    path.write_text(path.read_text().replace("Reversal", "CombinationErr"))
    with pytest.raises(ValueError, match="checksum"):
        plugin.setup_evaluation(EvaluationContext(options={"bundle": str(bundle)}))


def test_new_dataset_can_supply_an_evaluator_without_changing_the_runner(tmp_path, monkeypatch):
    module = types.ModuleType("future_dcc_evaluator")

    def factory():
        return Evaluator(
            "future_dataset",
            "custom-v1",
            lambda sample, label, text, opts, ctx: {"custom": float(text == label["gold"]["answer"])},
            lambda metrics, opts, ctx: {"custom_mean": sum(m["custom"] for m in metrics) / len(metrics)},
        )

    module.factory = factory
    monkeypatch.setitem(sys.modules, module.__name__, module)
    bundle, tasks = make_bundle(tmp_path, [sample("future", "future_dataset", {"answer": "YES"})])
    run = inference_run(tmp_path / "run", tasks, ["YES"])
    report = evaluate_run(
        tasks,
        run_dir=run,
        output_dir=tmp_path / "eval",
        specs=SPECS,
        options={"dcc_inline": {"bundle": str(bundle), "evaluators": {"future_dataset": module.__name__ + ":factory"}}},
    )
    assert report["complete_split"]
    assert report["benchmarks"]["dcc_inline"]["aggregate"]["datasets"]["future_dataset"]["custom_mean"] == 1.0


def test_model_transport_error_is_not_a_dataset_prediction_error(monkeypatch, tmp_path):
    import openai

    key = tmp_path / "key"
    key.write_text("PRIVATE_API_KEY_SENTINEL")
    monkeypatch.setenv("DCC_JUDGE_API_KEY_FILE", str(key))
    context = EvaluationContext()

    def broken(**kwargs):
        raise openai.OpenAIError("PRIVATE_API_KEY_SENTINEL")

    monkeypatch.setattr(openai, "OpenAI", broken)
    model = Model({"model": "custom", "base_url": "https://judge.example"}, context, "test", default_model="official")
    with pytest.raises(ModelCallError) as error:
        model("chat", messages=[])
    assert "PRIVATE_API_KEY_SENTINEL" not in str(error.value) + json.dumps(context.provenance)


def test_resource_fingerprint_ignores_generated_pycache():
    from dcc_harness.preprocessing.native import make_plugin

    first = plugin_identity(make_plugin(), phase="evaluation")
    from dcc_harness.evaluation.dcc_official import finmr  # noqa: F401

    second = plugin_identity(make_plugin(), phase="evaluation")
    assert first == second
    assert all("__pycache__" not in str(path) for path in plugin.RESOURCES)
