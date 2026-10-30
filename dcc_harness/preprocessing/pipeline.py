"""Immutable normalized bundles and an explicit, public task rendering phase."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter
from importlib import import_module
from pathlib import Path

from . import loaders
from .models import Asset, Sample
from .prompts import DEFAULT_LAYERS, compose


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".dcc-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def extension(spec: str, builtins: dict):
    if spec in builtins:
        return builtins[spec]
    module, separator, name = spec.partition(":")
    if not separator or not module or not name:
        raise ValueError("extension must use a built-in name or trusted module:function")
    function = getattr(import_module(module), name)
    if not callable(function):
        raise TypeError(f"extension is not callable: {spec}")
    return function


def _store(root: Path, role: str, asset: Asset) -> dict:
    content = asset.read_bytes()
    if not content.strip():
        raise ValueError(f"empty {role} asset: {asset.id}")
    suffix = Path(asset.path).suffix.lower() if asset.path else ".txt"
    if not suffix or len(suffix) > 12 or not suffix[1:].isalnum():
        suffix = ".bin"
    sha256 = digest(content)
    reference = f"public/{role}/{sha256}{suffix}"
    path = root / reference
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(content)
    return {"id": asset.id, "title": asset.title or asset.id, "path": reference, "sha256": sha256, "bytes": len(content)}


def prepare(config: dict, output: Path, *, samples=None, limit: int | None = None) -> dict:
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"bundle already exists; use a new output directory: {output}")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    if samples is None:
        loader = extension(config.get("loader", "manifest"), {"manifest": loaders.manifest, "finauditing": loaders.finauditing})
        samples = loader(config.get("loader_options", {}))
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".dcc-prepare-", dir=output.parent))
    try:
        (staging / "public").mkdir()
        (staging / "private").mkdir(mode=0o700)
        seen, counts, document_hashes, rule_hashes = set(), Counter(), set(), set()
        with (staging / "public/samples.jsonl").open("wb") as public, (staging / "private/gold.jsonl").open("wb") as gold:
            for item in samples:
                for processor in config.get("processors", []):
                    item = extension(processor["use"], {})(item, processor.get("options", {}))
                if not isinstance(item, Sample):
                    raise TypeError("loader/processors must yield Sample objects")
                if item.id in seen:
                    raise ValueError(f"duplicate sample id: {item.id}")
                seen.add(item.id)
                documents = [_store(staging, "documents", asset) for asset in item.documents]
                rules = [_store(staging, "rules", asset) for asset in item.rules]
                document_hashes.update(doc["sha256"] for doc in documents)
                rule_hashes.update(rule["sha256"] for rule in rules)
                public.write(
                    encoded(
                        {
                            "id": item.id,
                            "dataset": item.dataset,
                            "split": item.split,
                            "question": item.question,
                            "documents": documents,
                            "rules": rules,
                            "public_metadata": item.public_metadata,
                        }
                    )
                )
                gold.write(encoded({"id": item.id, "dataset": item.dataset, "split": item.split, "gold": item.gold, "metadata": item.metadata}))
                counts[f"{item.dataset}/{item.split}"] += 1
                if limit is not None and len(seen) >= limit:
                    break
        if not seen:
            raise ValueError("loader produced no samples")
        result = {
            "schema_version": 1,
            "samples": len(seen),
            "counts": dict(counts),
            "unique_documents": len(document_hashes),
            "unique_rules": len(rule_hashes),
            "public_sha256": digest((staging / "public/samples.jsonl").read_bytes()),
            "gold_sha256": digest((staging / "private/gold.jsonl").read_bytes()),
            "loader": config.get("loader", "manifest"),
            "loader_configuration_sha256": digest(encoded({key: config.get(key) for key in ("loader", "loader_options", "processors")})),
            "limit": limit,
        }
        atomic_json(staging / "manifest.json", result)
        os.rename(staging, output)
        return result
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def verified_samples(bundle: Path) -> tuple[dict, list[dict]]:
    manifest = json.loads((bundle / "manifest.json").read_text())
    data = (bundle / "public/samples.jsonl").read_bytes()
    if digest(data) != manifest["public_sha256"]:
        raise ValueError("public samples changed after preparation; create a new bundle")
    samples = read_jsonl(bundle / "public/samples.jsonl")
    for item in samples:
        for asset in item["documents"] + item["rules"]:
            if digest(loaders.inside(bundle, asset["path"]).read_bytes()) != asset["sha256"]:
                raise ValueError(f"asset content changed: {asset['path']}")
    return manifest, samples


def render(bundle: Path, config: dict) -> list[dict]:
    from .ragflow import RagflowError, index_identity, validate_sample_scopes

    bundle = Path(bundle).resolve()
    manifest, samples = verified_samples(bundle)
    state_path = bundle / "index.json"
    if not state_path.exists():
        raise RagflowError("index is not ready; run index first")
    state = json.loads(state_path.read_text())
    if not state.get("ready") or state["public_sha256"] != manifest["public_sha256"]:
        raise RagflowError("index is not ready for this bundle")
    if "ragflow" in config and state["configuration_sha256"] != digest(encoded(index_identity(manifest, config["ragflow"]))):
        raise ValueError("render configuration differs from the completed index configuration")
    validate_sample_scopes(state, [item["id"] for item in samples])
    tasks = []
    # Dataset layers append after common defaults unless an explicit layer list
    # replaces them. Sample layers are keyed by a public sample id.
    prompt_config = config.get("prompts", {})
    for item in samples:
        key = digest(item["id"].encode())[:24]
        context_ref = f"public/contexts/{key}.json"
        scope = state["scopes"][item["id"]]
        context = {
            "schema_version": 2,
            "retrieval": {**scope, "base_url": state["base_url"]},
            "documents": [{"id": f"document-{index}", "title": doc["title"], "sha256": doc["sha256"]} for index, doc in enumerate(item["documents"])],
            "rules": [
                {"file": f"dcc-rule-{index}{Path(rule['path']).suffix}", "title": rule["title"], "sha256": rule["sha256"]}
                for index, rule in enumerate(item["rules"])
            ],
        }
        files = []
        for rule, public_rule in zip(item["rules"], context["rules"], strict=True):
            try:
                loaders.inside(bundle, rule["path"]).read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                raise ValueError("SP rule files must be UTF-8 text; convert binary rules with a processor") from None
            files.append({"name": public_rule["file"], "path": Path(rule["path"]).relative_to("public").as_posix()})
        atomic_json(bundle / context_ref, context)
        files.append({"name": "dcc-context.json", "path": Path(context_ref).relative_to("public").as_posix()})
        rules = (
            "\n".join(f"- {rule['title']}: /mnt/user-data/uploads/dcc-rule-{index}{Path(rule['path']).suffix}" for index, rule in enumerate(item["rules"]))
            or "No separate rule files supplied."
        )
        variables = {
            "question": item["question"],
            "rules": rules,
            "documents": json.dumps(context["documents"], ensure_ascii=False),
            "sample_metadata": json.dumps(item["public_metadata"], ensure_ascii=False),
            "context_file": "/mnt/user-data/uploads/dcc-context.json",
        }
        layers = [
            *prompt_config.get("layers", DEFAULT_LAYERS),
            *prompt_config.get("datasets", {}).get(item["dataset"], []),
            *prompt_config.get("samples", {}).get(item["id"], []),
        ]
        prompt = compose(layers, variables)
        tasks.append(
            {
                "id": item["id"],
                "benchmark": "dcc",
                "split": item["split"],
                "prompt": prompt,
                "files": files,
                "metadata": {"dcc": {"dataset": item["dataset"], "context_file": context_ref, "context_sha256": digest(encoded(context))}},
            }
        )
    tasks_path = bundle / "public/tasks.jsonl"
    descriptor, temporary = tempfile.mkstemp(prefix=".tasks-", dir=tasks_path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            for task in tasks:
                handle.write(encoded(task))
        os.replace(temporary, tasks_path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return tasks
