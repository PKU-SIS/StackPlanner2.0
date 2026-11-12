from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import traceback
import uuid
import time

from . import __version__
from .core import Context, dump, contained, file_hash, groups, outcome
from .registry import NAMES, TOOLS


def _externalize(data):
    evidence = []
    lookup = {}

    def ref(item):
        key = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if key not in lookup:
            lookup[key] = f"E{len(evidence) + 1:06d}"
            evidence.append({"evidence_id": lookup[key], **item})
        return lookup[key]

    def walk(value):
        if isinstance(value, list):
            return [walk(v) for v in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for k, v in value.items():
            if k in ("evidence", "opening_evidence"):
                vv = v if isinstance(v, list) else [v]
                result[k + "_refs"] = [ref(x) for x in vv]
            else:
                result[k] = walk(v)
        return result

    return walk(data), evidence


def code_fingerprint():
    h = hashlib.sha256()
    for p in sorted(Path(__file__).parent.glob("*.py")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def run_rule(
    rule_id,
    manifest_path,
    output_root,
    *,
    permitted_root=None,
    cache_dir=None,
    parsed_texts=None,
):
    if rule_id not in TOOLS:
        raise ValueError("Unknown rule id")
    start = time.monotonic()
    output_root = Path(output_root).resolve()
    run_id = (
        rule_id
        + "_"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        + "_"
        + uuid.uuid4().hex[:10]
    )
    out = output_root / run_id
    out.mkdir(parents=True, exist_ok=False)
    base = {
        "schema_version": "1.0",
        "tool_version": __version__,
        "code_sha256": code_fingerprint(),
        "rule_id": rule_id,
        "rule_name": NAMES[rule_id],
        "run_id": run_id,
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "risk_verdict": None,
        "requires_agent_review": True,
        "input_text_is_untrusted_data": True,
    }
    try:
        ctx = Context(
            manifest_path,
            cache_dir or output_root / ".cache",
            permitted_root=permitted_root,
        )
        if int(rule_id[1:]) <= 12 and not any(
            len({d["bidder"] for d in ds}) >= 2
            for ds in groups(ctx.documents("bid")).values()
        ):
            data = outcome(
                status="insufficient_data",
                limitations=[
                    "At least two bidders in the same round and lot are required."
                ],
            )
        elif rule_id in ("M06", "M10", "M11", "M12") and not any(
            len({d["bidder"] for d in ds}) >= 2
            for ds in groups(
                [d for d in ctx.documents("bid") if d["_path"].suffix.lower() == ".pdf"]
            ).values()
        ):
            data = outcome(
                status="insufficient_data",
                limitations=[
                    "This rule requires original PDFs from at least two bidders in the same round and lot."
                ],
            )
        elif rule_id == "M01":
            from .m01_recall import audit_m01

            data = audit_m01(ctx, parsed_texts)
        elif parsed_texts is not None:
            from .structured_text import attach
            from .enhanced_rules import run as run_enhanced

            attach(ctx, parsed_texts)
            data = run_enhanced(ctx, rule_id)
        else:
            data = TOOLS[rule_id](ctx)
        warnings = sorted(set(ctx.warnings))
        if warnings and data["status"] == "ok":
            data["status"] = "partial"
        # Verify source bytes did not change while the audit was running.
        for d in ctx.docs:
            if file_hash(d["_path"]) != d["sha256"]:
                raise ValueError("Source changed during execution")
        data, evidence = _externalize(data)
        collections = {"findings": data.pop("findings"), "evidence": evidence}
        for key in list(data):
            if isinstance(data[key], list) and key not in ("limitations",):
                collections[key] = data.pop(key)
        for name, items in collections.items():
            with (out / (name + ".jsonl")).open("w", encoding="utf-8") as f:
                for item in items:
                    f.write(
                        json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n"
                    )
        dump(out / "sources.json", ctx.source_inventory())
        dump(out / "manifest.snapshot.json", ctx.manifest)
        summary = {
            **base,
            **data,
            "warnings": warnings,
            "manifest_sha256": file_hash(manifest_path),
            "parameters": ctx.params,
            "collections": {
                k: {"count": len(v), "file": k + ".jsonl"}
                for k, v in collections.items()
            },
            "evidence_count": len(evidence),
            "finding_count": len(collections["findings"]),
            "elapsed_seconds": round(time.monotonic() - start, 3),
            "files": {"sources": "sources.json", "manifest": "manifest.snapshot.json"},
        }
        # Imported versions are part of the audit environment, without imposing network access.
        from importlib.metadata import version

        summary["dependencies"] = {
            k: version(k) for k in ("pypdf", "pdfplumber", "Pillow", "python-docx")
        }
    except Exception as e:
        summary = {
            **base,
            "status": "error",
            "error": {"type": type(e).__name__, "message": str(e)},
            "finding_count": None,
            "evidence_count": None,
            "collections": {},
            "elapsed_seconds": round(time.monotonic() - start, 3),
            "limitations": [
                "Execution failed. Do not interpret this result as zero anomalies."
            ],
        }
        (out / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
    dump(out / "result.json", summary)
    return {**summary, "result_directory": str(out)}


def read_result(run_directory, collection="findings", offset=0, limit=5):
    root = Path(run_directory).resolve()
    summary = json.loads((root / "result.json").read_text())
    if (
        type(offset) is not int
        or offset < 0
        or type(limit) is not int
        or not 1 <= limit <= 20
    ):
        raise ValueError("offset >= 0 and 1 <= limit <= 20 required")
    if collection not in summary["collections"]:
        raise ValueError("Unknown result collection")
    spec = summary["collections"][collection]
    path = contained(root / spec["file"], root)
    items = []
    with path.open(encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i < offset:
                continue
            if len(items) >= limit:
                break
            items.append(json.loads(line))
    return {
        "run_id": summary["run_id"],
        "collection": collection,
        "offset": offset,
        "total": spec["count"],
        "items": items,
        "next_offset": offset + len(items)
        if offset + len(items) < spec["count"]
        else None,
    }
