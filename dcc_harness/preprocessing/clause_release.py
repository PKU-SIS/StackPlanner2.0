"""Versioned full CLAUSE releases, with shared L1/L2 indexes and file views."""

from __future__ import annotations

import gzip
import json
import os
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from dcc_harness.data import load_tasks
from dcc_harness.datasets import clause as file_plugin
from .clause import render_clause, validate_dataset
from .pipeline import atomic_json, digest, encoded, prepare
from .releases import copy_bundle, validate_ready_bundle

SPLITS = ("train", "validation", "test")
VARIANTS = ("l1", "l2")


def prepare_all(source: Path, output: Path, config: dict):
    """Prepare all pinned splits offline; publish only after full validation."""
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".clause-prepare-", dir=output.parent))
    try:
        for split in SPLITS:
            path = source / "private" / f"{split}.jsonl"
            tasks = load_tasks(path)
            file_plugin.validate_dataset(tasks)
            expected = file_plugin.protocol()["splits"][split]
            if (
                len(tasks) != expected["documents"]
                or file_plugin.ids_hash(t.id for t in tasks) != expected["ids_sha256"]
                or any(t.gold is None for t in tasks)
            ):
                raise ValueError(f"requires complete pinned private {split} split")
            prepare(
                {
                    **config,
                    "loader": "dcc_harness.preprocessing.clause:load",
                    "loader_options": {"source": str(path.resolve())},
                },
                staging / split,
            )
        # Public service settings only. Credentials must be environment/file based.
        rag = dict(config["ragflow"])
        for key in ("api_key", "api_key_file"):
            rag.pop(key, None)
        atomic_json(staging / "config.json", {"ragflow": rag})
        os.rename(staging, output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def render_all(root: Path):
    config = json.loads((root / "config.json").read_text())
    for split in SPLITS:
        bundle = root / split
        for variant in VARIANTS:
            render_clause(bundle, config, variant=variant)
            for visibility in ("public", "private"):
                path = bundle / visibility / f"clause-{variant}-tasks.jsonl"
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                for row in rows:
                    row["benchmark"] = "clause"
                    row["files"] = [
                        f for f in row["files"] if f["name"] == "document.txt"
                    ]
                    row["metadata"].pop("dcc", None)
                (path.parent / f"clause-files-{variant}-tasks.jsonl").write_bytes(
                    b"".join(encoded(row) for row in rows)
                )
    return verify_data(root)


def verify_data(root: Path):
    counts, owners = {}, set()
    for split in SPLITS:
        bundle = root / split
        manifest, samples = validate_ready_bundle(bundle)
        expected = file_plugin.protocol()["splits"][split]
        if (
            manifest["samples"] != expected["documents"]
            or file_plugin.ids_hash(s["id"] for s in samples) != expected["ids_sha256"]
            or any(s["split"] != split for s in samples)
        ):
            raise ValueError("release split differs from pinned CLAUSE protocol")
        index = json.loads((bundle / "index.json").read_text())
        for scope in index["scopes"].values():
            kb = scope["dataset_ids"][0]
            if kb in owners:
                raise ValueError("knowledge base reused across independent samples")
            owners.add(kb)
        counts[split] = len(samples)
        labels = {
            r["id"]: r
            for r in (
                json.loads(line)
                for line in (bundle / "private/gold.jsonl").read_text().splitlines()
            )
        }
        for variant in VARIANTS:
            for mode, validate in [
                ("", validate_dataset),
                ("files-", file_plugin.validate_dataset),
            ]:
                name = f"clause-{mode}{variant}-tasks.jsonl"
                public = load_tasks(bundle / "public" / name)
                private = load_tasks(bundle / "private" / name)
                validate(public)
                validate(private)
                if [t.id for t in public] != [s["id"] for s in samples] or [
                    t.id for t in private
                ] != [t.id for t in public]:
                    raise ValueError("task inventory changed")
                if any(
                    a.input_sha256() != b.input_sha256()
                    or a.gold is not None
                    or b.gold is None
                    for a, b in zip(public, private, strict=True)
                ):
                    raise ValueError("public/private input or label contract differs")
                if any(
                    t.gold != labels[t.id]["gold"]
                    or any(
                        t.metadata.get(k) != v
                        for k, v in labels[t.id]["metadata"].items()
                    )
                    for t in private
                ):
                    raise ValueError(
                        "rendered labels differ from canonical annotations"
                    )
                if any(t.public_metadata["prompt_variant"] != variant for t in public):
                    raise ValueError("prompt variant differs")
    return {
        "documents": sum(counts.values()),
        "splits": counts,
        "knowledge_bases": len(owners),
    }


def verify_release(root: Path):
    release = json.loads((root / "release.json").read_text())
    if release.get("schema") != "clause-ragflow-release-v1":
        raise ValueError("unknown CLAUSE release schema")
    actual = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p != root / ".dcc.lock"
    }
    if actual != {*release["files"], "release.json"}:
        raise ValueError("release file inventory differs")
    for name, checksum in release["files"].items():
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or digest((root / name).read_bytes()) != checksum
        ):
            raise ValueError("release file checksum/path differs")
    if verify_data(root) != release["data"]:
        raise ValueError("release data counts differ")
    return release


