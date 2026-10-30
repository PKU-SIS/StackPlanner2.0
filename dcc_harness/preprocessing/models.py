"""The loader contract: evidence documents, rule files, and private labels."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any


@dataclasses.dataclass(frozen=True)
class Asset:
    id: str
    text: str | None = None
    path: Path | None = None
    title: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("asset requires an id")
        if (self.text is None) == (self.path is None):
            raise ValueError("asset requires exactly one of text or path")
        if self.text is not None and not isinstance(self.text, str):
            raise ValueError("asset text must be a string")

    def read_bytes(self) -> bytes:
        return self.text.encode("utf-8") if self.text is not None else Path(self.path).read_bytes()


@dataclasses.dataclass(frozen=True)
class Sample:
    id: str
    dataset: str
    split: str
    question: str
    documents: tuple[Asset, ...]
    rules: tuple[Asset, ...] = ()
    gold: Any = None
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)
    public_metadata: dict[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("sample requires an id")
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", self.dataset):
            raise ValueError("dataset must be a lowercase identifier")
        if self.split not in {"train", "validation", "test"}:
            raise ValueError(f"unsupported split: {self.split}")
        if not isinstance(self.question, str) or not self.question.strip():
            raise ValueError("sample requires a question")
        if not self.documents:
            raise ValueError("sample requires at least one document")
        for assets in (self.documents, self.rules):
            if len({asset.id for asset in assets}) != len(assets):
                raise ValueError("duplicate asset id within its role")
