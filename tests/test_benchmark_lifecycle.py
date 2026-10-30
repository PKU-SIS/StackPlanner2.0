"""Contract tests: the SP graph is replaced, never executed in these tests."""

import asyncio
import dataclasses
import json
import sys
import types
from pathlib import Path

import pytest

from dcc_harness.data import Task, PublicTask, InputFile, load_tasks, validate_disjoint
from dcc_harness.datasets.benchmark_plugins import BenchmarkPlugin, stage_files
from dcc_harness.evaluation.offline import evaluate_run


def sample(**kwargs):
    return Task(
        **dict(
            id="PRIVATE_ID",
            benchmark="cuad",
            split="test",
            prompt="Find the clause.",
            document="First sentence.\n\nLast sentence.\n",
            gold=["Last sentence."],
            metadata={"category": "PRIVATE_CATEGORY"},
            **kwargs,
        )
    )


def test_public_view_has_no_identity_gold_or_private_metadata():
    task = sample()
    public = task.public_input()
    assert isinstance(public, PublicTask)
    assert not any(hasattr(public, key) for key in ("id", "gold", "split", "group_id"))
    assert "PRIVATE" not in repr(public)
    assert public.document == task.document


def test_explicit_public_metadata_and_files_are_loaded_without_private_paths(tmp_path):
    (tmp_path / "original-category.txt").write_text("a\r\nb\n", newline="")
    row = dict(
        id="x",
        benchmark="third",
        split="test",
        prompt="read",
        gold="secret",
        metadata={"category": "secret"},
        public_metadata={"answer_columns": ["value"]},
        files=[{"name": "document.txt", "path": "original-category.txt"}],
    )
    path = tmp_path / "tasks.jsonl"
    path.write_text(json.dumps(row))
    public = load_tasks(path)[0].public_input()
    assert public.files == (InputFile("document.txt", "a\r\nb\n"),)
    assert "original-category" not in repr(public) and "secret" not in repr(public)


@pytest.mark.parametrize(
    "name", ["../gold.json", "/tmp/gold", "a/b.txt", ".hidden", "a\\b.txt"]
)
def test_input_filenames_cannot_escape_uploads(name):
    with pytest.raises(ValueError):
        InputFile(name, "text")


def test_group_leakage_is_benchmark_independent():
    a = Task("a", "third", "train", "q", document="version one", group_id="base")
    b = Task("b", "third", "test", "q", document="version two", group_id="base")
    with pytest.raises(ValueError, match="group"):
        validate_disjoint([a, b])


def test_native_file_staging_preserves_full_text_and_only_public_names(
    tmp_path, monkeypatch
):
    from deerflow.config.paths import get_paths
    from deerflow.runtime.user_context import get_effective_user_id

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "state"))
    text = "one\r\n" + "long document\n" * 25000 + "END"
    public = PublicTask("third", "read", files=(InputFile("document.txt", text),))
    inputs = stage_files(public, "thread")
    root = get_paths().sandbox_uploads_dir("thread", user_id=get_effective_user_id())
    assert (root / "document.txt").read_bytes() == text.encode()
    assert inputs == {
        "uploaded_files": [
            {
                "name": "document.txt",
                "path": "/mnt/user-data/uploads/document.txt",
                "size": len(text.encode()),
            }
        ]
    }


