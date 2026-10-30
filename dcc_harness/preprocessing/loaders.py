"""Built-in loaders; custom loaders use ``module:function(options) -> samples``."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from pathlib import Path

from .models import Asset, Sample


def inside(root: Path, reference: str) -> Path:
    path = (root / reference).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"asset path is outside the source/bundle: {reference}")
    return path


def manifest(options: dict) -> Iterator[Sample]:
    """Load normalized JSONL; binary assets remain files for RAGFlow parsing.

    File references are relative to the JSONL file. A loader needing another
    source root can supply its own explicit extension instead of traversal.
    """
    path = Path(options["source"]).resolve()
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                assets = {}
                for role in ("documents", "rules"):
                    assets[role] = tuple(
                        Asset(
                            id=value["id"],
                            text=value.get("text"),
                            path=inside(path.parent, value["path"]) if "path" in value else None,
                            title=value.get("title", value["id"]),
                        )
                        for value in row.get(role, [])
                    )
                yield Sample(
                    id=row["id"],
                    dataset=row.get("dataset", options.get("dataset", "dcc")),
                    split=row.get("split", "test"),
                    question=row["question"],
                    **assets,
                    gold=row.get("gold"),
                    metadata=row.get("metadata", {}),
                    public_metadata=row.get("public_metadata", {}),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid sample at {path}:{number}: {exc}") from exc


FINAUDITING_HEADINGS = (
    "Schema document",
    "Presentation linkbase document",
    "Calculation linkbase document",
    "Definition linkbase document",
    "Label linkbase document",
    "Instance document",
    "US GAAP Taxonomy",
)


def _decode(value):
    # Official query/answer/dqc_id columns contain one JSON-encoding layer.
    # Do not use unicode_escape, which corrupts Unicode and XML backslashes.
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def finauditing(options: dict) -> Iterator[Sample]:
    try:
        import pyarrow.parquet as pq
    except ImportError:
        raise RuntimeError("FinAuditing requires pyarrow; install configs/dcc/preprocessing-requirements.txt") from None

    root = Path(options["source"])
    tasks = options.get("tasks", ["FinRE", "FinMR", "FinSM"])
    if not tasks or len(set(tasks)) != len(tasks) or any(task not in {"FinRE", "FinMR", "FinSM"} for task in tasks):
        raise ValueError("tasks must be a nonempty, unique selection of FinRE, FinMR, FinSM")
    limit = options.get("limit_per_task")
    if limit is not None and (not isinstance(limit, int) or limit < 1):
        raise ValueError("limit_per_task must be a positive integer")
    provenance_path = root / "manifest.json"
    provenance = json.loads(provenance_path.read_text()) if provenance_path.exists() else {}
    for task in tasks:
        path = root / task / "test.parquet"
        source = next((entry for entry in provenance.get("datasets", []) if entry.get("task") == task), {})
        source_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if source.get("sha256") and source["sha256"] != source_sha256:
            raise ValueError(f"{task}: source parquet differs from its download manifest")
        emitted = 0
        for batch in pq.ParquetFile(path).iter_batches(batch_size=32):
            for row in batch.to_pylist():
                if limit is not None and emitted >= limit:
                    break
                query = _decode(row["query"])
                if not isinstance(query, str):
                    raise TypeError(f"{task}/{row['id']}: query must decode to text")
                headings = list(re.finditer(r"^##([^\n]+)\n", query, re.MULTILINE))
                if tuple(match[1].strip() for match in headings) != FINAUDITING_HEADINGS:
                    raise ValueError(f"{task}/{row['id']}: unexpected document headings; adapter update required")
                questions = list(re.finditer(r"^Question(?:1)?:", query, re.MULTILINE))
                if not questions or questions[-1].start() <= headings[-1].end():
                    raise ValueError(f"{task}/{row['id']}: cannot locate the final question")
                question_start = questions[-1].start()
                documents, missing = [], []
                for index, match in enumerate(headings[:-1]):
                    body = query[match.end() : headings[index + 1].start()].strip()
                    if body in {"", "None"}:
                        missing.append(FINAUDITING_HEADINGS[index])
                        continue
                    documents.append(Asset(f"document-{index}", text=body, title=FINAUDITING_HEADINGS[index]))
                rules = [Asset("task-instructions", text=query[: headings[0].start()].strip(), title=f"{task} task instructions")]
                taxonomy = query[headings[-1].end() : question_start].strip()
                if taxonomy not in {"", "None"}:
                    rules.append(Asset("us-gaap-taxonomy", text=taxonomy, title="US GAAP Taxonomy"))
                question = re.sub(r"\nAnswer:\s*$", "", query[question_start:]).strip()
                yield Sample(
                    id=f"finauditing-{task.lower()}-{row['id']}",
                    dataset=f"finauditing_{task.lower()}",
                    split="test",
                    question=question,
                    documents=tuple(documents),
                    rules=tuple(rules),
                    gold={key: _decode(row[key]) for key in ("answer", "gold") if key in row},
                    metadata={
                        "source_file": str(path),
                        "source_sha256": source_sha256,
                        "source_revision": source.get("revision"),
                        "source_repository": source.get("repository"),
                        "source_row_id": row["id"],
                        "dqc_id": _decode(row.get("dqc_id")),
                        "missing_documents": missing,
                    },
                    public_metadata={"subtask": task, "missing_documents": missing},
                )
                emitted += 1
            if limit is not None and emitted >= limit:
                break
