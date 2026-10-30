"""Portable, verified dataset archives distributed as GitHub Release assets."""

from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from copy import deepcopy
from pathlib import Path, PurePosixPath

from .loaders import finauditing, inside
from .pipeline import atomic_json, digest, encoded, read_jsonl, render, verified_samples
from .prompts import DEFAULT_LAYERS
from .ragflow import index_identity, validate_sample_scopes


def validate_ready_bundle(bundle: Path) -> tuple[dict, list[dict]]:
    manifest, samples = verified_samples(bundle)
    gold_path = bundle / "private/gold.jsonl"
    if digest(gold_path.read_bytes()) != manifest["gold_sha256"]:
        raise ValueError("private labels differ from the prepared manifest")
    ids = [sample["id"] for sample in samples]
    gold = read_jsonl(gold_path)
    tasks = read_jsonl(bundle / "public/tasks.jsonl")
    if [row["id"] for row in gold] != ids or [row["id"] for row in tasks] != ids or len(set(ids)) != manifest["samples"]:
        raise ValueError("sample, label, and rendered task inventories differ")
    state = json.loads((bundle / "index.json").read_text())
    if not state.get("ready") or state.get("public_sha256") != manifest["public_sha256"]:
        raise ValueError("release requires a completed index for the whole bundle")
    validate_sample_scopes(state, ids)
    for sample, task in zip(samples, tasks, strict=True):
        scope = state["scopes"][sample["id"]]
        ledger = state["datasets"][sample["id"]]
        expected = {ledger["documents"][asset["path"]]["id"] for asset in sample["documents"]}
        if ledger["id"] != scope["dataset_ids"][0] or expected != set(scope["document_ids"]):
            raise ValueError("release retrieval scope differs from its document ledger")
        if any(doc.get("run") != "DONE" or not doc.get("chunk_count") for doc in ledger["documents"].values()):
            raise ValueError("release contains unfinished document parsing")
        context = task["metadata"]["dcc"]
        content = inside(bundle, context["context_file"]).read_bytes()
        if digest(content) != context["context_sha256"]:
            raise ValueError("rendered context checksum differs")
        if json.loads(content)["retrieval"] != {**scope, "base_url": state["base_url"]}:
            raise ValueError("rendered context differs from its sample scope")
        for reference in task["files"]:
            inside(bundle / "public", reference["path"]).read_bytes().decode("utf-8")
    return manifest, samples


def audit_finauditing(bundle: Path, source: Path) -> dict:
    """Compare all normalized inputs/labels with the original pinned rows."""
    manifest, rows = verified_samples(bundle)
    labels = read_jsonl(bundle / "private/gold.jsonl")
    originals = list(finauditing({"source": str(source)}))
    if len(originals) != len(rows) or len(labels) != len(rows):
        raise ValueError("release must contain the complete original FinAuditing split")
    for original, row, label in zip(originals, rows, labels, strict=True):
        if (original.id, original.question, original.split, original.gold) != (row["id"], row["question"], row["split"], label["gold"]):
            raise ValueError("FinAuditing question, split, or label was changed")
        for role in ("documents", "rules"):
            assets = getattr(original, role)
            if len(assets) != len(row[role]):
                raise ValueError("FinAuditing document/rule inventory was changed")
            for asset, stored in zip(assets, row[role], strict=True):
                if asset.read_bytes() != inside(bundle, stored["path"]).read_bytes():
                    raise ValueError("FinAuditing document/rule text was changed")
    return {
        "rows_checked": manifest["samples"],
        "counts": manifest["counts"],
        "processors": [],
        "transformations": [
            "Decode one JSON encoding layer in query and labels.",
            "Split query at its seven original document headings and final question.",
            "Trim boundary whitespace; remove the trailing empty Answer: marker.",
            "Record and omit empty/None document placeholders without inventing text.",
            "Store original instructions/taxonomy as files and labels separately.",
            "Deduplicate identical local assets by SHA-256; keep every original sample.",
        ],
        "question_document_rule_label_parity": True,
    }


