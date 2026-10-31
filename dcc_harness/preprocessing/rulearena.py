"""Pinned RuleArena NBA inputs: complete scenarios and original CBA rules."""

from __future__ import annotations

import ast
import json
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path

from .loaders import inside
from .models import Asset, Sample
from .pipeline import (
    atomic_json,
    digest,
    document_mode,
    read_jsonl,
    render,
    verified_samples,
)

ROOT = Path(__file__).resolve().parents[2]
SOURCE_SPEC = ROOT / "configs/dcc/sources/rulearena-nba.json"
PROTOCOL = ROOT / "configs/dcc/prompts/inline-protocol.md"
LOADER = "dcc_harness.preprocessing.rulearena:load"
BUILDER = "dcc_harness.preprocessing.rulearena:build_release"
QUESTION = "Decide whether any operation by any team violate the rules:"


def verify_source(source: Path) -> dict:
    expected = json.loads(SOURCE_SPEC.read_text())
    provenance = json.loads((source / "manifest.json").read_text())
    if provenance != expected:
        raise ValueError("RuleArena source manifest differs from the pinned NBA release")
    for entry in expected["files"]:
        if digest(inside(source, entry["path"]).read_bytes()) != entry["sha256"]:
            raise ValueError(f"RuleArena source checksum differs: {entry['path']}")
    return expected


def scenario(row: dict) -> str:
    parts = []
    for title, key in (("Team Situations", "team_situations"), ("Player Situations", "player_situations"), ("Operations", "operations")):
        values = row[key]
        if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError(f"RuleArena {key} must be a list of nonempty strings")
        if key != "player_situations" and not values:
            raise ValueError(f"RuleArena {key} cannot be empty")
        parts.append(title + ":\n" + "\n".join(values))
    return "\n\n".join(parts)


def prompt_layer(source: Path) -> str:
    """Read literal constants only; never import or execute upstream API code."""
    tree = ast.parse((source / "nba/auto_test.py").read_text())
    constants = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"system_prompt", "prompt_template"}
        ):
            constants[node.targets[0].id] = ast.literal_eval(node.value)
    template = constants["prompt_template"]
    for placeholder in ("$reference_rules", "$question", "$example"):
        if template.count(placeholder) != 1:
            raise ValueError("upstream NBA prompt placeholders changed")
    template = template.replace("$reference_rules", "__DCC_RULE_FILES__").replace("$question", "__DCC_COMPLETE_SCENARIO__").replace("$example", "")
    # Escape literal monetary dollars for our strict string.Template composer.
    template = template.replace("$", "$$").replace("__DCC_RULE_FILES__", "${rules}").replace("__DCC_COMPLETE_SCENARIO__", "${document_text}")
    return constants["system_prompt"] + "\n\n" + template.strip() + "\n"


def load(options: dict):
    source = Path(options["source"]).resolve()
    provenance = verify_source(source)
    levels = options.get("levels", [0, 1, 2])
    if not levels or any(type(level) is not int or level not in {0, 1, 2} for level in levels) or len(set(levels)) != len(levels):
        raise ValueError("levels must be a nonempty unique selection of 0, 1, 2")
    limit = options.get("limit_per_level")
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("limit_per_level must be a positive integer")
    for level in levels:
        relative = f"nba/annotated_problems/comp_{level}.json"
        rows = json.loads((source / relative).read_text())
        if len(rows) != provenance["counts"][f"comp_{level}"]:
            raise ValueError("RuleArena source row count differs")
        for index, row in enumerate(rows):
            if limit is not None and index >= limit:
                break
            if type(row["answer"]) is not bool or not isinstance(row["relevant_rules"], list):
                raise ValueError("RuleArena requires original Boolean answers and relevant_rules labels")
            if row["answer"] and not {"illegal_operation", "problematic_team"} <= row.keys():
                raise ValueError("RuleArena violating rows require original operation/team labels")
            yield Sample(
                id=f"rulearena-nba-comp{level}-{index:03d}",
                dataset="rulearena_nba",
                split="test",
                question=QUESTION,
                documents=(Asset("scenario", text=scenario(row), title="Transaction scenario"),),
                rules=(Asset("nba-cba", path=source / "nba/reference_rules.txt", title="NBA Collective Bargaining Agreement reference rules"),),
                gold={key: row[key] for key in ("answer", "illegal_operation", "problematic_team", "relevant_rules") if key in row},
                metadata={
                    "source_file": relative,
                    "source_row_index": index,
                    "source_revision": provenance["revision"],
                    "source_repository": provenance["repository"],
                    "complexity": level,
                    "difficulty_level": level + 1,
                    **{key: row[key] for key in ("n_teams", "n_players", "n_operations")},
                },
            )


