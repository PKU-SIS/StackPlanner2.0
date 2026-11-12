"""Immutable calculation-fact cache, excluding all model judgements/reports."""

import fcntl
import hashlib
import json
import shutil
from importlib.metadata import version
from pathlib import Path

from .knowledge import atomic, fingerprint


def calculate_cached(rule_id, project, outputs, parse_cache, cache_root, *, parsed_texts=None):
    from bid_audit.runner import code_fingerprint, run_rule

    project, outputs, cache_root = Path(project), Path(outputs), Path(cache_root)
    manifest = json.loads((project / "manifest.json").read_text())
    for d in manifest["documents"]:
        if hashlib.sha256((project / d["path"]).read_bytes()).hexdigest() != d["expected_sha256"]:
            raise ValueError("Source changed before cache lookup")
    key = fingerprint(
        {
            "manifest": manifest,
            "rule": rule_id,
            "code": code_fingerprint(),
            "parsed_texts": fingerprint(parsed_texts) if parsed_texts is not None else None,
            "dependencies": {n: version(n) for n in ["pypdf", "pdfplumber", "Pillow", "python-docx"]},
        }
    )
    folder = cache_root / key
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        summary = folder / "cached_result.json"
        if summary.exists():
            result = json.loads(summary.read_text())
            target = outputs / "bid_audit" / result["run_id"]
            integrity = json.loads((folder / "integrity.json").read_text())
            if any(hashlib.sha256((folder / "data" / n).read_bytes()).hexdigest() != sha for n, sha in integrity.items()):
                raise ValueError("Calculation cache changed")
            if not target.exists():
                shutil.copytree(folder / "data", target)
            hit = True
        else:
            result = run_rule(rule_id, project / "manifest.json", outputs / "bid_audit", permitted_root=project, cache_dir=parse_cache, parsed_texts=parsed_texts)
            hit = False
            if result.get("status") != "error":
                shutil.copytree(outputs / "bid_audit" / result["run_id"], folder / "data")
                atomic(folder / "integrity.json", {str(p.relative_to(folder / "data")): hashlib.sha256(p.read_bytes()).hexdigest() for p in (folder / "data").rglob("*") if p.is_file()})
                atomic(summary, result)
        result["cache"] = {"hit": hit, "key": key, "original_calculation_run_id": result["run_id"], "model_analysis_reused": False}
        return result
