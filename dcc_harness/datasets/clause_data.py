"""Verify the pinned server bundle and prepare separate public/private CLAUSE manifests."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from dcc_harness.clause_metrics import (
    CONFIG,
    CATEGORIES,
    SOURCES,
    file_hash,
    protocol,
    validate_gold,
)
from .clause import ids_hash, prompt_text


def verify_bundle(archive: Path):
    if file_hash(archive) != protocol()["bundle_sha256"]:
        raise ValueError("CLAUSE archive SHA256 does not match the pinned dataset")
    contents = {}
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            parts = PurePosixPath(member.name).parts
            if (
                not parts
                or parts[0] != "CLAUSE_Eval3_InText_ready"
                or ".." in parts
                or member.issym()
                or member.islnk()
            ):
                raise ValueError("unsafe CLAUSE archive member")
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("unsupported CLAUSE archive member")
            relative = str(PurePosixPath(*parts[1:]))
            if relative in contents:
                raise ValueError("duplicate CLAUSE archive member")
            contents[relative] = bundle.extractfile(member).read()
    return _verify_contents(contents)


def _manifest_records(body):
    if hashlib.sha256(body).hexdigest() != protocol()["package_checksums_sha256"]:
        raise ValueError("CLAUSE checksum manifest differs from the pinned release")
    return [json.loads(line) for line in body.splitlines()]


def verify_directory(directory: Path):
    """Authenticate a ready directory by file content, independently of tar/gzip bytes."""
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(
            "CLAUSE ready directory must be a real directory, not a symlink"
        )
    files = {}
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError("CLAUSE ready directory contains a symlink")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("CLAUSE ready directory contains a nonregular file")
        files[path.relative_to(directory).as_posix()] = path
    manifest = "provenance/package_checksums.jsonl"
    if manifest not in files:
        raise ValueError("CLAUSE ready directory is missing its checksum manifest")
    records = _manifest_records(files[manifest].read_bytes())
    required = {r["path"] for r in records} | {manifest}
    if required - files.keys():
        raise ValueError("CLAUSE ready directory is missing expected files")
    if files.keys() - required:
        raise ValueError("Unexpected files in CLAUSE ready directory")
    # Read once: these verified bytes are exactly what the converter will use.
    return _verify_contents({name: path.read_bytes() for name, path in files.items()})


def _verify_contents(contents):
    checks = _manifest_records(contents["provenance/package_checksums.jsonl"])
    for check in checks:
        body = contents.get(check["path"])
        if (
            body is None
            or len(body) != check["bytes"]
            or hashlib.sha256(body).hexdigest() != check["sha256"]
        ):
            raise ValueError(f"CLAUSE bundle checksum failed: {check['path']}")
    if {r["path"] for r in checks} != set(contents) - {
        "provenance/package_checksums.jsonl"
    }:
        raise ValueError("CLAUSE checksum manifest does not cover the entire bundle")
    groups, texts, seen, splits = {}, {}, set(), {}
    for split, expected in protocol()["splits"].items():
        raw = contents[f"splits/{split}.jsonl"]
        if hashlib.sha256(raw).hexdigest() != expected["manifest_sha256"]:
            raise ValueError("CLAUSE split manifest differs from pinned protocol")
        rows = [json.loads(line) for line in raw.splitlines()]
        perturbations = 0
        for row in rows:
            rid = row["record_id"]
            if (
                rid in seen
                or row["split"] != split
                or row["source"] not in SOURCES
                or row["category"] not in CATEGORIES
            ):
                raise ValueError("CLAUSE sample identity/source/category is invalid")
            seen.add(rid)
            document = contents[row["text_path"]]
            annotation = contents[row["ground_truth_path"]]
            if (
                hashlib.sha256(document).hexdigest() != row["input_sha256"]
                or hashlib.sha256(annotation).hexdigest()
                != row["packaged_annotation_sha256"]
            ):
                raise ValueError("CLAUSE input/annotation pairing failed")
            text = document.decode("utf-8")
            if "<*$p$*>" in text:
                raise ValueError("answer marker remains in CLAUSE input")
            gt = json.loads(annotation)
            count = len(validate_gold(gt))
            if count != row["perturbations"]:
                raise ValueError("CLAUSE perturbation count mismatch")
            perturbations += count
            group = row["group_id"]
            normalized = hashlib.sha256(
                re.sub(r"\s+", " ", text).strip().casefold().encode()
            ).hexdigest()
            if (
                groups.get(group, split) != split
                or texts.get(normalized, split) != split
            ):
                raise ValueError("CLAUSE group or normalized input leaks across splits")
            groups[group], texts[normalized] = split, split
        if (
            len(rows) != expected["documents"]
            or perturbations != expected["perturbations"]
            or ids_hash(r["record_id"] for r in rows) != expected["ids_sha256"]
        ):
            raise ValueError("CLAUSE split does not match pinned counts/membership")
        splits[split] = rows
    prompt_provenance = json.loads(contents["prompts/provenance.json"])
    if groups[prompt_provenance["l2_example_group"]] != "train":
        raise ValueError("CLAUSE one-shot example is not restricted to train")
    for variant in ("l1", "l2"):
        if (
            contents[f"prompts/{variant}_system.txt"]
            != (CONFIG / f"{variant}_system.txt").read_bytes()
        ):
            raise ValueError("CLAUSE prompt differs from pinned resource")
    return contents, splits, len(checks)


def prepare(
    archive: Path | None,
    output_dir: Path,
    *,
    variant="l1",
    ready_dir: Path | None = None,
    source_provenance: dict | None = None,
):
    prompt = prompt_text(variant)
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError("CLAUSE output already exists; choose a new directory")
    if (archive is None) == (ready_dir is None):
        raise ValueError(
            "Choose exactly one CLAUSE archive or verified ready directory"
        )
    contents, splits, checksum_count = (
        verify_directory(ready_dir)
        if ready_dir is not None
        else verify_bundle(Path(archive))
    )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".clause-prepare-", dir=output_dir.parent))
    try:
        documents = temporary / "public/documents"
        documents.mkdir(parents=True)
        (temporary / "private").mkdir()
        index = 0
        for split, rows in splits.items():
            with (
                (temporary / f"public/{split}.jsonl").open(
                    "w", encoding="utf-8"
                ) as public_file,
                (temporary / f"private/{split}.jsonl").open(
                    "w", encoding="utf-8"
                ) as private_file,
            ):
                for row in rows:
                    name = f"document-{index:05d}.txt"
                    index += 1
                    (documents / name).write_bytes(contents[row["text_path"]])
                    base = {
                        "id": row["record_id"],
                        "benchmark": "clause",
                        "split": split,
                        "prompt": prompt,
                        "group_id": hashlib.sha256(
                            row["group_id"].encode()
                        ).hexdigest(),
                        "public_metadata": {"prompt_variant": variant},
                    }
                    public = {
                        **base,
                        "files": [
                            {"name": "document.txt", "path": f"documents/{name}"}
                        ],
                    }
                    private = {
                        **base,
                        "files": [
                            {
                                "name": "document.txt",
                                "path": f"../public/documents/{name}",
                            }
                        ],
                        "gold": json.loads(contents[row["ground_truth_path"]]),
                        "metadata": {
                            "protocol": protocol()["protocol"],
                            "source": row["source"],
                            "category": row["category"],
                            "perturbations": row["perturbations"],
                            "input_sha256": row["input_sha256"],
                            "annotation_sha256": row["packaged_annotation_sha256"],
                            "original_group_id": row["group_id"],
                            "upstream_commit": protocol()["upstream_commit"],
                        },
                    }
                    public_file.write(json.dumps(public, ensure_ascii=False) + "\n")
                    private_file.write(json.dumps(private, ensure_ascii=False) + "\n")
        for name, body in contents.items():
            if name.startswith("resources/") or name in {
                "NOTICE.md",
                "provenance/filter_split_report.json",
                "prompts/provenance.json",
            }:
                target = temporary / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
        report = {
            "source_mode": "verified_directory" if ready_dir is not None else "archive",
            "package_checksums_sha256": protocol()["package_checksums_sha256"],
            "protocol": protocol()["protocol"],
            "bundle_sha256": protocol()["bundle_sha256"],
            "prompt_variant": variant,
            "input_mode": "sp2-files",
            "checked_files": checksum_count,
            "splits": protocol()["splits"],
            "public_manifests": {s: f"public/{s}.jsonl" for s in splits},
            "evaluation_manifests": {s: f"private/{s}.jsonl" for s in splits},
        }
        if source_provenance is not None:
            report["source_rebuild"] = source_provenance
        (temporary / "preparation.json").write_text(json.dumps(report, indent=2) + "\n")
        # Check the handoff, not just the source bundle. No SP is constructed.
        from dcc_harness.data import load_tasks, validate_disjoint
        from .clause import validate_dataset

        public_tasks = []
        for split in splits:
            public = load_tasks(temporary / f"public/{split}.jsonl")
            private = load_tasks(temporary / f"private/{split}.jsonl")
            validate_dataset(public)
            validate_dataset(private)
            if any(
                a.input_sha256() != b.input_sha256()
                for a, b in zip(public, private, strict=True)
            ):
                raise ValueError(
                    "CLAUSE public and evaluation manifests have different inputs"
                )
            public_tasks.extend(public)
        validate_disjoint(public_tasks)
        temporary.rename(output_dir)
        return report
    except BaseException:
        shutil.rmtree(temporary)
        raise
