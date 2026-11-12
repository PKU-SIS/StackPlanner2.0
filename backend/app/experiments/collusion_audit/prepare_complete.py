"""Add original tender ZIP members without admitting any report archive."""

import hashlib
import zipfile
from pathlib import Path, PurePosixPath

from .data import safe_document, sha256


def add_tender_archives(source, destination):
    existing = {sha256(p) for p in destination.rglob("*") if p.is_file() and safe_document(p.relative_to(destination))}
    added, duplicates = [], []
    for archive in sorted(source.rglob("*.zip")):
        if "招标文件" not in archive.name:
            continue
        if any(part in {"分析报告", "分析结果"} or part.startswith("历史分析") for part in archive.relative_to(source).parts):
            continue
        archive_hash = sha256(archive)
        with zipfile.ZipFile(archive) as handle:
            for member in handle.infolist():
                name = PurePosixPath(member.filename)
                if member.is_dir() or name.is_absolute() or ".." in name.parts or "__MACOSX" in name.parts:
                    continue
                if "招标文件" not in name.name or not safe_document(Path(member.filename)):
                    continue
                data = handle.read(member)
                digest = hashlib.sha256(data).hexdigest()
                if digest in existing:
                    duplicates.append({"archive": str(archive.relative_to(source)), "member": member.filename, "sha256": digest})
                    continue
                round_name = "第二次开标" if "第二次开标" in str(archive.relative_to(source)) else "第一次开标"
                relative = Path(round_name) / "招标文件_原始压缩包" / name.name
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(data)
                existing.add(digest)
                added.append({"path": str(relative), "source_archive": str(archive.relative_to(source)), "source_archive_sha256": archive_hash, "member": member.filename, "sha256": digest})
    return added, duplicates


def main():
    from .prepare import main as prepare_main

    prepare_main()


if __name__ == "__main__":
    main()
