"""Fail-loud repository-root resolution shared by the detectors.

Depth-indexed resolution (`Path(__file__).resolve().parents[N]`) fails
silently when a detector file moves to a different directory depth: scan
roots resolve under the wrong directory, nothing is scanned, and the
detector reports zero findings with no error. Walking upward to a
repository marker turns that into an immediate error instead.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT_MARKER = ".git"


def _is_git_marker(marker: Path) -> bool:
    """Reject empty placeholder directories while preserving worktree files."""

    if marker.is_file():
        try:
            return marker.read_text(encoding="utf-8", errors="replace").lstrip().startswith("gitdir:")
        except OSError:
            return False
    return marker.is_dir() and (marker / "HEAD").is_file()


def resolve_repo_root(start: Path) -> Path:
    """Return the repository root above `start` (the directory containing `.git`).

    `.git` is checked with `exists()` rather than `is_dir()` so git worktrees
    (where `.git` is a file) resolve correctly.

    Raises:
        RuntimeError: when no marker is found above `start`, so a relocated
            detector fails loudly instead of silently scanning an empty tree.
    """
    resolved = start.resolve()
    for candidate in (resolved, *resolved.parents):
        if _is_git_marker(candidate / REPO_ROOT_MARKER):
            return candidate
    raise RuntimeError(f"could not resolve the repository root: no '{REPO_ROOT_MARKER}' marker found above {resolved}; refusing to guess scan paths")
