"""Validated benchmark tasks; labels are deliberately separate from policy input."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any


@dataclasses.dataclass(frozen=True, slots=True)
class InputFile:
    """Public UTF-8 file; its neutral basename is visible to the agent."""

    name: str
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.name
        ):
            raise ValueError("input file needs a plain public basename")
        if not isinstance(self.text, str):
            raise ValueError("input files currently support UTF-8 text only")


@dataclasses.dataclass(frozen=True, slots=True)
class PublicTask:
    """The ONLY task type given to prompt/input builders and answer parsers.

    No sample ID, split, group, labels, source paths, or private metadata.
    Public metadata is explicitly opted in by the dataset author.
    """

    benchmark: str
    prompt: str
    document: str = ""
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)
    files: tuple[InputFile, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class Task:
    id: str
    benchmark: str
    split: str
    prompt: str
    document: str = ""
    gold: Any = None
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)

    public_metadata: dict[str, Any] = dataclasses.field(default_factory=dict)
    files: tuple[InputFile, ...] = ()
    group_id: str | None = None

    def public_input(self) -> PublicTask:
        public = copy.deepcopy(self.public_metadata)
        # Narrow compatibility migration for existing WideSearch task manifests.
        # Never pass the evaluation criteria, provenance, or arbitrary metadata.
        if self.benchmark == "widesearch" and "answer_columns" not in public:
            columns = self.metadata.get("answer_columns")
            if columns is None:
                columns = self.metadata.get("evaluation", {}).get("required", [])
            public["answer_columns"] = copy.deepcopy(columns)
        return PublicTask(
            self.benchmark, self.prompt, self.document, public, tuple(self.files)
        )

    def input_sha256(self) -> str:
        payload = json.dumps(
            dataclasses.asdict(self.public_input()),
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def __post_init__(self) -> None:
        if not isinstance(self.benchmark, str) or not re.fullmatch(
            r"[a-z][a-z0-9_-]*", self.benchmark
        ):
            raise ValueError(f"invalid benchmark identifier: {self.benchmark}")
        if self.split not in {"train", "validation", "test"}:
            raise ValueError(f"unsupported split: {self.split}")
        if (
            not isinstance(self.id, str)
            or not self.id
            or not isinstance(self.prompt, str)
            or not self.prompt.strip()
        ):
            raise ValueError("task requires a nonempty id and prompt")
        if not isinstance(self.metadata, dict) or not isinstance(
            self.public_metadata, dict
        ):
            raise ValueError("task metadata must be objects")
        if not isinstance(self.document, str):
            raise ValueError("document must be text")
        if any(not isinstance(item, InputFile) for item in self.files):
            raise ValueError("files must contain InputFile values")
        if len({item.name for item in self.files}) != len(self.files):
            raise ValueError("duplicate public input filename")
        if self.group_id is not None and (
            not isinstance(self.group_id, str) or not self.group_id
        ):
            raise ValueError("group_id must be a nonempty string")
        if self.benchmark == "cuad" and not self.document:
            raise ValueError("CUAD task requires the source contract")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Task:
        payload = {
            field.name: value[field.name]
            for field in dataclasses.fields(cls)
            if field.name in value
        }
        payload["files"] = tuple(InputFile(**item) for item in payload.get("files", ()))
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def load_tasks(path: str | Path, *, split: str | None = None) -> list[Task]:
    tasks: list[Task] = []
    seen: set[str] = set()
    documents: dict[tuple[Path, bool], str | dict[str, str]] = {}
    path = Path(path)
    with Path(path).open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("task row must be an object")
                reference = value.get("metadata", {}).get("document_file")
                if not value.get("document") and reference:
                    document_path = (path.parent / reference).resolve()
                    cache_key = (
                        document_path,
                        bool(value.get("metadata", {}).get("document_key")),
                    )
                    if cache_key not in documents:
                        content = document_path.read_bytes().decode("utf-8")
                        documents[cache_key] = (
                            json.loads(content)
                            if value.get("metadata", {}).get("document_key")
                            else content
                        )
                    document = documents[cache_key]
                    document_key = value.get("metadata", {}).get("document_key")
                    value["document"] = (
                        document[document_key] if document_key else document
                    )
                    if not isinstance(value["document"], str):
                        raise ValueError("document reference must resolve to text")
                files = []
                for item in value.get("files", []):
                    if set(item) == {"name", "path"}:
                        # Read bytes to preserve CRLF; source paths never enter PublicTask.
                        text = (path.parent / item["path"]).read_bytes().decode("utf-8")
                        files.append({"name": item["name"], "text": text})
                    elif set(item) == {"name", "text"}:
                        files.append(item)
                    else:
                        raise ValueError(
                            "file requires name and exactly one of path/text"
                        )
                value["files"] = files
                task = Task.from_dict(value)
            except (TypeError, ValueError, KeyError) as exc:
                raise ValueError(f"invalid task at {path}:{number}: {exc}") from exc
            if task.id in seen:
                raise ValueError(f"duplicate task id: {task.id}")
            seen.add(task.id)
            if split is None or task.split == split:
                tasks.append(task)
    return tasks


def validate_disjoint(*collections: Iterable[Task]) -> None:
    """Reject ID, base-group, and exact document leakage for every benchmark."""

    ids: dict[tuple[str, str], str] = {}
    documents: dict[tuple[str, str], str] = {}
    groups: dict[tuple[str, str], str] = {}
    for collection in collections:
        for task in collection:
            key = (task.benchmark, task.id)
            if key in ids and ids[key] != task.split:
                raise ValueError(
                    f"task {task.id} appears in {ids[key]} and {task.split}"
                )
            ids[key] = task.split
            if task.group_id:
                group = (task.benchmark, task.group_id)
                if group in groups and groups[group] != task.split:
                    raise ValueError(f"base group leaks across splits: {task.group_id}")
                groups[group] = task.split
            if task.document:
                # Exact source text also catches renamed contract IDs.
                key_text = (
                    task.benchmark,
                    hashlib.sha256(task.document.encode()).hexdigest(),
                )
                if key_text in documents and documents[key_text] != task.split:
                    raise ValueError(f"document leaks across splits: {task.id}")
                documents[key_text] = task.split

            if task.files:
                key_files = (
                    task.benchmark,
                    hashlib.sha256(
                        json.dumps(
                            sorted(item.text for item in task.files), ensure_ascii=False
                        ).encode()
                    ).hexdigest(),
                )
                if key_files in documents and documents[key_files] != task.split:
                    raise ValueError(
                        f"input file bundle leaks across splits: {task.id}"
                    )
                documents[key_files] = task.split
