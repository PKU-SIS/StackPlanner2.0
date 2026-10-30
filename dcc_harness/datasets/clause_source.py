#!/usr/bin/env python3
"""Rebuild the exact prepared CLAUSE dataset without a local audit index.

Adapted from the user-supplied source rebuild supplement; see
configs/clause/source_rebuild/README.md for pinned provenance.

Python >=3.10, standard library only. The files under templates/ are frozen
prompts, expected manifests, evaluation code and provenance; no corpus bodies,
full annotations, NLTK corpora or model weights are shipped here.
"""

import argparse
import contextlib
import gzip
import hashlib
import io
import json
import os
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unicodedata
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from collections import Counter

from dcc_harness.clause_metrics import CONFIG, protocol

TEMPLATES = CONFIG / "source_rebuild/templates"
COMMIT = "c81c6a5251771cab3d9f3d081f71c388957cf936"
ROOT_TREE = "1298b34966edf98f1a03a59820915f88de4a0742"
DATA_TREE = "a51edc3815cc1b7c084c8aa5366780dc26a011fa"
SOURCE_URL = (
    f"https://codeload.github.com/mananroychoudhury/Better-Call-CLAUSE/tar.gz/{COMMIT}"
)
CANONICAL_ARCHIVE_SHA = (
    "e6499247c56d737234afee174b9e6ae17db4c81a7c7a4be7439bc317636f8377"
)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def download(url, path, expected_sha=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if expected_sha and sha(path) != expected_sha:
            raise ValueError(f"Cached resource checksum mismatch: {path}")
        print("Using cached:", path, flush=True)
        return path
    partial = path.with_name(path.name + ".part")
    for attempt in range(3):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "CLAUSE-fixed-source-rebuilder/1"}
            )
            print("Downloading:", url, flush=True)
            with (
                urllib.request.urlopen(request, timeout=60) as response,
                partial.open("wb") as out,
            ):
                count = 0
                reported = 0
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    out.write(block)
                    count += len(block)
                    if count - reported >= 16 * 1024 * 1024:
                        print(f"  {count / (1024 * 1024):.0f} MiB", flush=True)
                        reported = count
            if expected_sha and sha(partial) != expected_sha:
                raise ValueError("Downloaded checksum mismatch: " + url)
            os.replace(partial, path)
            return path
        except Exception:
            if partial.exists():
                partial.unlink()
            if attempt == 2:
                raise
            time.sleep(2**attempt)


def git_hash(kind, data):
    return hashlib.sha1(
        kind.encode() + b" " + str(len(data)).encode() + b"\0" + data
    ).hexdigest()


def hash_tree(node, prefix="", hashes=None):
    hashes = {} if hashes is None else hashes
    entries = []
    for name, value in node.items():
        if isinstance(value, dict):
            digest = hash_tree(value, prefix + name + "/", hashes)
            mode = "40000"
            key = (name + "/").encode()
        else:
            mode, digest = value
            key = name.encode()
        entries.append(
            (key, mode.encode() + b" " + name.encode() + b"\0" + bytes.fromhex(digest))
        )
    digest = git_hash("tree", b"".join(v for _, v in sorted(entries)))
    hashes[prefix.rstrip("/")] = digest
    return digest


def source_members(archive):
    with tarfile.open(archive, "r|gz") as tar:
        for member in tar:
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError(
                    "Only regular upstream files are permitted: " + member.name
                )
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
                raise ValueError("Unsafe archive path: " + member.name)
            yield path, tar.extractfile(member).read(), member.mode


