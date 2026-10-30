"""CLAUSE protocol and native adapter tests; no actual SP inference."""

import json

import pytest

from dcc_harness.data import InputFile, Task
from dcc_harness.datasets.benchmark_plugins import resolve_dataset_plugin, build_inputs
from dcc_harness.datasets.clause import parse_answer, validate_dataset, prompt_text
from dcc_harness.clause_metrics import align, summarize, aggregate_documents


def sample(gold=None):
    return Task(
        "cuad_ambiguity_PRIVATE",
        "clause",
        "test",
        prompt_text("l1"),
        files=(
            InputFile(
                "document.txt", "Payment is due in 30 days. Payment is due in 60 days."
            ),
        ),
        public_metadata={"prompt_variant": "l1"},
        gold=gold,
        metadata={"category": "ambiguity", "source": "CUAD"},
    )


@pytest.mark.parametrize(
    "value",
    [
        "{}",
        '["span"]',
        '[{"text":"x","explanation":"e","category":"ambiguity"}]',
        '[{"text":" ","explanation":"e"}]',
        "```json\n[]\n```",
        "[] trailing",
    ],
)
def test_strict_clause_schema_rejects_nonprotocol_outputs(value):
    with pytest.raises(ValueError):
        parse_answer(sample().public_input(), value)


def test_clause_empty_answer_and_empty_explanation_follow_bundle_schema():
    assert parse_answer(sample().public_input(), "[]") == []
    assert (
        parse_answer(sample().public_input(), '[{"text":"sentence","explanation":""}]')[
            0
        ]["explanation"]
        == ""
    )


