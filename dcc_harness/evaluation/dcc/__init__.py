"""Dataset-specific scorers plugged into the existing offline evaluator."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Evaluator:
    """score(sample, private_label, delivery, options, context); aggregate(metrics, options, context)."""

    dataset: str
    version: str
    score: Callable
    aggregate: Callable
