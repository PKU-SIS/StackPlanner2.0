"""Independent partitions must preserve inputs and keep scenario families together."""

from copy import deepcopy
from pathlib import Path

import pytest

from dcc_harness.data import load_tasks, validate_disjoint
from dcc_harness.preprocessing.models import Asset, Sample
from dcc_harness.preprocessing.pipeline import prepare, read_jsonl, render
from dcc_harness.preprocessing.releases import validate_ready_bundle
from dcc_harness.preprocessing.splits import (
    export_splits,
    make_definition,
    nba_context_group,
)


def fixture_bundle(tmp_path):
    samples = []
    for level, count in enumerate([12, 10, 8]):
        for index in range(count):
            background = 0 if index < 2 else index
            text = f"Team Situations:\nTeam A {level}-{background}.\n\nPlayer Situations:\nPlayer A {background}.\n\nOperations:\nOperation {index}."
            positive = index != 0 if level == 2 else index % 3 != 0
            samples.append(
                Sample(
                    f"nba-{level}-{index}",
                    "rulearena_nba",
                    "test",
                    "Apply the original rules.",
                    (Asset("scenario", text=text),),
                    (Asset("cba", text="COMPLETE ORIGINAL RULES"),),
                    gold={"answer": positive, **({"illegal_operation": "A", "problematic_team": "A"} if positive else {})},
                    metadata={"complexity": level, "source_revision": "fixture-v1"},
                )
            )
    cfg = {"document_mode": "inline", "prompts": {"layers": ["literal:${question}\n${document_text}\n${rules}"]}}
    bundle = tmp_path / "original"
    prepare(cfg, bundle, samples=samples)
    render(bundle, cfg)
    return bundle


def contents(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_seeded_grouped_split_is_exhaustive_stratified_and_reproducible(tmp_path):
    bundle = fixture_bundle(tmp_path)
    definition = make_definition(bundle)
    assert definition == make_definition(bundle)
    assert definition["partitions"] != make_definition(bundle, seed=43)["partitions"]
    train, test = map(set, (definition["partitions"]["train"], definition["partitions"]["test"]))
    assert len(train) == 24 and len(test) == 6 and not train & test
    assert train | test == {row["id"] for row in read_jsonl(bundle / "public/samples.jsonl")}
    for level in range(3):
        assert (f"nba-{level}-0" in train) == (f"nba-{level}-1" in train)
    assert "nba-2-0" in train  # The sole rare negative cannot appear in both partitions.


def test_export_creates_separate_usable_bundles_without_mutating_baseline(tmp_path):
    bundle = fixture_bundle(tmp_path)
    before = contents(bundle)
    definition = make_definition(bundle)
    output = tmp_path / "separate-split"
    export_splits(bundle, definition, output)
    original = {t.id: t for t in load_tasks(bundle / "public/tasks.jsonl")}
    groups = {}
    for split in ["train", "test"]:
        target = output / split
        _, samples = validate_ready_bundle(target)
        tasks = load_tasks(target / "public/tasks.jsonl")
        assert {t.id for t in tasks} == set(definition["partitions"][split])
        assert all(t.split == split and t.input_sha256() == original[t.id].input_sha256() for t in tasks)
        assert all(t.group_id and not hasattr(t.public_input(), "group_id") for t in tasks)
        assert all(r["split"] == split for r in read_jsonl(target / "private/gold.jsonl"))
        from dcc_harness.evaluation.dcc import plugin
        from dcc_harness.evaluation.offline import EvaluationContext

        context = EvaluationContext(options={"bundle": str(target)})
        plugin.setup_evaluation(context)
        truth = read_jsonl(target / "private/gold.jsonl")[0]["gold"]["answer"]
        answer = "Answer: True. Illegal Operation: A. Problematic Team: A." if truth else "Answer: False."
        assert plugin.score_task(tasks[0], answer, context)["accuracy"] == 1.0
        groups[split] = {nba_context_group(s, target) for s in samples}
        assert {p.name for p in (target / "public/documents").iterdir()} == {Path(s["documents"][0]["path"]).name for s in samples}
    assert not groups["train"] & groups["test"]
    validate_disjoint(*(load_tasks(output / split / "public/tasks.jsonl") for split in ["train", "test"]))
    assert contents(bundle) == before
    with pytest.raises(FileExistsError):
        export_splits(bundle, definition, output)


@pytest.mark.parametrize("damage", ["overlap", "binding", "missing"])
def test_export_rejects_changed_definition_before_publishing(tmp_path, damage):
    bundle = fixture_bundle(tmp_path)
    definition = deepcopy(make_definition(bundle))
    if damage == "overlap":
        definition["partitions"]["test"].append(definition["partitions"]["train"][0])
    elif damage == "binding":
        definition["source_bundle"]["gold_sha256"] = "changed"
    else:
        definition["partitions"]["train"].pop()
    output = tmp_path / "must-not-exist"
    with pytest.raises(ValueError):
        export_splits(bundle, definition, output)
    assert not output.exists()


def test_export_refuses_to_write_inside_active_source_bundle(tmp_path):
    bundle = fixture_bundle(tmp_path)
    before = contents(bundle)
    with pytest.raises(ValueError, match="source bundle"):
        export_splits(bundle, make_definition(bundle), bundle / "new-split")
    assert contents(bundle) == before