def test_runner_never_calls_parser_or_scorer_and_passes_native_configuration(
    tmp_path, monkeypatch
):
    import deerflow.sp.runtime as native
    from dcc_harness.evaluation.benchmark_runner import Settings, run_task
    from deerflow.config.paths import get_paths

    def forbidden(*args, **kwargs):
        raise AssertionError("inference must not parse or score")

    def prepare(public):
        assert isinstance(public, PublicTask) and "PRIVATE" not in repr(public)
        return public.prompt + " Read /mnt/user-data/uploads/document.txt"

    module = types.ModuleType("lifecycle_fixture")
    module.plugin = BenchmarkPlugin(
        "third",
        prepare,
        stage_files,
        tmp_path / "skills",
        parse_answer=forbidden,
        score_task=forbidden,
    )
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "state"))
    seen = {}

    class Graph:
        async def astream(self, inputs, **kwargs):
            seen["inputs"] = inputs
            seen["kwargs"] = kwargs
            yield "custom", {"event": "test"}
            yield (
                "values",
                {
                    "sp_current_stage": "finished",
                    "sp_last_handler_result": {"next_step": "finish"},
                    "sp_last_run_summary": "malformed JSON is still a collected delivery",
                },
            )

    def factory(config, *, app_config):
        seen.update(config=config, app=app_config)
        return Graph()

    monkeypatch.setattr(native, "make_sp_agent", factory)
    task = Task(
        "PRIVATE_ID",
        "third",
        "test",
        "Public task",
        document="PUBLIC BODY",
        gold="PRIVATE_GOLD",
        metadata={"x": "PRIVATE_META"},
    )
    settings = Settings(
        config="configs/stackplanner_benchmarks.yaml",
        base_url="http://unused/v1",
        model="fake",
        temperature=0.2,
        top_p=0.9,
        max_tokens=8192,
        seed=None,
        max_iterations=13,
        dataset_plugins=("lifecycle_fixture:plugin",),
    )
    row = asyncio.run(run_task(task, settings, output_dir=tmp_path / "attempt"))
    assert row["status"] == "completed" and "metrics" not in row and "answer" not in row
    assert row["delivery"].startswith("malformed")
    assert "PRIVATE" not in str(seen["inputs"]) + str(seen["config"])
    model = seen["app"].get_model_config(seen["app"].models[0].name)
    assert (
        model.model,
        model.temperature,
        model.top_p,
        model.max_tokens,
        model.seed,
    ) == ("fake", 0.2, 0.9, 8192, None)
    assert seen["config"]["configurable"]["sp_max_actions"] == 13
    upload = (
        get_paths().sandbox_uploads_dir(row["thread_id"], user_id=row["user_id"])
        / "document.txt"
    )
    assert upload.read_text() == "PUBLIC BODY"
    assert json.loads((tmp_path / "attempt/result.json").read_text()) == row
    assert (tmp_path / "attempt/trace.jsonl").is_file()