def verify_extract_source(archive, destination):
    root = {}
    seen = set()
    prefix = None
    files = 0
    total = 0
    for path, data, mode in source_members(archive):
        if prefix is None:
            prefix = path.parts[0]
        if path.parts[0] != prefix:
            raise ValueError("Multiple archive roots")
        rel = PurePosixPath(*path.parts[1:])
        canonical = unicodedata.normalize("NFD", str(rel)).casefold()
        if canonical in seen:
            raise ValueError("Duplicate or case-colliding upstream path: " + str(rel))
        seen.add(canonical)
        node = root
        for name in rel.parts[:-1]:
            node = node.setdefault(name, {})
        node[rel.name] = (
            "100755" if mode & 0o111 else "100644",
            git_hash("blob", data),
        )
        files += 1
        total += len(data)
    hashes = {}
    actual = hash_tree(root, hashes=hashes)
    if actual != ROOT_TREE or hashes.get("datasets") != DATA_TREE:
        raise ValueError("Upstream content is not the pinned official Git tree")
    destination.mkdir(parents=True, exist_ok=False)
    for path, data, mode in source_members(archive):
        rel = PurePosixPath(*path.parts[1:])
        target = destination / str(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return dict(
        commit=COMMIT,
        root_tree=actual,
        dataset_tree=hashes["datasets"],
        files=files,
        bytes=total,
        archive_sha256=sha(archive),
    )


def category(name):
    s = name.lower()
    for needle, value in [
        ("ambigu", "ambiguity"),
        ("inconsisten", "inconsistency"),
        ("terminolog", "terminology"),
        ("omission", "omission"),
        ("structural", "structural"),
    ]:
        if needle in s:
            return value
    return None


def scan_intext(source, index):
    """Recreate the necessary source index by scanning actual upstream files."""
    rows = []
    for src in sorted((source / "datasets").iterdir()):
        if not src.is_dir():
            continue
        for folder in sorted(src.iterdir()):
            if not folder.is_dir() or "intext" not in folder.name.lower():
                continue
            cat = category(folder.name)
            if not cat:
                raise ValueError("Unknown internal category: " + str(folder))
            texts = {}
            for p in sorted(folder.rglob("*.txt")):
                if p.name in texts:
                    raise ValueError("Ambiguous same-name text files: " + p.name)
                texts[p.name] = p
            for annotation in sorted(folder.glob("*.json")):
                data = json.loads(annotation.read_text(encoding="utf-8-sig"))
                if not isinstance(data, list) or len(data) != 1:
                    raise ValueError("Unexpected GT structure: " + str(annotation))
                obj = data[0]
                ps = obj.get("perturbation", [])
                name = obj.get("file_name", "")
                if not isinstance(ps, list):
                    raise ValueError("Expected perturbation array")
                base = name or annotation.name.removeprefix("perturbed_").removesuffix(
                    ".json"
                )
                candidates = [
                    base,
                    base + ".txt",
                    annotation.name.removeprefix("perturbed_").removesuffix(".json"),
                ]
                text = next((texts[c] for c in candidates if c in texts), None)
                labels = Counter(
                    str(p.get("contradiction_exists", "<missing>")) for p in ps
                )
                rows.append(
                    dict(
                        source=src.name,
                        category=cat,
                        kind="intext",
                        annotation=annotation.relative_to(source).as_posix(),
                        perturbations=len(ps),
                        validation_label_counts=dict(labels),
                        all_perturbations_verified_YES=bool(ps)
                        and all(p.get("contradiction_exists") == "YES" for p in ps),
                        paired_text=text.relative_to(source).as_posix()
                        if text
                        else None,
                        file_name=name,
                        annotation_sha256=sha(annotation),
                    )
                )
    if len(rows) != 5160 or sum(r["perturbations"] for r in rows) != 18513:
        raise ValueError("Unexpected pinned-source internal dataset counts")
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )
    return dict(
        annotation_files=len(rows),
        perturbations=sum(r["perturbations"] for r in rows),
        paired=sum(bool(r["paired_text"]) for r in rows),
    )


