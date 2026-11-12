"""Stage operator-selected originals and optionally bind a published project manifest."""

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from .data import build_manifest, read_rules, safe_document, stage_project

ROOT = Path(__file__).resolve().parents[4]


def prepare(source, output, *, rules_workbook=None, shared_mapping=None, include_archives=False, project_id=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_dir():
        raise ValueError("Source project directory does not exist")
    if output.exists() or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("Choose a new output directory separate from the source")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".collusion-prepare-", dir=output.parent))
    try:
        project = stage / "project_complete"
        manifest = stage_project(source, project)
        if include_archives:
            from .prepare_complete import add_tender_archives

            add_tender_archives(source, project)
            manifest = build_manifest(project, [p for p in project.rglob("*") if p.is_file() and safe_document(p.relative_to(project))])
        manifest.update(project_id=project_id or source.name, project_name=source.name)
        if shared_mapping:
            reference = json.loads(Path(shared_mapping).read_text())["project_manifest"]
            actual = {d["path"]: d["expected_sha256"] for d in manifest["documents"]}
            expected = {d["path"]: d["expected_sha256"] for d in reference["documents"]}
            if actual != expected:
                raise ValueError("Local original paths or SHA256 differ from the shared project manifest")
            manifest = reference
        (project / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        rules_path = ROOT / "collusion_audit/rules.json"
        rules = read_rules(rules_workbook) if rules_workbook else json.loads(rules_path.read_text())
        (stage / "rules.json").write_text(json.dumps(rules, ensure_ascii=False, indent=2) + "\n")
        stage.rename(output)
        return manifest
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project-id")
    parser.add_argument("--rules-workbook", type=Path)
    parser.add_argument("--shared-mapping", type=Path)
    parser.add_argument("--include-tender-archives", action="store_true")
    args = parser.parse_args()
    manifest = prepare(args.source, args.output, project_id=args.project_id, rules_workbook=args.rules_workbook, shared_mapping=args.shared_mapping, include_archives=args.include_tender_archives)
    print(json.dumps({"documents": len(manifest["documents"]), "project": str(args.output / "project_complete")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