def saved_run(tmp_path, tasks, deliveries, statuses=None):
    import hashlib
    from dcc_harness.evaluation.benchmarks import PROTOCOL
    from dcc_harness.evaluation.offline import task_identity

    root = tmp_path / "inference"
    root.mkdir()
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "phase": "inference",
                "protocol": PROTOCOL,
                "task_inputs": [task_identity(t) for t in tasks],
            }
        )
    )
    rows = []
    for i, (task, delivery) in enumerate(zip(tasks, deliveries)):
        status = statuses[i] if statuses else "completed"
        row = {**task_identity(task), "protocol": PROTOCOL, "status": status}
        if status == "completed":
            row.update(
                delivery=delivery,
                delivery_sha256=hashlib.sha256(delivery.encode()).hexdigest(),
            )
        rows.append(row)
    (root / "predictions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return root


def test_real_cuad_evaluation_includes_dataset_aupr_without_sp_or_api(
    tmp_path, monkeypatch
):
    import deerflow.sp.runtime as native

    monkeypatch.setattr(
        native, "make_sp_agent", lambda *a, **kw: pytest.fail("SP must not run")
    )
    from dcc_harness.metrics import evaluate_cuad

    task = sample()
    delivery = '[{"text":"Last sentence.","confidence":0.8}]'
    root = saved_run(tmp_path, [task], [delivery])
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    report = evaluate_run([task], run_dir=root, output_dir=tmp_path / "eval")
    expected = evaluate_cuad({task.id: task.gold}, {task.id: json.loads(delivery)})
    assert report["complete_split"]
    aggregate = report["benchmarks"]["cuad"]["aggregate"]
    assert {k: aggregate[k] for k in expected} == expected
    assert aggregate["task_macro"]["f1"] == 1
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before
    assert json.loads((tmp_path / "eval/manifest.json").read_text())["judge"] is None
    again = evaluate_run([task], run_dir=root, output_dir=tmp_path / "eval-again")
    assert report == again


@pytest.mark.parametrize("mode", ["missing", "failed", "invalid"])
def test_strict_eval_refuses_incomplete_predictions_without_scores(tmp_path, mode):
    tasks = [sample(), dataclasses.replace(sample(), id="second")]
    deliveries = ['["Last sentence."]', "not JSON" if mode == "invalid" else "[]"]
    root = saved_run(
        tmp_path,
        tasks,
        deliveries[:1] if mode == "missing" else deliveries,
        statuses=["completed", "unfinished"] if mode == "failed" else None,
    )
    report = evaluate_run(tasks, run_dir=root, output_dir=tmp_path / "eval")
    assert report["status"] == "preflight_failed" and not report["complete_split"]
    assert report["benchmarks"] == {} and not (tmp_path / "eval/scores.jsonl").exists()
    partial = evaluate_run(
        tasks, run_dir=root, output_dir=tmp_path / "diagnostic", allow_partial=True
    )
    assert partial["diagnostic_partial"] and not partial["complete_split"]
    assert partial["benchmarks"]["cuad"]["scored_tasks"] == 1


def test_valid_empty_prediction_is_scored_not_a_failure(tmp_path):
    task = sample()
    root = saved_run(tmp_path, [task], ["[]"])
    report = evaluate_run([task], run_dir=root, output_dir=tmp_path / "eval")
    assert report["complete_split"]
    assert report["benchmarks"]["cuad"]["aggregate"]["micro_recall"] == 0


@pytest.mark.parametrize(
    "damage", ["duplicate", "foreign", "input", "delivery", "subset", "split"]
)
def test_eval_rejects_mixed_changed_or_incomplete_task_identity(tmp_path, damage):
    tasks = [sample(), dataclasses.replace(sample(), id="second")]
    root = saved_run(tmp_path, tasks, ["[]", "[]"])
    rows = [
        json.loads(line)
        for line in (root / "predictions.jsonl").read_text().splitlines()
    ]
    if damage == "duplicate":
        rows.append(rows[0])
    elif damage == "foreign":
        rows[0]["task_id"] = "foreign"
    elif damage == "delivery":
        rows[0]["delivery"] = '["changed"]'
    elif damage == "split":
        rows[0]["split"] = "train"
    elif damage == "input":
        tasks[0] = dataclasses.replace(tasks[0], document="different contract")
    else:
        tasks = tasks[:1]
    (root / "predictions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    with pytest.raises(ValueError):
        evaluate_run(tasks, run_dir=root, output_dir=tmp_path / "eval")


def custom_plugin(tmp_path, monkeypatch, *, score_error=False, aggregate_error=False):
    from dcc_harness.evaluation.offline import macro_average

    called = []

    def parse(public, text):
        assert isinstance(public, PublicTask) and not hasattr(public, "gold")
        return json.loads(text)

    def setup(context):
        called.append("setup")
        context.resources["model"] = object()

    def score(task, text, context):
        called.append("score")
        assert context.resources["model"] is not None
        assert task.metadata["category"] == "private"
        if score_error:
            raise ValueError("scorer failure, not invalid model output")
        return {
            "similarity": None if json.loads(text) == [] else context.options["scale"],
            "miss": 1.0 if json.loads(text) == [] else 0.0,
        }

    def aggregate(tasks, rows, context):
        called.append("aggregate")
        if aggregate_error:
            raise ValueError("aggregate failed")
        assert all(t.gold == "private label" for t in tasks)
        return {"custom_report": macro_average(rows, ("similarity", "miss"))}

    def validate(tasks):
        called.append("validate")
        assert all(t.group_id for t in tasks)

    module = types.ModuleType("custom_eval_fixture")
    module.plugin = BenchmarkPlugin(
        "third",
        lambda t: t.prompt,
        stage_files,
        tmp_path / "skills",
        parse_answer=parse,
        score_task=score,
        aggregate=aggregate,
        setup_evaluation=setup,
        validate_dataset=validate,
        evaluation_version="fixture-v1",
    )
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return ("custom_eval_fixture:plugin",), called


def test_custom_eval_setup_options_private_metadata_and_null_denominators(
    tmp_path, monkeypatch
):
    specs, called = custom_plugin(tmp_path, monkeypatch)
    tasks = [
        Task(
            str(i),
            "third",
            "test",
            "public",
            gold="private label",
            metadata={"category": "private"},
            group_id=f"g{i}",
        )
        for i in range(2)
    ]
    root = saved_run(tmp_path, tasks, ['["match"]', "[]"])
    report = evaluate_run(
        tasks,
        run_dir=root,
        output_dir=tmp_path / "eval",
        specs=specs,
        options={"third": {"scale": 0.8}},
    )
    result = report["benchmarks"]["third"]["aggregate"]["custom_report"]
    assert result == {
        "similarity": 0.8,
        "miss": 0.5,
        "metric_denominators": {"similarity": 1, "miss": 2},
    }
    assert called == ["validate", "setup", "score", "score", "aggregate"]


@pytest.mark.parametrize("aggregate_error", [False, True])
def test_evaluation_errors_are_separate_and_never_change_inference(
    tmp_path, monkeypatch, aggregate_error
):
    specs, called = custom_plugin(
        tmp_path,
        monkeypatch,
        score_error=not aggregate_error,
        aggregate_error=aggregate_error,
    )
    task = Task(
        "a",
        "third",
        "test",
        "public",
        gold="private label",
        metadata={"category": "private"},
        group_id="g",
    )
    root = saved_run(tmp_path, [task], ['["match"]'])
    before = (root / "predictions.jsonl").read_bytes()
    report = evaluate_run(
        [task],
        run_dir=root,
        output_dir=tmp_path / "eval",
        specs=specs,
        options={"third": {"scale": 0.8}},
    )
    assert report["status"] == "evaluation_failed"
    assert report["benchmarks"]["third"]["aggregate"] is None
    row = json.loads((tmp_path / "eval/scores.jsonl").read_text())
    assert row["status"] == ("scored" if aggregate_error else "evaluation_error")
    assert (root / "predictions.jsonl").read_bytes() == before


def test_offline_cli_needs_no_inference_config_or_model_credentials(
    tmp_path, monkeypatch
):
    from scripts import evaluate_sp_benchmarks as cli

    monkeypatch.delenv("DCC_MODEL_API_KEY", raising=False)
    task = sample()
    root = saved_run(tmp_path, [task], ['["Last sentence."]'])
    path = tmp_path / "tasks.jsonl"
    path.write_text(json.dumps(task.to_dict()))
    args = cli.parser().parse_args(
        [
            "--tasks",
            str(path),
            "--run-dir",
            str(root),
            "--output-dir",
            str(tmp_path / "eval"),
        ]
    )
    assert cli.main_args(args) == 0


def test_input_plugin_cannot_inject_native_finish_or_override_messages(
    tmp_path, monkeypatch
):
    from dcc_harness.datasets.benchmark_plugins import build_inputs

    plugin = BenchmarkPlugin(
        "third",
        lambda t: t.prompt,
        lambda t, thread: {"sp_current_stage": "finished"},
        tmp_path,
    )
    with pytest.raises(ValueError, match="only return uploaded_files"):
        build_inputs(plugin, PublicTask("third", "task"), "thread")


def test_infer_and_eval_cli_round_trip_selected_artifact_and_native_file_access(
    tmp_path, monkeypatch
):
    import hashlib
    import deerflow.sp.runtime as native
    from deerflow.config.paths import get_paths
    from deerflow.sandbox.sandbox_provider import (
        get_sandbox_provider,
        reset_sandbox_provider,
    )
    from scripts import (
        infer_sp_benchmarks as infer_cli,
        evaluate_sp_benchmarks as eval_cli,
    )

    task = sample()
    tasks_path = tmp_path / "tasks.jsonl"
    tasks_path.write_text(json.dumps(task.to_dict()))
    monkeypatch.setenv("DCC_MODEL_API_KEY", "TEST_ONLY_NO_REQUEST")
    seen = []

    class Graph:
        def __init__(self, config, app):
            self.context = config["context"]
            assert "read_file" in {tool.name for tool in app.tools}
            assert Path(app.skills.path).name == "cuad"

        async def astream(self, inputs, **kwargs):
            context = self.context
            provider = get_sandbox_provider()
            sid = provider.acquire(context["thread_id"], user_id=context["user_id"])
            sandbox = provider.get(sid)
            seen.append(sandbox.read_file(inputs["uploaded_files"][0]["path"]))
            assert "PRIVATE" not in str(inputs)
            output = get_paths().sandbox_outputs_dir(
                context["thread_id"], user_id=context["user_id"]
            )
            output.mkdir(parents=True, exist_ok=True)
            body = '["Last sentence."]'
            (output / "answer.json").write_text(body)
            yield (
                "values",
                {
                    "sp_current_stage": "finished",
                    "sp_last_handler_result": {"next_step": "finish"},
                    "sp_last_run_summary": "this summary must NOT be scored",
                    "thread_data": {"outputs_path": str(output)},
                    "sp_last_final_artifact_ref": "selected",
                    "sp_current_artifact_refs": {
                        "generated_file": {
                            "artifact_id": "selected",
                            "thread_id": context["thread_id"],
                            "run_id": context["run_id"],
                            "virtual_path": "/mnt/user-data/outputs/answer.json",
                            "content_hash": hashlib.sha256(body.encode()).hexdigest(),
                            "metadata": {"completion_status": "complete"},
                        }
                    },
                },
            )

    monkeypatch.setattr(
        native, "make_sp_agent", lambda config, app_config: Graph(config, app_config)
    )
    args = infer_cli.parser().parse_args(
        [
            "--tasks",
            str(tasks_path),
            "--run-dir",
            str(tmp_path / "run"),
            "--dataset-source",
            "fixture",
            "--base-url",
            "http://never-called.invalid/v1",
            "--model",
            "fake",
            "--temperature",
            "0.2",
            "--top-p",
            "0.95",
            "--max-tokens",
            "8192",
        ]
    )
    try:
        assert asyncio.run(infer_cli.main_async(args)) == 0
        args.resume = True
        assert asyncio.run(infer_cli.main_async(args)) == 0
        assert seen == [task.document]
        assert (
            eval_cli.main_args(
                eval_cli.parser().parse_args(
                    [
                        "--tasks",
                        str(tasks_path),
                        "--run-dir",
                        str(tmp_path / "run"),
                        "--output-dir",
                        str(tmp_path / "scores"),
                    ]
                )
            )
            == 0
        )
        report = json.loads((tmp_path / "scores/report.json").read_text())
        assert report["benchmarks"]["cuad"]["aggregate"]["micro_f1"] == 1
        inference = json.loads((tmp_path / "run/predictions.jsonl").read_text())
        assert (
            inference["delivery"] == '["Last sentence."]' and "metrics" not in inference
        )
    finally:
        reset_sandbox_provider()


def test_input_error_is_recorded_without_constructing_graph(tmp_path, monkeypatch):
    from dcc_harness.evaluation.benchmark_runner import Settings, run_task
    import deerflow.sp.runtime as native

    monkeypatch.setattr(
        native,
        "make_sp_agent",
        lambda *a, **kw: pytest.fail("bad input must not call SP"),
    )
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "state"))
    row = asyncio.run(
        run_task(
            dataclasses.replace(sample(), prompt="x" * 12001),
            Settings(
                config="configs/stackplanner_benchmarks.yaml",
                base_url="http://unused",
                model="fake",
            ),
            output_dir=tmp_path / "attempt",
        )
    )
    assert row["status"] == "input_error"
    assert (tmp_path / "attempt/result.json").exists()


