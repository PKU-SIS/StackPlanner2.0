from __future__ import annotations

import io
import json
import shutil
import tarfile
from pathlib import Path

import pytest

from dcc_harness.data import load_tasks
from dcc_harness.preprocessing.pipeline import digest, prepare, read_jsonl, render
from dcc_harness.preprocessing.ragflow import index_bundle
from dcc_harness.preprocessing.releases import (
    audit_finauditing,
    build_finauditing_release,
    download_release,
    install_release,
    verify_release,
)
from scripts.dcc_preprocess import load_config, main


@pytest.fixture
def release_inputs(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from test_dcc_preprocessing import FakeRagflow

    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "configs/dcc/preprocessing.finauditing.example.json")
    source = tmp_path / "source"
    source.mkdir()
    provenance = {"license": "CC-BY-4.0", "datasets": []}
    for task in ("FinRE", "FinMR", "FinSM"):
        instructions = (root / f"configs/dcc/prompts/finauditing/{task.lower()}.md").read_text().strip()
        headings = (
            "Schema document",
            "Presentation linkbase document",
            "Calculation linkbase document",
            "Definition linkbase document",
            "Label linkbase document",
            "Instance document",
            "US GAAP Taxonomy",
        )
        bodies = ("None", "None", "None", "None", "None", "<fact unitRef='USD'>1,000</fact>", "PUBLIC TAXONOMY")
        query = (
            instructions + "\n" + "".join(f"##{heading}\n{body}\n" for heading, body in zip(headings, bodies, strict=True)) + "Question: Check A?\nAnswer:\n"
        )
        path = source / task / "test.parquet"
        path.parent.mkdir()
        pq.write_table(pa.Table.from_pylist([{"id": 0, "query": json.dumps(query), "answer": json.dumps("PRIVATE_LABEL")}]), path)
        (path.parent / "README.md").write_text("Original dataset card, CC BY 4.0")
        provenance["datasets"].append(
            {"task": task, "file": f"{task}/test.parquet", "sha256": digest(path.read_bytes()), "revision": "pinned-commit", "rows": 1}
        )
    (source / "manifest.json").write_text(json.dumps(provenance))
    cfg["loader_options"]["source"] = str(source)
    cfg["ragflow"] = {"base_url": "https://rag.example", "poll_interval": 0, "parse_timeout": 3, "dataset_prefix": "release-test"}
    bundle = tmp_path / "bundle"
    prepare(cfg, bundle)
    index_bundle(bundle, cfg["ragflow"], client=FakeRagflow())
    render(bundle, cfg)
    return source, bundle, cfg


def pack(inputs, output):
    source, bundle, cfg = inputs
    return build_finauditing_release(bundle, source, cfg, output, version="finauditing-test")


def test_pack_install_relocates_all_inputs_and_preserves_remote_scopes(tmp_path, release_inputs):
    source, bundle, cfg = release_inputs
    cfg["ragflow"]["api_key_file"] = "/PRIVATE_KEY_FILE"
    cfg["ragflow"]["api_key"] = "PRIVATE_KEY_VALUE"
    archive = tmp_path / "release.tar.gz"
    result = pack(release_inputs, archive)
    output = tmp_path / "another server 数据"
    installed = install_release(archive, output, sha256=result["sha256"])
    assert installed["samples"] == 3
    assert verify_release(output)["processing_audit"]["original_task_instructions_inline"]
    relocated = load_config(output / "config.json")
    assert Path(relocated["loader_options"]["source"]) == output / "raw/FinAuditing"
    assert "PRIVATE_KEY" not in (output / "config.json").read_text()
    assert (output / "bundle/index.json").read_bytes() == (bundle / "index.json").read_bytes()
    assert (output / "raw/FinAuditing/FinRE/test.parquet").read_bytes() == (source / "FinRE/test.parquet").read_bytes()
    tasks = load_tasks(output / "bundle/public/tasks.jsonl")
    assert len(tasks) == 3 and all(task.files for task in tasks)
    assert all("PRIVATE_LABEL" not in task.prompt for task in tasks)
    assert all(row["metadata"]["source_file"].startswith("raw/FinAuditing/") for row in read_jsonl(output / "bundle/private/gold.jsonl"))
    assert len(render(output / "bundle", relocated)) == 3
    assert (output / "bundle/private").stat().st_mode & 0o777 == 0o700
    with pytest.raises(FileExistsError):
        install_release(archive, output, sha256=result["sha256"])


