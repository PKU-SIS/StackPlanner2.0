"""Locate native SP sources in the training checkout or the SP repository."""

from pathlib import Path


def native_harness_path(root: Path) -> Path:
    root = Path(root).resolve()
    for base in (root, root / "StackPlanner2.0"):
        candidate = base / "backend/packages/harness"
        if (candidate / "deerflow").is_dir():
            return candidate
    raise FileNotFoundError(f"Native SP harness not found below {root}")