def test_multi_benchmark_selection_uses_corresponding_scorer_and_judge_policy(tmp_path):
    wide = Task(
        "wide",
        "widesearch",
        "test",
        "List names",
        gold=[{"name": "A"}],
        public_metadata={"answer_columns": ["name"]},
        metadata={
            "evaluation": {
                "required": ["name"],
                "unique_columns": ["name"],
                "eval_pipeline": {"name": {"metric": ["exact_match"]}},
            }
        },
    )
    tasks = [sample(), wide]
    root = saved_run(tmp_path, tasks, ['["Last sentence."]', '[{"name":"A"}]'])
    # CUAD-only evaluation never asks for a judge, even in a mixed inference run.
    report = evaluate_run(
        tasks, run_dir=root, output_dir=tmp_path / "cuad", benchmark="cuad"
    )
    assert set(report["benchmarks"]) == {"cuad"}
    with pytest.raises(ValueError, match="requires a judge"):
        evaluate_run(tasks, run_dir=root, output_dir=tmp_path / "needs-judge")
    report = evaluate_run(
        tasks, run_dir=root, output_dir=tmp_path / "both", exact_proxy=True
    )
    assert set(report["benchmarks"]) == {"cuad", "widesearch"}
    assert report["benchmarks"]["widesearch"]["aggregate"]["f1_by_row"] == 1
    assert (
        report["benchmarks"]["widesearch"]["aggregate"]["evaluation_protocol"]
        == "strict_exact_proxy"
    )


