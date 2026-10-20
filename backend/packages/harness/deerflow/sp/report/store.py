"""Filesystem and identity helpers for StackPlanner report runs.

The report ledger under ``<thread outputs>/report/<report_id>/`` is the single
authoritative business state.  This module owns only deterministic storage:
path resolution, atomic single-file writes, stable identifiers, and the
human-readable material catalog derived from the ledger.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPORT_ROOT = "report"
CURRENT_POINTER = "current.json"
OUTPUTS_VIRTUAL_PREFIX = "/mnt/user-data/outputs"
REPORT_STATUSES = frozenset({"active", "delivered", "abandoned"})
REPORT_CONTEXT_STATUSES = frozenset({"disabled", *REPORT_STATUSES, "error"})
_LOCK_GUARD = threading.Lock()
_REPORT_LOCKS: dict[str, threading.RLock] = {}


class ReportStoreError(RuntimeError):
    """Raised when report storage cannot be resolved or read safely."""


def _lock_for(key: str) -> threading.RLock:
    with _LOCK_GUARD:
        lock = _REPORT_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _REPORT_LOCKS[key] = lock
        return lock


def report_lock(paths: ReportPaths) -> threading.RLock:
    """Process-wide serialization for one report ledger read-modify-write.

    Report tools can run concurrently inside one subagent ToolNode turn.  A
    single atomic file replace is not enough because two writers that loaded
    the same base ledger would overwrite each other's identity allocation.
    The lock is process-local and keyed by the ledger path; distributed writers
    are outside the current single-Gateway deployment boundary.
    """
    return _lock_for(f"ledger:{paths.ledger}")


def report_root_lock(outputs_path: Path | str) -> threading.RLock:
    """Serialize report-id allocation/pointer changes for one outputs root."""
    return _lock_for(f"root:{Path(outputs_path).expanduser()}")


@dataclass(frozen=True, slots=True)
class ReportContext:
    """Resolved report mode for one ThreadState snapshot."""

    status: str
    outputs_path: Path | None = None
    report_id: str | None = None
    report_dir: Path | None = None
    ledger_path: Path | None = None
    error: str | None = None

    @property
    def is_enabled(self) -> bool:
        return self.status in REPORT_STATUSES

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def is_failed(self) -> bool:
        return self.status == "error"


def outputs_path_from_state(state: Mapping[str, Any] | None) -> Path:
    """Return the host outputs directory carried by ThreadState."""
    thread_data = state.get("thread_data") if isinstance(state, Mapping) else None
    raw = thread_data.get("outputs_path") if isinstance(thread_data, Mapping) else None
    if not isinstance(raw, str) or not raw.strip():
        raise ReportStoreError("report storage requires thread_data.outputs_path")
    return Path(raw).expanduser()


def pointer_path(outputs_path: Path) -> Path:
    return outputs_path / REPORT_ROOT / CURRENT_POINTER


def report_dir(outputs_path: Path, report_id: str) -> Path:
    return outputs_path / REPORT_ROOT / report_id


def ledger_path(outputs_path: Path, report_id: str) -> Path:
    return report_dir(outputs_path, report_id) / "ledger.json"


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReportStoreError(f"cannot read JSON file {path}: {exc}") from exc


def write_text_atomic(path: Path, text: str) -> None:
    """Replace one UTF-8 text file atomically inside its own directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def write_json_atomic(path: Path, payload: Any) -> None:
    write_text_atomic(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def line_count(text: str) -> int:
    return text.count("\n") + 1 if text else 0


def next_identifier(prefix: str, existing: list[str]) -> str:
    """Allocate the next ``<prefix><n>`` identifier from persisted values."""
    highest = 0
    for value in existing:
        if not isinstance(value, str) or not value.startswith(prefix):
            continue
        suffix = value[len(prefix) :]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{prefix}{highest + 1}"


def allocate_report_id(outputs_path: Path) -> str:
    root = outputs_path / REPORT_ROOT
    existing = [item.name for item in root.iterdir() if item.is_dir() and item.name.startswith("r")] if root.exists() else []
    return next_identifier("r", existing)


def virtual_path_for(host_path: Path, outputs_path: Path) -> str:
    try:
        relative = host_path.resolve().relative_to(outputs_path.resolve())
    except (OSError, ValueError) as exc:
        raise ReportStoreError(f"report path is outside thread outputs: {host_path}") from exc
    return f"{OUTPUTS_VIRTUAL_PREFIX}/{relative.as_posix()}"


@dataclass(frozen=True, slots=True)
class ReportPaths:
    """Path layout for one report id under a thread's outputs directory."""

    outputs_path: Path
    report_id: str

    @property
    def directory(self) -> Path:
        return report_dir(self.outputs_path, self.report_id)

    @property
    def ledger(self) -> Path:
        return ledger_path(self.outputs_path, self.report_id)

    @property
    def request(self) -> Path:
        return self.directory / "request.md"

    @property
    def inputs(self) -> Path:
        return self.directory / "user_inputs"

    @property
    def materials(self) -> Path:
        return self.directory / "materials"

    @property
    def catalog(self) -> Path:
        return self.materials / "catalog.md"

    @property
    def tree(self) -> Path:
        return self.directory / "tree"

    @property
    def research(self) -> Path:
        return self.directory / "research"

    def input_file(self, input_id: str) -> Path:
        return self.inputs / f"{input_id}.md"

    def material_file(self, source_id: str, content_sha: str) -> Path:
        return self.materials / source_id / f"{content_sha}.txt"

    def tree_file(self, version: int) -> Path:
        return self.tree / f"{version}.json"

    def summary_file(self, action_id: str) -> Path:
        return self.research / action_id / "summary.md"

    def virtual(self, host_path: Path) -> str:
        return virtual_path_for(host_path, self.outputs_path)


def load_report_context(
    state: Mapping[str, Any] | None,
    *,
    outputs_path: Path | str | None = None,
) -> ReportContext:
    """Resolve the current report pointer without hiding technical failures.

    A missing pointer means report mode was never enabled.  A pointer that
    exists but cannot be read (bad JSON, missing ledger, unreadable file) is a
    technical error and must not be treated as "report disabled".
    """
    try:
        root = Path(outputs_path).expanduser() if outputs_path is not None else outputs_path_from_state(state)
    except ReportStoreError:
        # No thread outputs path means report storage cannot exist in this
        # runtime snapshot; ordinary non-report behavior is unchanged.
        return ReportContext(status="disabled")

    pointer = pointer_path(root)
    try:
        payload = read_json(pointer)
    except FileNotFoundError:
        return ReportContext(status="disabled", outputs_path=root)
    except ReportStoreError as exc:
        return ReportContext(status="error", outputs_path=root, error=str(exc))

    report_id = payload.get("report_id") if isinstance(payload, Mapping) else None
    if not isinstance(report_id, str) or not report_id.strip():
        return ReportContext(status="error", outputs_path=root, error=f"invalid report pointer: {pointer}")
    report_id = report_id.strip()
    ledger = ledger_path(root, report_id)
    try:
        ledger_payload = read_json(ledger)
    except FileNotFoundError:
        return ReportContext(status="error", outputs_path=root, report_id=report_id, error=f"active report pointer has no ledger: {ledger}")
    except ReportStoreError as exc:
        return ReportContext(status="error", outputs_path=root, report_id=report_id, error=str(exc))
    if not isinstance(ledger_payload, Mapping):
        return ReportContext(status="error", outputs_path=root, report_id=report_id, error=f"report ledger is not an object: {ledger}")
    raw_status = str(ledger_payload.get("status") or "").strip().lower()
    status = raw_status if raw_status in REPORT_STATUSES else "error"
    return ReportContext(
        status=status,
        outputs_path=root,
        report_id=report_id,
        report_dir=report_dir(root, report_id),
        ledger_path=ledger,
        error=None if status != "error" else f"report ledger has invalid status: {raw_status!r}",
    )


def catalog_markdown(ledger: Mapping[str, Any]) -> str:
    """Render the ledger's sources/materials into a deterministic catalog."""
    sources = ledger.get("sources") if isinstance(ledger.get("sources"), list) else []
    materials = ledger.get("materials") if isinstance(ledger.get("materials"), list) else []
    lines = ["# Report materials", ""]
    if not materials:
        lines.append("_No saved materials yet._")
        return "\n".join(lines) + "\n"

    lines.append(f"Saved sources: {len(sources)}; material versions: {len(materials)}. Paths are relative to the thread outputs directory.")
    for item in materials:
        if not isinstance(item, Mapping):
            continue
        material_id = str(item.get("material_id") or "")
        source_id = str(item.get("source_id") or "")
        lines.extend(
            [
                "",
                f"## {material_id} ({source_id})",
                f"- path: {item.get('virtual_path')}",
                f"- url: {item.get('url')}",
                f"- title: {item.get('title') or ''}",
                f"- chars/lines: {item.get('chars')}/{item.get('lines')}",
                f"- fetched_at: {item.get('fetched_at')}",
                f"- content_scope: {item.get('content_scope')}",
                f"- upstream_truncation: {item.get('upstream_truncation')}",
                f"- sha256: {item.get('sha256')}",
                f"- preview: {' '.join(str(item.get('preview') or '').split())[:300]}",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def write_catalog(paths: ReportPaths, ledger: Mapping[str, Any]) -> None:
    write_text_atomic(paths.catalog, catalog_markdown(ledger))


def append_user_input_file(paths: ReportPaths, input_id: str, text: str) -> None:
    write_text_atomic(paths.input_file(input_id), text.rstrip() + "\n")


def write_request(paths: ReportPaths, text: str) -> None:
    write_text_atomic(paths.request, text.rstrip() + "\n")


def write_tree_version(paths: ReportPaths, version: int, tree: Mapping[str, Any]) -> Path:
    path = paths.tree_file(version)
    write_json_atomic(path, tree)
    return path


def write_research_summary(paths: ReportPaths, action_id: str, text: str) -> Path:
    path = paths.summary_file(action_id)
    write_text_atomic(path, text.rstrip() + "\n")
    return path
