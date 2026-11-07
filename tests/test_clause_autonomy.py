"""Task contracts leave role selection to SP; concurrency is an explicit budget."""

from pathlib import Path
import pytest
from dcc_harness.data import PublicTask, InputFile
from dcc_harness.datasets.clause import prepare_task, prompt_text, create_plugin
from dcc_harness.evaluation.benchmark_runner import Settings
from scripts.infer_sp_benchmarks import parser


@pytest.mark.parametrize("variant", ["l1", "l2"])
def test_clause_preserves_frozen_prompt_and_only_adds_io_contract(variant):
    public = PublicTask(
        "clause",
        prompt_text(variant),
        files=(InputFile("document.txt", "PUBLIC SOURCE"),),
    )
    task = prepare_task(public)
    assert task.startswith(public.prompt)
    assert "/mnt/user-data/uploads/document.txt" in task
    assert "/mnt/user-data/outputs/" in task and "JSON array" in task
    skill = (create_plugin().skills_path / "public/clause/SKILL.md").read_text()
    for forbidden in (
        "reporter",
        "researcher",
        "perception",
        "coder",
        "outline",
        "completion sequence",
        "artifact_content",
        "sp_finish",
        "clause_save_answer",
    ):
        assert forbidden not in (task + skill).lower()
    assert {t.name for t in create_plugin().tools} == {"clause_check_passages"}
    assert "PUBLIC SOURCE" not in task
    root = Path(__file__).resolve().parents[1]
    assert (root / f"configs/clause/{variant}_system.txt").read_bytes() == (
        root / f"configs/clause/source_rebuild/templates/prompts/{variant}_system.txt"
    ).read_bytes()


def test_parallel_budget_default_and_cli_opt_in():
    assert parser().parse_args([]).max_concurrent_subagents == 1
    assert (
        parser()
        .parse_args(["--max-concurrent-subagents", "3"])
        .max_concurrent_subagents
        == 3
    )
    assert (
        Settings(
            config="unused", base_url="unused", model="unused"
        ).max_concurrent_subagents
        == 1
    )


@pytest.mark.parametrize("value", [0, 5, -1, True, 1.5, "3"])
def test_parallel_budget_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="max_concurrent_subagents"):
        Settings(
            config="unused",
            base_url="unused",
            model="unused",
            max_concurrent_subagents=value,
        )


@pytest.mark.parametrize("field", ["central_timeout", "central_retry_timeout"])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True])
def test_central_timeout_validation(field, value):
    with pytest.raises(ValueError, match="central.*timeout"):
        Settings(config="unused", base_url="unused", model="unused", **{field: value})


def test_central_timeout_cli_defaults_and_opt_in():
    assert parser().parse_args([]).central_timeout is None
    args = parser().parse_args(
        ["--central-timeout", "300", "--central-retry-timeout", "180"]
    )
    assert args.central_timeout == 300 and args.central_retry_timeout == 180