def test_resource_content_changes_evaluation_fingerprint(tmp_path, monkeypatch):
    from dcc_harness.evaluation.offline import plugin_identity

    specs, _ = custom_plugin(tmp_path, monkeypatch)
    resource = tmp_path / "tokenizer.dat"
    resource.write_text("version1")
    module = sys.modules["custom_eval_fixture"]
    plugin = dataclasses.replace(module.plugin, evaluation_resources=(resource,))
    before = plugin_identity(plugin, phase="evaluation")
    resource.write_text("version2")
    assert before != plugin_identity(plugin, phase="evaluation")


def test_missing_evaluation_hooks_do_not_prevent_inference_but_block_eval(
    tmp_path, monkeypatch
):
    module = types.ModuleType("inference_only_fixture")
    module.plugin = BenchmarkPlugin("third", lambda t: t.prompt, stage_files, tmp_path)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    task = Task("a", "third", "test", "public")
    root = saved_run(tmp_path, [task], ["raw delivery"])
    with pytest.raises(
        ValueError, match="requires parse_answer, score_task, aggregate"
    ):
        evaluate_run(
            [task],
            run_dir=root,
            output_dir=tmp_path / "eval",
            specs=("inference_only_fixture:plugin",),
        )


@pytest.mark.parametrize("budget", [0, 1, 21, 100])
def test_action_budget_cannot_be_silently_clamped_by_native_sp(budget):
    from dcc_harness.evaluation.benchmark_runner import Settings

    with pytest.raises(ValueError, match="native SP action range"):
        Settings(
            config="unused", base_url="unused", model="unused", max_iterations=budget
        )


def test_nonfinite_parser_result_is_a_serializable_preflight_failure(
    tmp_path, monkeypatch
):
    specs, _ = custom_plugin(tmp_path, monkeypatch)
    module = sys.modules["custom_eval_fixture"]
    module.plugin = dataclasses.replace(
        module.plugin, parse_answer=lambda task, text: float("nan")
    )
    task = Task("a", "third", "test", "public", group_id="g")
    root = saved_run(tmp_path, [task], ["anything"])
    report = evaluate_run(
        [task], run_dir=root, output_dir=tmp_path / "eval", specs=specs
    )
    assert report["status"] == "preflight_failed"
    assert report["preflight_failures"][0]["status"] == "invalid_prediction"
    assert "answer" not in report["preflight_failures"][0]
