"""Operator-selected paths and server-side RAGFlow credentials."""

import os
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[4]


def ragflow_settings(env_file=None):
    path = Path(env_file or os.getenv("COLLUSION_RAGFLOW_ENV_FILE") or ROOT / ".deer-flow/collusion-ragflow.env")
    values = {**dotenv_values(path), **{key: value for key, value in os.environ.items() if key.startswith("RAGFLOW_")}}
    base = str(values.get("RAGFLOW_API_URL") or "").rstrip("/")
    key = values.get("RAGFLOW_API_KEY")
    if not base.startswith(("http://", "https://")) or not key:
        raise ValueError("Set RAGFLOW_API_URL and RAGFLOW_API_KEY in the environment or the selected credentials file")
    return {"RAGFLOW_API_URL": base, "RAGFLOW_API_KEY": key}