def prepare_nltk(output, cache, resources_from=None):
    # Copy only the three frozen resources. Verification below checks their shipped hashes.
    paths = [
        "tokenizers/punkt/english.pickle",
        "tokenizers/punkt/PY3/english.pickle",
        "corpora/wordnet.zip",
    ]
    root = output / "resources/nltk_data"
    if resources_from is not None:
        supplied = resources_from / "nltk_data"
        for name in paths:
            origin = supplied / name
            if not origin.is_file():
                raise FileNotFoundError(origin)
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, target)
        return "copied existing resources; subsequently SHA-256 verified"
    metadata = json.loads(
        (output / "resources/nltk_resources_provenance.json").read_text()
    )
    downloads = {}
    for r in metadata:
        downloads[r["name"]] = download(
            r["url"], cache / (r["name"] + ".zip"), r["sha256"]
        )
    with zipfile.ZipFile(downloads["punkt"]) as archive:
        for name in ["punkt/english.pickle", "punkt/PY3/english.pickle"]:
            target = root / "tokenizers" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
    target = root / "corpora/wordnet.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(downloads["wordnet"], target)
    return "official NLTK archives (downloaded or reused cache); source archive checksums enforced"


def verify_expected_files(output):
    if (
        sha(output / "provenance/package_checksums.jsonl")
        != protocol()["package_checksums_sha256"]
    ):
        raise ValueError("Rebuilt checksum manifest differs from pinned release")
    if any(path.is_symlink() for path in output.rglob("*")):
        raise ValueError("Rebuilt release contains a symlink")
    records = [
        json.loads(line)
        for line in (TEMPLATES / "provenance/package_checksums.jsonl")
        .read_text()
        .splitlines()
    ]
    for r in records:
        path = output / r["path"]
        if (
            not path.is_file()
            or path.stat().st_size != r["bytes"]
            or sha(path) != r["sha256"]
        ):
            raise ValueError("Rebuilt file differs from current release: " + r["path"])
    required = {r["path"] for r in records} | {"provenance/package_checksums.jsonl"}
    actual = {
        p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()
    }
    if actual != required:
        raise ValueError(
            "Unexpected/missing output files: " + str(sorted(actual ^ required)[:10])
        )
    return len(records)


def archive_output(output, target):
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    records = [
        json.loads(line)["path"]
        for line in (output / "provenance/package_checksums.jsonl")
        .read_text()
        .splitlines()
    ] + ["provenance/package_checksums.jsonl"]
    with (
        target.open("wb") as raw,
        gzip.GzipFile(
            filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=6
        ) as gz,
        tarfile.open(fileobj=gz, mode="w") as tar,
    ):
        for name in sorted(records):
            path = output / name
            info = tar.gettarinfo(
                str(path), arcname="CLAUSE_Eval3_InText_ready/" + name
            )
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            info.mode = 0o644
            with path.open("rb") as stream:
                tar.addfile(info, stream)
    digest = sha(target)
    target.with_name(target.name + ".sha256").write_text(
        digest + "  " + target.name + "\n"
    )
    return dict(
        path=str(target),
        bytes=target.stat().st_size,
        sha256=digest,
        byte_identical_to_previous_archive=digest == CANONICAL_ARCHIVE_SHA,
    )


def verify_templates(root=None):
    root = Path(root) if root is not None else TEMPLATES
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Invalid CLAUSE template directory")
    manifest = root / "provenance/package_checksums.jsonl"
    if sha(manifest) != protocol()["package_checksums_sha256"]:
        raise ValueError(
            "CLAUSE template checksum manifest differs from pinned release"
        )
    records = [json.loads(line) for line in manifest.read_text().splitlines()]
    expected = {
        r["path"]: r
        for r in records
        if not r["path"].startswith(
            ("data/", "resources/nltk_data/", "provenance/upstream_code/")
        )
    }
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("CLAUSE template symlink is not permitted")
        if path.is_dir():
            continue
        relative = path.relative_to(root).as_posix()
        actual.add(relative)
        if relative == "provenance/package_checksums.jsonl":
            continue
        record = expected.get(relative)
        if (
            record is None
            or not path.is_file()
            or path.stat().st_size != record["bytes"]
            or sha(path) != record["sha256"]
        ):
            raise ValueError("CLAUSE template differs from pinned release: " + relative)
    if actual != set(expected) | {"provenance/package_checksums.jsonl"}:
        raise ValueError("Missing CLAUSE template files")
    return len(actual)