def audit_bundle(bundle: Path, source: Path) -> dict:
    provenance = verify_source(source)
    manifest, rows = verified_samples(bundle)
    labels = read_jsonl(bundle / "private/gold.jsonl")
    originals = list(load({"source": str(source)}))
    if document_mode(manifest) != "inline" or len(rows) != len(originals) or len(labels) != len(rows):
        raise ValueError("NBA baseline release requires every original row in inline mode")
    for original, row, label in zip(originals, rows, labels, strict=True):
        if (original.id, original.question, original.split, original.gold) != (row["id"], row["question"], row["split"], label["gold"]):
            raise ValueError("RuleArena sample, question, split or label was changed")
        for role in ("documents", "rules"):
            assets = getattr(original, role)
            if len(assets) != len(row[role]):
                raise ValueError("RuleArena document/rule inventory was changed")
            if any(asset.read_bytes() != inside(bundle, stored["path"]).read_bytes() for asset, stored in zip(assets, row[role], strict=True)):
                raise ValueError("RuleArena scenario or complete reference rules were changed")
    return {
        "rows_checked": len(rows),
        "source_revision": provenance["revision"],
        "complexity_counts": provenance["counts"],
        "scenario_rule_label_parity": True,
        "processors": [],
        "use_example": False,
        "transformations": [
            "Join the original team_situations, player_situations and operations using the official three-section format.",
            "Keep the full CBA file unchanged; separate original answers and relevant_rules from public inputs.",
        ],
    }


def build_release(bundle: Path, source: Path, config: dict, output: Path, *, version: str) -> dict:
    from .releases import (
        copy_bundle,
        freeze_prompts,
        validate_ready_bundle,
        write_release_archive,
    )

    bundle, source, output = Path(bundle).resolve(), Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"release archive already exists: {output}")
    if config.get("loader") != LOADER or config.get("processors") or document_mode(config) != "inline":
        raise ValueError("NBA baseline release requires its loader, inline mode and no processors")
    prompts = config.get("prompts", {})
    layers = prompts.get("layers", [])
    dataset_layers = prompts.get("datasets", {}).get("rulearena_nba", [])
    if (
        len(layers) != 1
        or Path(layers[0]).read_bytes() != PROTOCOL.read_bytes()
        or prompts.get("samples")
        or len(dataset_layers) != 1
        or Path(dataset_layers[0]).read_text() != prompt_layer(source)
    ):
        raise ValueError("NBA baseline release requires the original zero-shot task template and the SP file/FINISH protocol")
    report = audit_bundle(bundle, source)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".dcc-pack-", dir=output.parent) as temporary:
        root = Path(temporary)
        manifest = copy_bundle(bundle, root / "bundle")
        provenance = verify_source(source)
        raw = root / "raw/RuleArenaNBA"
        raw.mkdir(parents=True)
        for entry in provenance["files"]:
            destination = raw / entry["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(inside(source, entry["path"]), destination)
        shutil.copyfile(source / "manifest.json", raw / "manifest.json")
        portable = {
            "loader": LOADER,
            "release_builder": BUILDER,
            "document_mode": "inline",
            "loader_options": {"source": "raw/RuleArenaNBA", "levels": [0, 1, 2]},
            "processors": [],
        }
        portable["prompts"], resolved_prompts = freeze_prompts(root, config)
        atomic_json(root / "config.json", portable)
        resolved = deepcopy(portable)
        resolved["prompts"] = resolved_prompts
        render(root / "bundle", resolved)
        validate_ready_bundle(root / "bundle")
        (root / "README.md").write_text(
            f"# RuleArena NBA for SP DCC\n\nVersion: {version}. All original NBA benchmark rows.\n\n"
            f"Source: https://github.com/SkyRiver-2000/RuleArena/tree/{provenance['revision']}/nba\n"
            "License: MIT; original LICENSE and README are under raw/RuleArenaNBA/.\n\n"
            "The complete scenario stays in the prompt; read the complete CBA rules as a file. "
            "No RAGFlow service, API key or index is required. Original historical salary assumptions and zero-shot instructions are retained.\n\n"
            "True means a violation. Answers, offending operations/teams and relevant_rules remain in bundle/private/gold.jsonl. "
            "Complexity levels are not train/validation/test splits. No source labels, rule IDs or missing salary tables were corrected or supplemented.\n\n"
            "Set DCC_BUNDLE to bundle/ and register dcc_harness.preprocessing.native:make_inline_plugin.\n",
            encoding="utf-8",
        )
        return write_release_archive(
            root,
            output,
            {
                "schema_version": 1,
                "dataset": "RuleArena NBA Transaction",
                "version": version,
                "document_mode": "inline",
                "ragflow_required": False,
                "samples": manifest["samples"],
                "counts": manifest["counts"],
                "processing_audit": report,
            },
        )