def build_finauditing_release(bundle: Path, source: Path, config: dict, output: Path, *, version: str) -> dict:
    """Package originals and baseline inputs, binding them to the shared RAGFlow."""
    bundle, source, output = (Path(bundle).resolve(), Path(source).resolve(), Path(output).resolve())
    if output.exists():
        raise FileExistsError(f"release archive already exists: {output}")
    if config.get("processors") or config.get("loader") != "finauditing":
        raise ValueError("baseline FinAuditing releases require its loader and no processors")
    report = audit_finauditing(bundle, source)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".dcc-pack-", dir=output.parent) as temporary:
        root = Path(temporary)
        target = root / "bundle"
        target.mkdir()
        # Deliberate inventory: exclude locks, logs, credentials, and old reports.
        for name in ("manifest.json", "index.json", "public/samples.jsonl", "private/gold.jsonl"):
            path = inside(bundle, name)
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
        _, samples = verified_samples(bundle)
        for sample in samples:
            for asset in sample["documents"] + sample["rules"]:
                destination = target / asset["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(inside(bundle, asset["path"]), destination)
        raw = root / "raw/FinAuditing"
        raw.mkdir(parents=True)
        source_manifest = json.loads((source / "manifest.json").read_text())
        shutil.copyfile(source / "manifest.json", raw / "manifest.json")
        for entry in source_manifest["datasets"]:
            original = inside(source, entry["file"])
            if digest(original.read_bytes()) != entry["sha256"]:
                raise ValueError("original parquet checksum differs from its source manifest")
            destination = raw / entry["file"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, destination)
            card = original.parent / "README.md"
            if card.exists():
                shutil.copyfile(card, destination.parent / "README.md")
        # Provenance is relative to the release root, never the publisher's disk.
        labels = read_jsonl(target / "private/gold.jsonl")
        for label in labels:
            task = label["dataset"].rsplit("_", 1)[1]
            folder = {"finre": "FinRE", "finmr": "FinMR", "finsm": "FinSM"}[task]
            label["metadata"]["source_file"] = f"raw/FinAuditing/{folder}/test.parquet"
        (target / "private/gold.jsonl").write_bytes(b"".join(encoded(row) for row in labels))
        manifest = json.loads((target / "manifest.json").read_text())
        manifest["gold_sha256"] = digest((target / "private/gold.jsonl").read_bytes())
        atomic_json(target / "manifest.json", manifest)
        portable = {
            "loader": "finauditing",
            "loader_options": {"source": "raw/FinAuditing", "tasks": ["FinRE", "FinMR", "FinSM"]},
            "processors": [],
            "prompts": deepcopy(config.get("prompts", {})),
            "ragflow": {
                **index_identity(manifest, config["ragflow"]),
                "api_key_env": "RAGFLOW_API_KEY",
                "api_key_file_env": "RAGFLOW_API_KEY_FILE",
                "index_workers": config["ragflow"].get("index_workers", 1),
            },
        }
        portable["ragflow"].pop("public_sha256")
        for key in ("retrieval_options", "http_timeout", "check_embedding_models", "poll_interval", "parse_timeout", "parse_batch_size"):
            if key in config["ragflow"]:
                portable["ragflow"][key] = deepcopy(config["ragflow"][key])
        prompts = portable["prompts"]
        prompts.setdefault("layers", list(DEFAULT_LAYERS))
        groups = [prompts["layers"], *prompts.get("datasets", {}).values(), *prompts.get("samples", {}).values()]
        for group in groups:
            for index, layer in enumerate(group):
                if not layer.startswith("literal:"):
                    content = Path(layer).read_bytes()
                    reference = f"prompts/{digest(content)}.md"
                    (root / "prompts").mkdir(exist_ok=True)
                    (root / reference).write_bytes(content)
                    group[index] = reference
        atomic_json(root / "config.json", portable)
        resolved = deepcopy(portable)
        prompt_groups = [resolved["prompts"]["layers"], *resolved["prompts"].get("datasets", {}).values(), *resolved["prompts"].get("samples", {}).values()]
        for group in prompt_groups:
            for index, layer in enumerate(group):
                if not layer.startswith("literal:"):
                    group[index] = str(root / layer)
        tasks = render(target, resolved)
        originals = list(finauditing({"source": str(source)}))
        for original, task in zip(originals, tasks, strict=True):
            if original.rules[0].text not in task["prompt"]:
                raise ValueError("baseline task prompt must include the original task instructions verbatim")
        validate_ready_bundle(target)
        report["original_task_instructions_inline"] = True
        (root / "README.md").write_text(
            "# FinAuditing for SP DCC\n\n"
            f"Version: {version}. Original official test rows; no summarization, reasoning or label changes.\n\n"
            "Source: https://github.com/The-FinAI/FinAuditing\n"
            "Paper and authors: https://arxiv.org/abs/2510.08886\n"
            "Dataset license: CC BY 4.0, https://creativecommons.org/licenses/by/4.0/ . "
            "Original dataset cards, revision hashes and Parquet files are under raw/FinAuditing.\n\n"
            "bundle/ contains normalized inputs, separate private labels, prompts and sample-scoped remote knowledge-base mappings. "
            "The vector index stays in the shared RAGFlow service; this archive does not host that service.\n"
            "Set DCC_BUNDLE to bundle/, DCC_CONFIG to config.json and configure RAGFLOW_API_KEY or RAGFLOW_API_KEY_FILE.\n",
            encoding="utf-8",
        )
        metadata = {
            "schema_version": 1,
            "dataset": "FinAuditing",
            "version": version,
            "samples": manifest["samples"],
            "counts": manifest["counts"],
            "processing_audit": report,
            "ragflow": {"base_url": portable["ragflow"]["base_url"], "knowledge_base_scope": "sample", "index_ready": True},
            "files": {path.relative_to(root).as_posix(): digest(path.read_bytes()) for path in sorted(root.rglob("*")) if path.is_file()},
        }
        atomic_json(root / "release.json", metadata)
        verify_release(root)
        temporary_archive = root / ".archive.tar.gz"
        with (
            temporary_archive.open("wb") as stream,
            gzip.GzipFile(fileobj=stream, mode="wb", mtime=0) as compressed,
            tarfile.open(fileobj=compressed, mode="w") as archive,
        ):
            for name in sorted([*metadata["files"], "release.json"]):
                path = root / name
                info = archive.gettarinfo(str(path), arcname=name)
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ""
                info.mode = 0o600 if name.startswith("bundle/private/") else 0o644
                with path.open("rb") as handle:
                    archive.addfile(info, handle)
        checksum = digest(temporary_archive.read_bytes())
        os.rename(temporary_archive, output)
    return {"version": version, "asset": output.name, "sha256": checksum, "bytes": output.stat().st_size, "samples": metadata["samples"]}


def verify_release(root: Path) -> dict:
    metadata = json.loads((root / "release.json").read_text())
    if metadata.get("schema_version") != 1:
        raise ValueError("unsupported dataset release schema")
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if actual != {*metadata["files"], "release.json"}:
        raise ValueError("release file inventory differs")
    for name, checksum in metadata["files"].items():
        if digest(inside(root, name).read_bytes()) != checksum:
            raise ValueError(f"release checksum differs: {name}")
    manifest, _ = validate_ready_bundle(root / "bundle")
    if manifest["samples"] != metadata["samples"] or manifest["counts"] != metadata["counts"]:
        raise ValueError("release counts differ from its bundle")
    return metadata


def install_release(archive: Path, output: Path, *, sha256: str) -> dict:
    """Verify before publishing a new directory; never overwrite existing data."""
    archive, output = Path(archive).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"dataset destination already exists: {output}")
    if digest(archive.read_bytes()) != sha256:
        raise ValueError("downloaded release archive SHA-256 differs")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".dcc-download-", dir=output.parent) as temporary:
        root = Path(temporary) / "data"
        root.mkdir()
        seen = set()
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar:
                name = PurePosixPath(member.name)
                if not member.isfile() or name.is_absolute() or ".." in name.parts or member.name in seen:
                    raise ValueError("release archive contains an invalid or duplicate file")
                seen.add(member.name)
                destination = inside(root, member.name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, destination.open("xb") as target:
                    shutil.copyfileobj(source, target)
        metadata = verify_release(root)
        (root / "bundle/private").chmod(0o700)
        (root / "bundle/private/gold.jsonl").chmod(0o600)
        os.rename(root, output)
    return {
        "dataset": metadata["dataset"],
        "version": metadata["version"],
        "samples": metadata["samples"],
        "bundle": str(output / "bundle"),
        "config": str(output / "config.json"),
    }


def download_release(specification: dict, output: Path, *, archive: Path | None = None) -> dict:
    if archive is not None:
        return install_release(archive, output, sha256=specification["sha256"])
    if shutil.which("gh") is None:
        raise RuntimeError("install GitHub CLI (gh) and run gh auth login to download private release assets; or pass --archive")
    if Path(specification["asset"]).name != specification["asset"]:
        raise ValueError("release asset must be a plain filename")
    with tempfile.TemporaryDirectory(prefix="dcc-release-") as temporary:
        try:
            subprocess.run(
                [
                    "gh",
                    "release",
                    "download",
                    specification["tag"],
                    "--repo",
                    specification["repository"],
                    "--pattern",
                    specification["asset"],
                    "--dir",
                    temporary,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            raise RuntimeError("GitHub Release download failed; check gh auth status, repository access and network connectivity") from None
        return install_release(Path(temporary) / specification["asset"], output, sha256=specification["sha256"])
