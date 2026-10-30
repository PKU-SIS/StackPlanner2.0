"""Pinned source reconstruction and archive/directory compatibility; never run SP."""

import hashlib
import os
import tarfile
from pathlib import Path

import pytest

from dcc_harness.datasets import clause_data, clause_source


ROOT = Path(__file__).resolve().parents[1]


def test_frozen_templates_are_verified_before_execution(tmp_path):
    assert clause_source.verify_templates() == 34
    import shutil

    root = tmp_path / "templates"
    shutil.copytree(clause_source.TEMPLATES, root)
    path = root / "provenance/prepare_server_bundle.py"
    path.write_text(path.read_text() + "\nraise RuntimeError('modified')\n")
    with pytest.raises(ValueError, match="template"):
        clause_source.verify_templates(root)


def test_source_cli_modes_are_explicit_and_mutually_exclusive():
    from scripts.dcc_prepare_clause import parser

    p = parser()
    assert p.parse_args(["--download"]).download
    assert p.parse_args(["--ready-dir", "/ready"]).ready_dir == Path("/ready")
    with pytest.raises(SystemExit):
        p.parse_args([])
    with pytest.raises(SystemExit):
        p.parse_args(["--download", "--archive", "/old.tar.gz"])


def test_unverified_directory_is_rejected(tmp_path):
    (tmp_path / "provenance").mkdir()
    (tmp_path / "provenance/package_checksums.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="checksum manifest"):
        clause_data.verify_directory(tmp_path)


def test_source_archive_is_verified_before_extraction(tmp_path):
    import io

    archive = tmp_path / "wrong.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("repo/README.md")
        info.size = 3
        tar.addfile(info, io.BytesIO(b"bad"))
    with pytest.raises(ValueError, match="Git tree"):
        clause_source.verify_extract_source(archive, tmp_path / "source")
    assert not (tmp_path / "source").exists()


@pytest.mark.parametrize("name", ["repo/../outside", "/absolute", "repo/link"])
def test_unsafe_upstream_archive_is_rejected(tmp_path, name):
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(name)
        if name.endswith("link"):
            info.type = tarfile.SYMTYPE
            info.linkname = "../../outside"
        tar.addfile(info)
    with pytest.raises(ValueError):
        clause_source.verify_extract_source(archive, tmp_path / "source")
    assert not (tmp_path / "source").exists()


def test_rebuild_failure_does_not_publish_partial_dataset(tmp_path, monkeypatch):
    monkeypatch.setattr(clause_source, "verify_extract_source", lambda *a: {})
    monkeypatch.setattr(
        clause_source,
        "scan_intext",
        lambda *a: (_ for _ in ()).throw(ValueError("bad source")),
    )
    with pytest.raises(ValueError, match="bad source"):
        clause_source.rebuild(
            tmp_path / "ready",
            tmp_path / "cache",
            source_archive=tmp_path / "source.tar.gz",
        )
    assert not (tmp_path / "ready").exists()
    assert not (tmp_path / "cache/last_rebuild_verification.json").exists()


def test_existing_output_rejected_before_network(tmp_path, monkeypatch):
    from scripts.dcc_prepare_clause import main_args, parser

    def forbidden(*args, **kwargs):
        pytest.fail("existing outputs must be rejected before network access")

    monkeypatch.setattr(clause_source, "download", forbidden)
    output = tmp_path / "existing"
    output.mkdir()
    (output / "keep").write_text("existing user data")
    with pytest.raises(FileExistsError):
        main_args(parser().parse_args(["--download", "--output-dir", str(output)]))
    assert (output / "keep").read_text() == "existing user data"


@pytest.fixture(scope="module")
def rebuilt_ready():
    value = os.environ.get("DCC_CLAUSE_REBUILT_READY")
    if not value:
        pytest.skip("set DCC_CLAUSE_REBUILT_READY after fresh upstream reconstruction")
    return Path(value)


def test_rebuilt_files_match_original_archive_byte_for_byte(rebuilt_ready):
    value = os.environ.get("DCC_CLAUSE_TEST_ARCHIVE")
    if not value:
        pytest.skip("set DCC_CLAUSE_TEST_ARCHIVE for original archive comparison")
    from_directory, splits, count = clause_data.verify_directory(rebuilt_ready)
    from_archive, old_splits, old_count = clause_data.verify_bundle(Path(value))
    assert from_directory.keys() == from_archive.keys()
    for name, content in from_directory.items():
        assert (
            hashlib.sha256(content).hexdigest()
            == hashlib.sha256(from_archive[name]).hexdigest()
        ), name
    assert splits == old_splits and count == old_count == 10092


def test_directory_verifier_rejects_missing_extra_tampered_and_symlinked_files(
    rebuilt_ready, tmp_path
):
    # Reuse a verified tree without allocating another full corpus on the test filesystem.
    import shutil

    root = tmp_path / "ready"
    shutil.copytree(rebuilt_ready, root, copy_function=os.link)
    target = root / "prompts/l1_system.txt"
    original = target.read_bytes()
    target.unlink()  # Never write through a hardlink into the reference tree.
    with pytest.raises(ValueError, match="missing"):
        clause_data.verify_directory(root)
    target.write_bytes(original + b"changed")
    with pytest.raises(ValueError, match="checksum"):
        clause_data.verify_directory(root)
    target.unlink()
    target.symlink_to(rebuilt_ready / "prompts/l1_system.txt")
    with pytest.raises(ValueError, match="symlink"):
        clause_data.verify_directory(root)
    target.unlink()
    target.write_bytes(original)
    (root / "unexpected.txt").write_text("extra")
    with pytest.raises(ValueError, match="Unexpected"):
        clause_data.verify_directory(root)


def test_rebuilt_directory_converts_to_identical_public_private_inputs(
    rebuilt_ready, tmp_path
):
    from dcc_harness.data import load_tasks
    from dcc_harness.datasets.clause import validate_dataset
    from scripts.dcc_prepare_clause import main_args, parser

    output = tmp_path / "converted"
    report = main_args(
        parser().parse_args(
            ["--ready-dir", str(rebuilt_ready), "--output-dir", str(output)]
        )
    )
    assert (
        report["checked_files"] == 10092
        and report["source_mode"] == "verified_directory"
    )
    original = Path(
        os.environ.get("DCC_CLAUSE_TEST_DATA", ROOT / ".benchmark-data/clause")
    )
    for split, size in [("train", 4025), ("validation", 501), ("test", 500)]:
        public = load_tasks(output / f"public/{split}.jsonl")
        private = load_tasks(output / f"private/{split}.jsonl")
        validate_dataset(public)
        validate_dataset(private)
        assert len(public) == len(private) == size
        for a, b in zip(public, private, strict=True):
            assert a.gold is None and not a.metadata
            assert a.input_sha256() == b.input_sha256()
        for side in ("public", "private"):
            # Paths are relative, so byte equality also establishes exactly the same
            # IDs, splits, groups, prompts, annotations and evaluation metadata.
            assert (output / f"{side}/{split}.jsonl").read_bytes() == (
                original / f"{side}/{split}.jsonl"
            ).read_bytes()
    old = original / "public/documents"
    for path in (output / "public/documents").iterdir():
        assert path.read_bytes() == (old / path.name).read_bytes()


def test_release_directory_symlink_rejected(tmp_path):
    link = tmp_path / "alias"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        clause_data.verify_directory(link)


def test_archive_and_directory_cannot_be_supplied_together(tmp_path):
    with pytest.raises(ValueError, match="exactly one"):
        clause_data.prepare(
            tmp_path / "old.tar.gz", tmp_path / "new", ready_dir=tmp_path
        )


def test_historical_upstream_code_and_embedded_credentials_are_not_vendored():
    import re

    assert not (clause_source.TEMPLATES / "provenance/upstream_code").exists()
    pattern = re.compile(rb"sk-(?:svcacct|proj)-[A-Za-z0-9_-]{20,}")
    for path in clause_source.TEMPLATES.rglob("*"):
        if path.is_file():
            assert not pattern.search(path.read_bytes()), str(path)


def test_original_source_and_existing_nltk_work_without_any_network(
    tmp_path, monkeypatch
):
    from scripts.dcc_prepare_clause import main_args, parser

    archive = os.environ.get("DCC_CLAUSE_SOURCE_ARCHIVE")
    if not archive:
        pytest.skip(
            "set DCC_CLAUSE_SOURCE_ARCHIVE for fully offline source reconstruction"
        )
    original = Path(
        os.environ.get("DCC_CLAUSE_TEST_DATA", ROOT / ".benchmark-data/clause")
    )

    def forbidden(*args, **kwargs):
        pytest.fail("offline reconstruction must never download resources")

    monkeypatch.setattr(clause_source, "download", forbidden)
    output = tmp_path / "offline-converted"
    args = parser().parse_args(
        [
            "--source-archive",
            archive,
            "--resources-from",
            str(original / "resources"),
            "--work-dir",
            str(tmp_path / "offline-cache"),
            "--output-dir",
            str(output),
        ]
    )
    report = main_args(args)
    assert report["source_rebuild"]["status"] == "PASS"
    assert report["source_rebuild"]["model_or_judge_called"] is False
    for split in ("train", "validation", "test"):
        for side in ("public", "private"):
            assert (output / side / f"{split}.jsonl").read_bytes() == (
                original / side / f"{split}.jsonl"
            ).read_bytes()