def test_release_hash_and_file_inventory_are_verified(tmp_path, release_inputs):
    archive = tmp_path / "release.tar.gz"
    result = pack(release_inputs, archive)
    output = tmp_path / "installed"
    with pytest.raises(ValueError, match="SHA-256"):
        install_release(archive, output, sha256="0" * 64)
    assert not output.exists()
    install_release(archive, output, sha256=result["sha256"])
    (output / "raw/FinAuditing/FinRE/test.parquet").write_bytes(b"modified")
    with pytest.raises(ValueError, match="checksum"):
        verify_release(output)


@pytest.mark.parametrize("kind", ["traversal", "symlink", "duplicate"])
def test_archive_rejects_unsafe_or_duplicate_members_atomically(tmp_path, kind):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("../escaped" if kind == "traversal" else "file")
        if kind == "symlink":
            info.type, info.linkname = tarfile.SYMTYPE, "/outside"
            tar.addfile(info)
        else:
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
            if kind == "duplicate":
                tar.addfile(info, io.BytesIO(b"y"))
    output = tmp_path / "installed"
    with pytest.raises(ValueError, match="invalid or duplicate"):
        install_release(archive, output, sha256=digest(archive.read_bytes()))
    assert not output.exists() and not (tmp_path / "escaped").exists()


def test_github_download_and_manual_cli_install(tmp_path, release_inputs, monkeypatch, capsys):
    archive = tmp_path / "release.tar.gz"
    result = pack(release_inputs, archive)
    specification = {**result, "repository": "PKU-SIS/StackPlanner2.0", "tag": "finauditing-test"}
    monkeypatch.setattr(shutil, "which", lambda name: "/bin/gh")

    def run(command, **kwargs):
        assert command[:4] == ["gh", "release", "download", "finauditing-test"]
        shutil.copyfile(archive, Path(command[-1]) / archive.name)

    monkeypatch.setattr("subprocess.run", run)
    assert download_release(specification, tmp_path / "downloaded")["samples"] == 3
    descriptor = tmp_path / "release.json"
    descriptor.write_text(json.dumps(specification))
    assert main(["download", "--release", str(descriptor), "--archive", str(archive), "--output", str(tmp_path / "manual")]) == 0
    assert json.loads(capsys.readouterr().out)["samples"] == 3


def test_baseline_release_refuses_processors_changed_text_and_incomplete_index(tmp_path, release_inputs):
    source, bundle, cfg = release_inputs
    cfg["processors"] = [{"use": "example:optimize"}]
    with pytest.raises(ValueError, match="no processors"):
        pack(release_inputs, tmp_path / "not-created.tar.gz")
    cfg["processors"] = []
    state_path = bundle / "index.json"
    state = json.loads(state_path.read_text())
    state["ready"] = False
    state_path.write_text(json.dumps(state))
    with pytest.raises(RuntimeError, match="not ready"):
        pack(release_inputs, tmp_path / "not-created.tar.gz")
    assert not (tmp_path / "not-created.tar.gz").exists()
    rows = read_jsonl(bundle / "public/samples.jsonl")
    rows[0]["question"] = "An optimized question"
    data = "".join(json.dumps(row) + "\n" for row in rows).encode()
    (bundle / "public/samples.jsonl").write_bytes(data)
    manifest = json.loads((bundle / "manifest.json").read_text())
    manifest["public_sha256"] = digest(data)
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="was changed"):
        audit_finauditing(bundle, source)