def rebuild(
    output, work_dir, *, source_archive=None, resources_from=None, archive_target=None
):
    """Download/rebuild in staging; publish only after all content checks pass."""
    output = Path(output).absolute()
    work = Path(work_dir).resolve()
    if output.exists() or output.is_symlink():
        raise FileExistsError("Output already exists; use a new directory")
    if work == output or output in work.parents:
        raise ValueError("Work directory must not be inside the new output directory")
    if archive_target is not None:
        archive_target = Path(archive_target).absolute()
        if archive_target.exists() or archive_target.is_symlink():
            raise FileExistsError("Archive output already exists")
        if archive_target == output or output in archive_target.parents:
            raise ValueError("Archive output must be outside the ready directory")
    verify_templates()  # Authenticate frozen executable helpers before runpy/subprocess.
    work.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    upstream = (
        Path(source_archive).resolve()
        if source_archive
        else download(SOURCE_URL, work / (COMMIT + ".tar.gz"))
    )
    with (
        tempfile.TemporaryDirectory(prefix="source-stage-", dir=work) as temporary,
        tempfile.TemporaryDirectory(
            prefix=".clause-ready-", dir=output.parent
        ) as publishing,
    ):
        stage = Path(temporary)
        source = stage / "upstream/CLAUSE"
        ready = Path(publishing) / "ready"
        verified = verify_extract_source(upstream, source)
        print("Pinned upstream Git tree verified.", flush=True)
        scanned = scan_intext(source, stage / "audit/dataset_records.jsonl")
        print("Recreated source index:", scanned, flush=True)
        shutil.copytree(TEMPLATES, ready)
        refs = stage / "eval3_intext"
        refs.mkdir()
        for name in [
            "paper_prompts_intext.tex",
            "released_L1_system_prompt.txt",
            "released_L2_system_prompt.txt",
            "paper_results_intext.json",
            "paper_table_verification.json",
        ]:
            shutil.copyfile(TEMPLATES / "provenance" / name, refs / name)
        module = runpy.run_path(str(TEMPLATES / "provenance/prepare_server_bundle.py"))
        module["main"].__globals__.update(ROOT=stage, UP=source, OUT=ready)
        with contextlib.redirect_stdout(io.StringIO()):
            module["main"]()
        nltk = prepare_nltk(
            ready,
            work / "nltk",
            Path(resources_from).resolve() if resources_from else None,
        )
        count = verify_expected_files(ready)
        process = subprocess.run(
            [sys.executable, "-B", str(ready / "tools/verify_bundle.py")],
            capture_output=True,
            text=True,
        )
        if process.returncode:
            raise RuntimeError(process.stdout + process.stderr)
        print(process.stdout, flush=True)
        report = dict(
            status="PASS",
            upstream=verified,
            scan=scanned,
            prepared_files_verified=count,
            total_prepared_files=count + 1,
            all_release_files_byte_identical=True,
            nltk=nltk,
            output=str(output),
            model_or_judge_called=False,
        )
        if archive_target is not None:
            report["archive"] = archive_output(ready, archive_target)
        ready.rename(output)
    record = work / "last_rebuild_verification.json"
    temporary_record = record.with_suffix(".json.tmp")
    temporary_record.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    temporary_record.replace(record)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument(
        "--work-dir", type=Path, default=Path(".benchmark-data/clause-rebuild-cache")
    )
    p.add_argument("--source-archive", type=Path)
    p.add_argument("--resources-from", type=Path)
    p.add_argument("--archive-output", type=Path)
    args = p.parse_args()
    try:
        report = rebuild(
            args.output,
            args.work_dir,
            source_archive=args.source_archive,
            resources_from=args.resources_from,
            archive_target=args.archive_output,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (ValueError, OSError, RuntimeError) as error:
        p.exit(2, f"CLAUSE source rebuild failed: {error}\n")


if __name__ == "__main__":
    main()