def pack(root: Path, output: Path, version: str):
    """Whitelist data assets; never archive logs, local config, keys or models."""
    data = verify_data(root)
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".clause-pack-", dir=output.parent) as tmp:
        target = Path(tmp) / "data"
        target.mkdir()
        config = json.loads((root / "config.json").read_text())
        allowed = {
            "base_url",
            "dataset_prefix",
            "knowledge_base_scope",
            "dataset_options",
            "retrieval_options",
            "http_timeout",
            "poll_interval",
            "parse_timeout",
            "parse_batch_size",
            "index_workers",
            "check_embedding_models",
        }
        atomic_json(
            target / "config.json",
            {"ragflow": {k: v for k, v in config["ragflow"].items() if k in allowed}},
        )
        for split in SPLITS:
            copy_bundle(root / split, target / split)
            state = json.loads((target / split / "index.json").read_text())
            state["read_only_view"] = True
            atomic_json(target / split / "index.json", state)
        render_all(target)
        protocol = target / "protocol"
        protocol.mkdir()
        for name in (
            "protocol.json",
            "prompt_provenance.json",
            "l1_system.txt",
            "l2_system.txt",
            "NOTICE.md",
        ):
            shutil.copyfile(file_plugin.CONFIG / name, protocol / name)
        (target / "README.md").write_text(
            "# CLAUSE EVAL3 In-Text shared release\n\nFull pinned train/validation/test splits; L1 and L2 share each sample's index.\nUse public/clause-l1-tasks.jsonl (or l2) for RAG; use public/clause-files-l1-tasks.jsonl for file mode. Labels are in the corresponding private manifests.\nSet DCC_BUNDLE to the selected split directory and DCC_CONFIG to config.json. RAG requires authorized RAGFLOW_API_KEY or RAGFLOW_API_KEY_FILE; GitHub access alone is insufficient.\nThe vector index lives in the shared RAGFlow service, not this archive. Do not reindex or delete shared libraries. Offline scoring and file mode need no RAG credentials. See repository docs/clause_ragflow.md.\n"
        )
        release = {
            "schema": "clause-ragflow-release-v1",
            "version": version,
            "data": data,
            "files": {
                p.relative_to(target).as_posix(): digest(p.read_bytes())
                for p in sorted(target.rglob("*"))
                if p.is_file()
            },
        }
        atomic_json(target / "release.json", release)
        verify_release(target)
        archive = Path(tmp) / "release.tar.gz"
        with (
            archive.open("wb") as stream,
            gzip.GzipFile(fileobj=stream, mode="wb", mtime=0) as zipped,
            tarfile.open(fileobj=zipped, mode="w") as tar,
        ):
            for name in sorted([*release["files"], "release.json"]):
                path = target / name
                info = tar.gettarinfo(str(path), arcname=name)
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ""
                info.mode = 0o600 if "/private/" in name else 0o644
                with path.open("rb") as handle:
                    tar.addfile(info, handle)
        result = {
            "schema_version": 1,
            "dataset": "CLAUSE EVAL3 In-Text",
            "repository": "PKU-SIS/StackPlanner2.0",
            "tag": version,
            "asset": output.name,
            "sha256": digest(archive.read_bytes()),
            "bytes": archive.stat().st_size,
            **data,
        }
        os.rename(archive, output)
        return result


def install(archive: Path, output: Path, checksum: str):
    if output.exists():
        raise FileExistsError(output)
    if digest(archive.read_bytes()) != checksum:
        raise ValueError("archive SHA-256 differs")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".clause-install-", dir=output.parent
    ) as tmp:
        target = Path(tmp) / "data"
        target.mkdir()
        seen = set()
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar:
                name = PurePosixPath(member.name)
                if (
                    not member.isfile()
                    or name.is_absolute()
                    or ".." in name.parts
                    or member.name in seen
                ):
                    raise ValueError("invalid archive member")
                seen.add(member.name)
                path = target / member.name
                path.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, path.open("xb") as dest:
                    shutil.copyfileobj(source, dest)
        release = verify_release(target)
        for path in target.glob("*/private"):
            path.chmod(0o700)
            for item in path.iterdir():
                item.chmod(0o600)
        os.rename(target, output)
        return release["data"]


def audit_service(root: Path, *, workers: int = 4):
    """Check every remote document and retrieve evidence without running SP."""
    from concurrent.futures import ThreadPoolExecutor
    from .ragflow import RagflowClient, retrieve

    if type(workers) is not int or not 1 <= workers <= 8:
        raise ValueError("audit workers must be 1..8")
    data = verify_data(root)
    config = json.loads((root / "config.json").read_text())["ragflow"]
    jobs = []
    fingerprints = {}
    for split in SPLITS:
        path = root / split / "index.json"
        fingerprints[split] = digest(path.read_bytes())
        index = json.loads(path.read_text())
        jobs.extend(
            (split, sample, {**scope, "base_url": index["base_url"]})
            for sample, scope in index["scopes"].items()
        )

    def check(job):
        split, sample, scope = job
        client = RagflowClient(config)
        try:
            documents = client.list_documents(scope["dataset_ids"][0])
            if {d["id"] for d in documents} != set(scope["document_ids"]):
                raise ValueError("remote document inventory changed")
            if any(
                str(d.get("run")).upper() not in {"DONE", "3"}
                or not d.get("chunk_count")
                for d in documents
            ):
                raise ValueError("remote document not fully indexed")
            hits = retrieve(
                client,
                scope,
                "contract obligations definitions payment termination",
                page_size=1,
                options={"similarity_threshold": 0},
            )
            if not hits["chunks"]:
                raise ValueError("indexed sample returned no retrieval evidence")
            return {
                "split": split,
                "sample_id": sample,
                "documents": len(documents),
                "retrieval_ok": True,
            }
        finally:
            client.session.close()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(check, jobs))
    return {
        "passed": True,
        "data": data,
        "index_sha256": fingerprints,
        "checked_samples": len(rows),
        "checked_documents": sum(r["documents"] for r in rows),
    }