def test_clause_and_cuad_have_separate_skills_and_input_contracts(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    clause = resolve_dataset_plugin("clause")
    cuad = resolve_dataset_plugin("cuad")
    assert clause.skills_path != cuad.skills_path
    assert not clause.requires_judge
    assert clause.tools == ()
    inputs = build_inputs(clause, sample().public_input(), "thread")
    text = str(inputs)
    assert "PRIVATE" not in text and "cuad_ambiguity" not in text
    assert "document.txt" in text and "explanation" in text
    assert "contradiction_exists" not in text


def test_clause_rejects_category_named_files_and_mismatched_prompt():
    task = sample()
    import dataclasses

    with pytest.raises(ValueError):
        validate_dataset(
            [dataclasses.replace(task, files=(InputFile("ambiguity.txt", "text"),))]
        )
    with pytest.raises(ValueError):
        validate_dataset([dataclasses.replace(task, prompt="Category is ambiguity")])


@pytest.fixture(scope="module")
def prepared_data():
    import os
    from pathlib import Path

    root = Path(
        os.environ.get(
            "DCC_CLAUSE_TEST_DATA",
            Path(__file__).resolve().parents[1] / ".benchmark-data/clause",
        )
    )
    if not (root / "preparation.json").is_file():
        pytest.skip("prepare the pinned CLAUSE bundle for data integration checks")
    return root


@pytest.fixture(scope="module")
def calculator(prepared_data):
    pytest.importorskip("nltk")
    pytest.importorskip("rouge_score")
    from dcc_harness.clause_metrics import TextMetrics

    return TextMetrics(
        nltk_data=prepared_data / "resources/nltk_data", without_bert=True
    )


@pytest.fixture(scope="module")
def reference(prepared_data, calculator):
    """Load the supplied reference scripts, not a second handwritten implementation."""
    import os
    import tarfile
    import types
    from pathlib import Path
    from dcc_harness.clause_metrics import file_hash, protocol

    archive = os.environ.get("DCC_CLAUSE_TEST_ARCHIVE")
    if not archive:
        pytest.skip(
            "set DCC_CLAUSE_TEST_ARCHIVE to compare against the supplied reference"
        )
    assert file_hash(Path(archive)) == protocol()["bundle_sha256"]
    with tarfile.open(archive, "r:gz") as bundle:
        core_source = (
            bundle.extractfile("CLAUSE_Eval3_InText_ready/tools/metric_core.py")
            .read()
            .decode()
        )
        score_source = (
            bundle.extractfile("CLAUSE_Eval3_InText_ready/tools/score.py")
            .read()
            .decode()
        )
    core = types.ModuleType("clause_reference_core")
    exec(compile(core_source, "bundle/tools/metric_core.py", "exec"), core.__dict__)
    # Remove only the CLI imports that depend on reference file layout. No metric code changes.
    score_source = score_source.replace(
        "from common import ROOT,records,dump,validate_prediction", ""
    )
    score_source = score_source.replace("from metric_core import align", "")
    score = types.ModuleType("clause_reference_score")
    score.ROOT = prepared_data
    score.align = core.align
    exec(compile(score_source, "bundle/tools/score.py", "exec"), score.__dict__)
    return core, score


def gold(*pairs):
    return [
        {
            "perturbation": [
                {
                    "changed_text": a,
                    "contradicted_text": b,
                    "contradiction_exists": "YES",
                    "explanation": "internal conflict",
                }
                for a, b in pairs
            ]
        }
    ]


@pytest.mark.parametrize(
    "pairs,pred",
    [
        ([("Payment is due today.", "Payment is due tomorrow.")], []),
        (
            [("Payment is due today.", "Payment is due tomorrow.")],
            ["Payment is due tomorrow."],
        ),
        (
            [("Payment is due today.", "Payment is due tomorrow.")],
            ["PAYMENT IS DUE TODAY.", "Payment is due today."],
        ),
        (
            [
                ("First clause. Shared sentence.", "Other clause."),
                ("Shared sentence. Second clause.", "Different clause."),
            ],
            ["Shared sentence.", "First clause.", "Unmatched passage."],
        ),
        ([("The rent is 10.", "The rent is 20.")], ["The rent is ten."]),
        ([("The rent is 10.", "The rent is 20.")], ["The rent  is 10."]),
        ([("The rent is 10.", "")], ["The rent is 10."]),
    ],
)
def test_alignment_matches_supplied_gpt_dfs_reference(
    reference, calculator, pairs, pred
):
    core, _ = reference
    gt = gold(*pairs)
    prediction = [{"text": text, "explanation": "fixture"} for text in pred]
    assert align(gt, prediction, calculator.sentence_tokenize) == core.align(
        gt, prediction
    )


@pytest.mark.parametrize(
    "gt,pred",
    [
        ("Payment is due today.", "Payment is due today."),
        ("The company must pay the fee.", "A company shall pay a fee."),
        ("Tenant paid rent.", "The tenant pays the rent."),
        ("A. B. C.", "A. C. B."),
        ("There are no obligations.", "Obligations do exist."),
        (
            "It isn't permitted; Clause 1.2 applies.",
            "It is not permitted. Clause 1.2 applies.",
        ),
    ],
)
def test_real_lexical_metrics_match_reference(reference, calculator, gt, pred):
    _, score = reference
    expected = score.TextMetrics(without_bert=True).score(gt, pred)
    assert calculator.score(gt, pred) == expected


def test_unmatched_other_side_is_not_automatically_added(calculator):
    result = align(
        gold(("Payment is due today.", "Payment is due tomorrow.")),
        [{"text": "Payment is due today.", "explanation": "fixture"}],
        calculator.sentence_tokenize,
    )
    assert result["FN"] == 0
    assert result["groups"][0]["gt_text"] == "Payment is due today."


def test_nullable_metrics_and_category_equal_macro_match_reference(reference):
    _, ref = reference
    docs = []
    for category, score in [
        ("ambiguity", 0.8),
        ("ambiguity", 0.6),
        ("structural", 0.1),
        ("omission", None),
    ]:
        docs.append(
            dict(
                source="CUAD",
                category=category,
                n=1,
                m=1 if score is not None else 0,
                FN=0 if score is not None else 1,
                FP=0,
                miss=0 if score is not None else 1,
                extra=0,
                matched_groups=1 if score is not None else 0,
                text_scores={"rouge-1": score} if score is not None else None,
            )
        )
    assert summarize(docs) == ref.summarize(docs)
    result = aggregate_documents(docs)
    assert result["all_documents_macro"]["text_score_documents"] == 3
    assert result["all_documents_macro"]["text_scores"]["rouge-1"] == 0.5
    assert (
        result["source_five_category_equal_macro"]["CUAD"]["text_scores"]["rouge-1"]
        == 0.4
    )
    assert (
        result["source_five_category_equal_macro"]["CUAD"][
            "text_metric_category_denominators"
        ]["rouge-1"]
        == 2
    )


def test_prepared_actual_splits_have_equal_public_inputs_and_no_gold_in_public(
    prepared_data,
):
    from dcc_harness.data import load_tasks, validate_disjoint

    all_public = []
    for split, count in [("train", 4025), ("validation", 501), ("test", 500)]:
        public = load_tasks(prepared_data / f"public/{split}.jsonl")
        private = load_tasks(prepared_data / f"private/{split}.jsonl")
        assert len(public) == len(private) == count
        validate_dataset(public)
        validate_dataset(private)
        for a, b in zip(public, private, strict=True):
            assert a.gold is None and not a.metadata
            assert a.input_sha256() == b.input_sha256()
            assert (
                a.files[0].name == "document.txt" and a.files[0].text == b.files[0].text
            )
        all_public.extend(public)
    validate_disjoint(all_public)
    assert sum(len(t.gold[0]["perturbation"]) for t in private) == 1809


def inference_fixture(root, tasks, deliveries):
    import hashlib
    from dcc_harness.evaluation.offline import task_identity
    from dcc_harness.evaluation.benchmarks import PROTOCOL

    root.mkdir()
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "protocol": PROTOCOL,
                "phase": "inference",
                "task_inputs": [task_identity(t) for t in tasks],
            }
        )
    )
    with (root / "predictions.jsonl").open("w") as stream:
        for task, text in zip(tasks, deliveries, strict=True):
            stream.write(
                json.dumps(
                    {
                        **task_identity(task),
                        "protocol": PROTOCOL,
                        "status": "completed",
                        "delivery": text,
                        "delivery_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    }
                )
                + "\n"
            )


def test_all_500_empty_predictions_are_valid_but_miss_every_defect(
    tmp_path, prepared_data, calculator
):
    from dcc_harness.data import load_tasks
    from dcc_harness.evaluation.offline import evaluate_run

    tasks = load_tasks(prepared_data / "private/test.jsonl")
    root = tmp_path / "synthetic-empty-deliveries"
    inference_fixture(root, tasks, ["[]"] * len(tasks))
    report = evaluate_run(
        tasks,
        run_dir=root,
        output_dir=tmp_path / "eval",
        options={
            "clause": {
                "resource_root": str(prepared_data / "resources"),
                "without_bert": True,
            }
        },
    )
    assert report["complete_split"]
    aggregate = report["benchmarks"]["clause"]["aggregate"]
    assert aggregate["complete_benchmark_split"] and not aggregate["metrics_complete"]
    stats = aggregate["all_documents_macro"]
    assert (
        stats["documents"],
        stats["ground_truth"],
        stats["FN"],
        stats["FP"],
        stats["miss"],
        stats["extra"],
    ) == (500, 1809, 1809, 0, 1, 0)
    assert stats["text_scores"] == {} and stats["text_score_documents"] == 0
    assert report["evaluation_provenance"]["clause"]["llm_judge_enabled"] is False


def test_default_full_metrics_fail_when_encoder_is_missing(tmp_path, prepared_data):
    pytest.importorskip("nltk")
    from dcc_harness.datasets.clause import setup_evaluation
    from dcc_harness.evaluation.offline import EvaluationContext

    context = EvaluationContext(
        options={
            "resource_root": str(prepared_data / "resources"),
            "bert_model_dir": str(tmp_path / "missing"),
        }
    )
    with pytest.raises((ImportError, ValueError, FileNotFoundError)):
        setup_evaluation(context)
    assert "text_metrics" not in context.resources
    assert (
        context.provenance["metrics_complete"] is True
    )  # requested mode, not a fabricated partial score


def test_changed_archive_rejected_before_preparing_data(tmp_path):
    from dcc_harness.datasets.clause_data import prepare

    archive = tmp_path / "invalid.tar.gz"
    archive.write_bytes(b"not the pinned bundle")
    with pytest.raises(ValueError, match="SHA256"):
        prepare(archive, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_cuad_clause_shared_inference_and_independent_evaluation(
    tmp_path, prepared_data, calculator, monkeypatch
):
    import asyncio
    import dataclasses
    from pathlib import Path
    import deerflow.sp.runtime as native
    from deerflow.config.paths import get_paths
    from dcc_harness.data import load_tasks
    from scripts import (
        infer_sp_benchmarks as infer_cli,
        evaluate_sp_benchmarks as eval_cli,
    )

    clause_task = load_tasks(prepared_data / "private/validation.jsonl")[0]
    cuad_task = Task(
        "cuad-example",
        "cuad",
        "validation",
        "What law governs?",
        document="California law governs.",
        gold=["California law"],
    )
    private = [cuad_task, clause_task]
    public_path, private_path = tmp_path / "public.jsonl", tmp_path / "private.jsonl"
    public_path.write_text(
        "".join(
            json.dumps(dataclasses.replace(t, gold=None, metadata={}).to_dict()) + "\n"
            for t in private
        )
    )
    private_path.write_text("".join(json.dumps(t.to_dict()) + "\n" for t in private))
    seen = []
    issue = clause_task.gold[0]["perturbation"][0]
    clause_answer = json.dumps(
        [
            {
                "text": issue.get("changed_text") or issue["contradicted_text"],
                "explanation": "Synthetic adapter fixture, not a model prediction.",
            }
        ]
    )

    class Graph:
        def __init__(self, config, app):
            self.context, self.app = config["context"], app

        async def astream(self, inputs, **kwargs):
            name = Path(self.app.skills.path).name
            seen.append(name)
            assert "web_search" not in {tool.name for tool in self.app.tools}
            upload = (
                get_paths().sandbox_uploads_dir(
                    self.context["thread_id"], user_id=self.context["user_id"]
                )
                / inputs["uploaded_files"][0]["name"]
            )
            expected = (
                clause_task.files[0].text if name == "clause" else cuad_task.document
            )
            assert upload.read_bytes().decode() == expected
            assert clause_task.id not in str(inputs)
            assert "contradiction_exists" not in str(inputs)
            yield (
                "values",
                {
                    "sp_current_stage": "finished",
                    "sp_last_handler_result": {"next_step": "finish"},
                    "sp_last_run_summary": clause_answer
                    if name == "clause"
                    else '["California law"]',
                },
            )

    monkeypatch.setattr(
        native, "make_sp_agent", lambda config, app_config: Graph(config, app_config)
    )
    monkeypatch.setenv("DCC_MODEL_API_KEY", "fake-no-request")
    args = infer_cli.parser().parse_args(
        [
            "--tasks",
            str(public_path),
            "--run-dir",
            str(tmp_path / "run"),
            "--dataset-source",
            "synthetic-adapter-fixture",
            "--base-url",
            "http://never-called.invalid/v1",
            "--model",
            "fake",
        ]
    )
    assert asyncio.run(infer_cli.main_async(args)) == 0
    assert seen == ["cuad", "clause"]
    monkeypatch.setattr(
        native,
        "make_sp_agent",
        lambda *a, **k: pytest.fail("offline eval must not call SP"),
    )
    options = tmp_path / "options.json"
    options.write_text(
        json.dumps(
            {
                "clause": {
                    "resource_root": str(prepared_data / "resources"),
                    "without_bert": True,
                }
            }
        )
    )
    eval_args = eval_cli.parser().parse_args(
        [
            "--tasks",
            str(private_path),
            "--run-dir",
            str(tmp_path / "run"),
            "--output-dir",
            str(tmp_path / "eval"),
            "--allow-partial",
            "--options",
            str(options),
        ]
    )
    assert eval_cli.main_args(eval_args) == 0
    report = json.loads((tmp_path / "eval/report.json").read_text())
    assert report["benchmarks"]["cuad"]["aggregate"]["micro_f1"] == 1
    clause = report["benchmarks"]["clause"]["aggregate"]
    assert not clause["complete_benchmark_split"] and not clause["metrics_complete"]
    assert clause["all_documents_macro"]["aligned_groups"] > 0
    assert clause["all_documents_macro"]["text_score_documents"] == 1
    # The same one-task inference run cannot be described as the full pinned split.
    eval_args.allow_partial = False
    eval_args.output_dir = tmp_path / "strict-eval"
    assert eval_cli.main_args(eval_args) == 2
    strict = json.loads((eval_args.output_dir / "report.json").read_text())
    assert strict["benchmarks"]["clause"]["status"] == "aggregation_error"
    assert strict["benchmarks"]["clause"]["aggregate"] is None


def test_pinned_l2_prompt_keeps_train_example():
    assert prompt_text("l1") != prompt_text("l2")
    from pathlib import Path

    config = Path(__file__).resolve().parents[1] / "configs/clause"
    provenance = json.loads((config / "prompt_provenance.json").read_text())
    assert provenance["l2_example_split"] == "train"


def test_real_pinned_bert_encoder_through_offline_evaluation(prepared_data, tmp_path):
    import dataclasses
    import hashlib
    import os

    if os.environ.get("DCC_CLAUSE_TEST_BERT") != "1":
        pytest.skip("set DCC_CLAUSE_TEST_BERT=1 to exercise the real local encoder")
    from dcc_harness.clause_metrics import protocol
    from dcc_harness.evaluation.offline import evaluate_run

    sentence = "The tenant must pay rent on the first day of every month."
    document = sentence + " The tenant must pay rent on the last day of every month."
    task = dataclasses.replace(
        sample(
            gold((sentence, "The tenant must pay rent on the last day of every month."))
        ),
        files=(InputFile("document.txt", document),),
        metadata={
            "protocol": protocol()["protocol"],
            "source": "CUAD",
            "category": "inconsistency",
            "input_sha256": hashlib.sha256(document.encode()).hexdigest(),
            "perturbations": 1,
        },
    )
    run = tmp_path / "synthetic-encoder-delivery"
    inference_fixture(
        run,
        [task],
        [json.dumps([{"text": sentence, "explanation": "Synthetic metric fixture."}])],
    )
    report = evaluate_run(
        [task],
        run_dir=run,
        output_dir=tmp_path / "eval",
        allow_partial=True,
        options={
            "clause": {
                "resource_root": str(prepared_data / "resources"),
                "device": "cpu",
                "batch_size": 1,
            }
        },
    )
    result = report["benchmarks"]["clause"]["aggregate"]
    assert result is not None, report
    assert result["metrics_complete"] and not result["complete_benchmark_split"]
    assert len(result["metrics_enabled"]) == 7
    stats = result["all_documents_macro"]
    assert stats["miss"] == stats["extra"] == 0
    metrics = stats["text_scores"]
    assert metrics["bert_score_f1"] == pytest.approx(1.0, abs=0.0001)
    assert metrics["rouge-1"] == metrics["rouge-2"] == metrics["rouge-l"] == 1
    assert result["bert_truncated_groups"] == 0
    provenance = report["evaluation_provenance"]["clause"]
    assert provenance["bert"]["revision"] == protocol()["bert"]["revision"]
    assert provenance["metrics_complete"] and not provenance["llm_judge_enabled"]


def test_encoder_download_checks_pinned_lfs_not_cdn_etag(tmp_path, monkeypatch):
    import hashlib
    import io
    from scripts import dcc_prepare_clause_resources as resources

    payload = b"test encoder resource"
    pin = {
        "repo_id": "test/encoder",
        "revision": "fixed",
        "weights_sha256": hashlib.sha256(payload).hexdigest(),
        "weights_size": len(payload),
    }

    class Response(io.BytesIO):
        headers = {"etag": "0" * 64, "Content-Length": str(len(payload))}

    monkeypatch.setattr(resources, "protocol", lambda: {"bert": pin})
    monkeypatch.setattr(resources, "urlopen", lambda *a, **k: Response(payload))
    monkeypatch.setattr(
        resources,
        "verify_model",
        lambda p: json.loads((p / "MODEL_REVISION.json").read_text()),
    )
    result = resources.download_encoder(tmp_path / "good")
    assert len(result["files"]) == 6
    pin["weights_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="pinned LFS"):
        resources.download_encoder(tmp_path / "bad")
    assert not (tmp_path / "bad/MODEL_REVISION.json").exists()
    assert not (tmp_path / "bad/model.safetensors.download").exists()


def test_inference_plugin_registry_does_not_import_scoring_dependencies():
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(root), str(root / "backend/packages/harness")]
        ),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "from dcc_harness.datasets.benchmark_plugins import resolve_dataset_plugin; import sys; assert resolve_dataset_plugin('clause').name == 'clause'; assert not {'bert_score', 'torch', 'nltk', 'rouge_score'}.intersection(sys.modules)",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_shared_environment_manifest_uses_first_distribution_on_import_path(
    monkeypatch,
):
    from types import SimpleNamespace
    from dcc_harness.evaluation import offline

    distributions = [
        SimpleNamespace(metadata={"Name": "numpy"}, version="1.26.4"),
        SimpleNamespace(metadata={"Name": "numpy"}, version="2.4.4"),
    ]
    monkeypatch.setattr(
        offline.importlib.metadata, "distributions", lambda: distributions
    )
    assert offline.environment_versions()["numpy"] == "1.26.4"
