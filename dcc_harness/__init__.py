"""Shared dataset adapters for native SP evaluation and external training."""

from .data import Task, load_tasks, validate_disjoint

__all__ = ["Task", "load_tasks", "validate_disjoint"]
